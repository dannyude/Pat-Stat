"""Staff invitation business logic.

This module owns the security-critical decision points for the staff
invitation lifecycle. The API layer (router) should remain thin and
delegate all validation, token management, and user creation here.

Flow:
    Admin creates invite
        -> service generates token + access_code, persists StaffInvite
        -> returns (invite, raw_token) to caller (raw token given once)
        -> caller dispatches async email task with the raw token

    Staff verifies invite
        -> service hashes the provided raw token, looks up StaffInvite
        -> validates: status=pending, email match, access_code match, not expired
        -> returns invite metadata (name, role, hospital) for the setup form

    Staff sets up account
        -> service re-validates everything (defense in depth)
        -> creates User row with hashed password, role, hospital_id
        -> marks invite as accepted (idempotent)
        -> returns the new User

Security model mirrors the FamilyInvite flow:
    - Token: secrets.token_urlsafe(32) -> SHA-256 hash stored in DB
    - Access code: 8-char alphanumeric (ambiguous chars excluded)
    - Both + email match required for any acceptance action
"""

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.security import hash_password
from src.domains.staff_invites.models import StaffInvite
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from src.domains.users.services import get_user_by_email

logger = logging.getLogger(__name__)

# ── Token Utilities ──────────────────────────────────────────────────────────
# Identical to the family invite helpers. In a larger codebase you'd extract
# these into src/core/tokens.py, but co-locating keeps the domain self-contained
# for now (YAGNI — You Aren't Gonna Need It until a third invite type appears).


def _hash_token(raw_token: str) -> str:
    """SHA-256 hash prevents invite hijacking if the DB leaks."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _generate_access_code() -> str:
    """8-char alphanumeric code, excluding ambiguous characters (I, O, 1, 0).

    This code is the 'second factor' — shared via a side channel (admin tells
    staff verbally, or it's displayed on the admin's success screen).
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(8))


# ── Shared Validation ────────────────────────────────────────────────────────


async def _load_and_validate_invite(
    email: str,
    raw_token: str,
    access_code: str,
    db: AsyncSession,
) -> StaffInvite:
    """Core validation logic used by both verify and setup endpoints.

    Validates all three factors (token, access code, email) and checks
    expiration. Returns the invite if everything passes.

    Why validate all three factors every time?
    - Defense in depth: even if the verify endpoint was called from a
      different browser session, the setup endpoint doesn't blindly trust it.
    - Stateless: no session cookie or setup_token needed between steps.
    """
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(StaffInvite)
        .options(selectinload(StaffInvite.hospital))
        .where(StaffInvite.token_hash == token_hash)
    )
    invite = result.scalar_one_or_none()

    if invite is None:
        raise HTTPException(status_code=404, detail="Invitation not found")

    if invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invitation is no longer valid")

    if invite.access_code != access_code.strip().upper():
        raise HTTPException(status_code=400, detail="Invalid access code")

    if invite.email.lower() != email.lower().strip():
        raise HTTPException(status_code=400, detail="Email does not match invitation")

    if invite.expires_at <= datetime.now(timezone.utc):
        invite.status = "expired"
        raise HTTPException(status_code=400, detail="Invitation has expired")

    return invite


# ── Service Functions ────────────────────────────────────────────────────────


async def create_staff_invite(
    hospital_id: str,
    inviter: User,
    staff_name: str,
    email: str,
    role: UserRole,
    db: AsyncSession,
) -> tuple[StaffInvite, str]:
    """Create a pending staff invite and return the raw token for email delivery.

    The raw token is returned ONCE to the caller for embedding in an invite URL.
    Only the SHA-256 hash is persisted. This is the 'store hash, return secret'
    pattern — same approach used by GitHub personal access tokens.

    Raises:
        HTTPException 400: If the email already belongs to an active user,
            or if the role is not doctor/nurse.
        HTTPException 409: If a pending invite for this email+hospital already exists.
    """
    # Guard: only doctor and nurse roles are valid for staff invites
    if role not in (UserRole.doctor, UserRole.nurse):
        raise HTTPException(
            status_code=400,
            detail="Staff invites are only valid for doctor and nurse roles",
        )

    # Guard: don't invite someone who already has an account
    existing_user = await get_user_by_email(db, email.lower().strip())
    if existing_user and existing_user.is_active:
        raise HTTPException(
            status_code=400,
            detail="A user with this email already exists",
        )

    # Guard: don't create duplicate pending invites for the same email+hospital
    existing_invite = await db.execute(
        select(StaffInvite).where(
            StaffInvite.email == email.lower().strip(),
            StaffInvite.hospital_id == hospital_id,
            StaffInvite.status == "pending",
        )
    )
    if existing_invite.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A pending invite for this email already exists at this hospital",
        )

    raw_token = secrets.token_urlsafe(32)
    invite = StaffInvite(
        hospital_id=hospital_id,
        inviter_user_id=inviter.id,
        email=email.lower().strip(),
        staff_name=staff_name.strip(),
        role=role,
        token_hash=_hash_token(raw_token),
        access_code=_generate_access_code(),
        status="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(invite)
    await db.flush()

    logger.info(
        "Staff invite created: %s (%s) at hospital %s by %s",
        email,
        role.value,
        hospital_id,
        inviter.id,
    )
    return invite, raw_token


async def verify_staff_invite(
    email: str,
    raw_token: str,
    access_code: str,
    db: AsyncSession,
) -> StaffInvite:
    """Validate an invite without consuming it.

    This is the 'dry run' — the frontend calls this to check if the invite
    is valid before showing the password setup form. No state is mutated.

    Returns:
        The StaffInvite with hospital eagerly loaded (for hospital_name).
    """
    return await _load_and_validate_invite(email, raw_token, access_code, db)


async def setup_staff_account(
    email: str,
    raw_token: str,
    access_code: str,
    full_name: str,
    password: str,
    phone: str | None,
    db: AsyncSession,
) -> tuple[StaffInvite, User]:
    """Consume an invite and create the staff user account.

    This is the final step. After this:
    - A new User row exists with the correct role and hospital_id
    - The invite status flips from 'pending' to 'accepted'
    - The invite token can never be reused

    Returns:
        (invite, user) tuple for response building.
    """
    invite = await _load_and_validate_invite(email, raw_token, access_code, db)

    # Double-check no user was created between verify and setup
    # (race condition window is small but real in concurrent systems)
    existing_user = await get_user_by_email(db, invite.email)
    if existing_user and existing_user.is_active:
        raise HTTPException(
            status_code=400,
            detail="An account with this email was already created",
        )

    # Create the staff user
    user = User(
        email=invite.email,
        hashed_password=hash_password(password),
        full_name=full_name.strip(),
        phone=phone.strip() if phone else None,
        role=invite.role,
        hospital_id=invite.hospital_id,
    )
    db.add(user)
    await db.flush()

    # Mark invite as consumed — the token/code pair can never be reused
    invite.status = "accepted"
    invite.accepted_user_id = user.id
    invite.accepted_at = datetime.now(timezone.utc)

    logger.info(
        "Staff account created: %s (%s) at hospital %s via invite %s",
        user.email,
        user.role.value,
        user.hospital_id,
        invite.id,
    )
    return invite, user


async def list_hospital_invites(
    hospital_id: str,
    status_filter: str | None,
    skip: int,
    limit: int,
    db: AsyncSession,
) -> list[StaffInvite]:
    """Return staff invites for a hospital, optionally filtered by status.

    Used by the admin dashboard to see pending/accepted/expired invites.
    """
    query = (
        select(StaffInvite)
        .options(selectinload(StaffInvite.inviter))
        .where(StaffInvite.hospital_id == hospital_id)
    )
    if status_filter:
        query = query.where(StaffInvite.status == status_filter)

    query = query.order_by(StaffInvite.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def revoke_staff_invite(
    invite_id: str,
    hospital_id: str,
    db: AsyncSession,
) -> StaffInvite:
    """Revoke a pending invite so the token/code pair can no longer be used.

    Only pending invites can be revoked. Already-accepted invites cannot
    be 'un-accepted' — the admin should deactivate the user instead.
    """
    result = await db.execute(
        select(StaffInvite).where(
            StaffInvite.id == invite_id,
            StaffInvite.hospital_id == hospital_id,
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")

    if invite.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot revoke invite with status '{invite.status}'",
        )

    invite.status = "revoked"
    logger.info("Staff invite revoked: %s", invite_id)
    return invite


__all__ = [
    "create_staff_invite",
    "verify_staff_invite",
    "setup_staff_account",
    "list_hospital_invites",
    "revoke_staff_invite",
]
