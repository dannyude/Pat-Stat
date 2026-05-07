"""Family domain services.

This module contains the business logic behind family access and invitation flow.
The API layer should stay thin and defer invite lifecycle and family-link
persistence rules to this module.

When debugging family behavior, this is usually the best place to start because
it holds the authorization-sensitive decision points.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.security import hash_password
from src.domains.family.models import FamilyInvite, FamilyPatientLink
from src.domains.patients.models import Admission, ClinicalUpdate, Patient
from src.domains.assignments.models import CareAssignment
from src.domains.users.services import get_user_by_email
from src.domains.users.enums import UserRole
from src.domains.users.models import User


async def assert_family_link_or_404(
    db: AsyncSession,
    family_user_id: str,
    patient_id: str,
) -> None:
    """Raise 404 unless the family user has an active link to the patient.

    Why this exists
    ---------------
    The legacy pattern in this module relied on JOINs to silently filter
    out unauthorised reads (a non-linked family member would simply get an
    empty result set). That worked for security but produced inconsistent
    HTTP semantics across sibling endpoints — `/overview` 404s, `/updates`
    returned 200 `[]`. A frontend can't tell "no updates yet" from
    "you don't have access" with that contract.

    By raising explicitly here, every family endpoint that calls this
    helper has identical behaviour: 404 with the OWASP-uniform
    ``"Patient not found"`` message that does NOT distinguish between
    "this patient does not exist" and "this patient exists but isn't
    yours" — protecting against ID enumeration attacks.

    Defense-in-depth bonus
    ----------------------
    If a future refactor accidentally drops the ``family_patient_links``
    JOIN from a downstream query, this explicit check keeps the data
    safe. The two-layer protection (auth check + scoped JOIN) is the
    standard pattern for sensitive read endpoints.
    """
    exists = await db.execute(
        select(FamilyPatientLink.id).where(
            FamilyPatientLink.family_user_id == family_user_id,
            FamilyPatientLink.patient_id == patient_id,
        )
    )
    if exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Patient not found")


async def get_active_admission(patient_id: str, db: AsyncSession) -> Admission:
    """Return the active admission for a patient.

    Many family and patient-facing operations are scoped to active admissions.
    If a patient appears to "not exist" in family flows, confirm that they still
    have a non-discharged admission.
    """
    result = await db.execute(
        select(Admission).where(
            Admission.patient_id == patient_id,
            Admission.discharged_at.is_(None),
        )
    )
    admission = result.scalar_one_or_none()
    if admission is None:
        raise HTTPException(
            status_code=404, detail="Patient or active admission not found"
        )
    return admission


async def get_patient_with_links(patient_id: str, db: AsyncSession) -> Patient:
    """Load a patient together with linked family accounts.

    Eager-loading avoids N+1 lookups when rendering member lists in the family UI.
    """
    result = await db.execute(
        select(Patient)
        .where(Patient.id == patient_id)
        .options(
            selectinload(Patient.family_links).selectinload(
                FamilyPatientLink.family_user
            )
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    return patient


async def validate_family_user(
    family_user_id: str,
    db: AsyncSession,
) -> User:
    """Ensure the supplied user is an active family account.

    This is used by direct-link flows where staff select an existing family user.
    """
    result = await db.execute(
        select(User).where(
            User.id == family_user_id,
            User.role == UserRole.family,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid family member")
    return user


async def link_family_member_to_patient(
    patient_id: str,
    family_user_id: str,
    relationship_to_patient: str | None,
    db: AsyncSession,
) -> tuple[FamilyPatientLink, User]:
    """Create or update a family-to-patient access link.

    Debugging notes:
    - Existing links are updated in place instead of duplicated.
    - The returned tuple contains both the link row and the resolved family user
      so the API layer can build a response without re-querying.
    """
    await get_active_admission(patient_id, db)

    family_user = await validate_family_user(
        family_user_id=family_user_id,
        db=db,
    )

    existing = await db.execute(
        select(FamilyPatientLink).where(
            FamilyPatientLink.patient_id == patient_id,
            FamilyPatientLink.family_user_id == family_user_id,
        )
    )
    link = existing.scalar_one_or_none()
    if link is None:
        link = FamilyPatientLink(
            patient_id=patient_id,
            family_user_id=family_user_id,
            relationship_to_patient=relationship_to_patient,
        )
        db.add(link)
        await db.flush()
    else:
        link.relationship_to_patient = relationship_to_patient

    return link, family_user


async def unlink_family_member_from_patient(
    patient_id: str,
    family_user_id: str,
    db: AsyncSession,
) -> None:
    """Remove a family link for a patient without deleting the user account."""
    await get_active_admission(patient_id, db)

    result = await db.execute(
        select(FamilyPatientLink).where(
            FamilyPatientLink.patient_id == patient_id,
            FamilyPatientLink.family_user_id == family_user_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Family link not found")

    await db.delete(link)


async def list_family_user_active_admissions(
    family_user_id: str,
    db: AsyncSession,
) -> list[Admission]:
    """Return active admissions visible to a given family account.

    This powers the family dashboard list. We intentionally filter to active
    admissions so discharged encounters do not show on the default mobile home.
    """
    result = await db.execute(
        select(Admission)
        .join(Patient, Patient.id == Admission.patient_id)
        .join(FamilyPatientLink, FamilyPatientLink.patient_id == Patient.id)
        .where(
            Admission.discharged_at.is_(None),
            FamilyPatientLink.family_user_id == family_user_id,
        )
        .options(selectinload(Admission.patient))
    )
    return list(result.scalars().all())


async def get_family_patient_overview_admission(
    family_user_id: str,
    patient_id: str,
    db: AsyncSession,
) -> Admission:
    """Load a single family-visible patient with everything needed for overview UI.

    The query eagerly loads patient identity, care team, primary doctor, and the
    latest updates relation so the router can shape overview/mobile responses
    without issuing extra queries.
    """
    result = await db.execute(
        select(Admission)
        .join(FamilyPatientLink, FamilyPatientLink.patient_id == Admission.patient_id)
        .where(
            Admission.patient_id == patient_id,
            Admission.discharged_at.is_(None),
            FamilyPatientLink.family_user_id == family_user_id,
        )
        .options(
            selectinload(Admission.patient),
            selectinload(Admission.primary_doctor),
            selectinload(Admission.care_assignments).selectinload(CareAssignment.staff),
            selectinload(Admission.updates).selectinload(ClinicalUpdate.authored_by),
        )
        .order_by(Admission.admitted_at.desc())
        .limit(1)
    )
    admission = result.scalar_one_or_none()
    if admission is None:
        # Uniform error response per OWASP non-disclosure pattern:
        # the message is identical whether the patient_id is fake or
        # belongs to another family. Don't say "not found *for this
        # account*" — that confirms the patient exists somewhere in
        # the DB, letting an attacker enumerate IDs.
        raise HTTPException(status_code=404, detail="Patient not found")
    return admission


async def list_family_patient_updates(
    family_user_id: str,
    patient_id: str,
    skip: int,
    limit: int,
    db: AsyncSession,
) -> list[ClinicalUpdate]:
    """Return paginated clinical updates for a family-visible patient."""
    # Explicit authorization check — raises 404 if no link exists, so
    # the SQL below cannot leak data and the HTTP response is consistent
    # with sibling endpoints (/overview, /mobile-dashboard).
    await assert_family_link_or_404(
        db=db, family_user_id=family_user_id, patient_id=patient_id
    )

    result = await db.execute(
        select(ClinicalUpdate)
        .join(Admission, Admission.id == ClinicalUpdate.admission_id)
        .join(FamilyPatientLink, FamilyPatientLink.patient_id == Admission.patient_id)
        .where(
            Admission.patient_id == patient_id,
            Admission.discharged_at.is_(None),
            FamilyPatientLink.family_user_id == family_user_id,
        )
        .options(selectinload(ClinicalUpdate.authored_by))
        .order_by(ClinicalUpdate.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


def _hash_token(raw_token: str) -> str:
    # Only token hashes are stored in DB. If invite rows leak, raw invite URLs
    # still cannot be reconstructed from persisted data.
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _generate_access_code() -> str:
    # Avoid ambiguous characters so access codes are easier to read over phone
    # or retype from email/SMS without support friction.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


async def create_family_invite(
    patient_id: str,
    inviter: User,
    family_member_name: str,
    email: str,
    phone: str | None,
    relationship_to_patient: str | None,
    db: AsyncSession,
) -> tuple[FamilyInvite, str]:
    """Create a pending family invite and return the raw token for delivery.

    The raw token is returned only once to the caller so it can be embedded in a
    secure link. The database stores the SHA-256 hash instead.
    """
    await get_active_admission(patient_id, db)

    # The token is high-entropy because it will act as bearer proof in the invite URL.
    raw_token = secrets.token_urlsafe(32)
    invite = FamilyInvite(
        patient_id=patient_id,
        inviter_user_id=inviter.id,
        email=email.lower(),
        family_member_name=family_member_name.strip(),
        phone=phone.strip() if phone else None,
        relationship_to_patient=relationship_to_patient,
        token_hash=_hash_token(raw_token),
        access_code=_generate_access_code(),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(invite)
    await db.flush()
    return invite, raw_token


async def accept_family_invite(
    email: str,
    raw_token: str,
    access_code: str,
    full_name: str | None,
    password: str | None,
    phone: str | None,
    db: AsyncSession,
) -> tuple[FamilyPatientLink, User]:
    """Accept a family invite after validating token, code, email, and expiry.

    This function supports both cases:
    - invited email already has a family account
    - invited email needs a new family account created during acceptance

    Debugging notes:
    - Invitation lookup is by token hash, never by raw token storage.
    - Email verification is explicit so the verification screen can prove that
      the recipient controls the address originally invited.
    - Invite status transitions from `pending` to `accepted` or `expired` here.
    """
    # [Security/Authentication]: Re-hash the provided plaintext URL token to match
    # against the securely stored token_hash.
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(FamilyInvite).where(FamilyInvite.token_hash == token_hash)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invitation not found")

    # Only pending invites are redeemable. Accepted/revoked/expired invites must fail.
    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invitation is no longer valid")

    if invite.access_code != access_code:
        raise HTTPException(status_code=400, detail="Invalid access code")

    if invite.email.lower() != email.lower().strip():
        raise HTTPException(status_code=400, detail="Email does not match invitation")

    # Expiration is checked before any user linking so stale invites cannot grant access.
    if invite.expires_at <= datetime.now(timezone.utc):
        invite.status = "expired"
        raise HTTPException(status_code=400, detail="Invitation has expired")

    admission = await get_active_admission(invite.patient_id, db)

    # If the email already belongs to a family account, reuse it. Otherwise create
    # a new family user during acceptance.
    user = await get_user_by_email(db, invite.email)
    if user is None:
        if not password:
            raise HTTPException(
                status_code=400,
                detail="Password is required to create a new family account",
            )
        user = User(
            email=invite.email,
            hashed_password=hash_password(password),
            full_name=(
                full_name or invite.family_member_name or "Family Member"
            ).strip(),
            phone=(phone or invite.phone),
            role=UserRole.family,
            hospital_id=admission.hospital_id,
        )
        db.add(user)
        await db.flush()
    else:
        if user.role != UserRole.family:
            raise HTTPException(
                status_code=400,
                detail="Email belongs to a non-family account",
            )
        if full_name:
            user.full_name = full_name.strip()
        if phone:
            user.phone = phone.strip()

    # Acceptance is idempotent with respect to the patient-family link. If the
    # link already exists, we update missing relationship metadata instead.
    existing_link = await db.execute(
        select(FamilyPatientLink).where(
            FamilyPatientLink.patient_id == invite.patient_id,
            FamilyPatientLink.family_user_id == user.id,
        )
    )
    link = existing_link.scalar_one_or_none()
    if link is None:
        link = FamilyPatientLink(
            patient_id=invite.patient_id,
            family_user_id=user.id,
            relationship_to_patient=invite.relationship_to_patient,
        )
        db.add(link)
        await db.flush()
    elif not link.relationship_to_patient and invite.relationship_to_patient:
        link.relationship_to_patient = invite.relationship_to_patient

    # Mark the invite as consumed so the token/code pair cannot be reused.
    invite.status = "accepted"
    invite.accepted_user_id = user.id
    invite.accepted_at = datetime.now(timezone.utc)

    return link, user
