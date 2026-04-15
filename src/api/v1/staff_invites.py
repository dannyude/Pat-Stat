"""Staff invitation endpoints.

Implements the invitation-based onboarding flow for doctors and nurses:

    POST   /staff/invites          — Admin creates invite (auth: admin)
    GET    /staff/invites          — Admin lists invites (auth: admin)
    POST   /staff/invites/verify   — Staff verifies identity (public)
    POST   /staff/invites/setup    — Staff creates account (public)
    DELETE /staff/invites/{id}     — Admin revokes invite (auth: admin)

Design notes:
- The verify and setup endpoints are PUBLIC (no auth required) because the
  staff member doesn't have an account yet. The three-factor validation
  (token + access_code + email) provides the security instead of JWT.
- The admin endpoints require the `admin` role and automatically scope
  queries to the admin's own hospital (tenant isolation).
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.rate_limit import limiter
from src.core.security import require_roles
from src.domains.staff_invites.schemas import (
    StaffInviteCreateRequest,
    StaffInviteCreateResponse,
    StaffInviteOut,
    StaffInviteSetupRequest,
    StaffInviteSetupResponse,
    StaffInviteVerifyRequest,
    StaffInviteVerifyResponse,
)
from src.domains.staff_invites.services import (
    create_staff_invite,
    list_hospital_invites,
    revoke_staff_invite,
    setup_staff_account,
    verify_staff_invite,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/staff", tags=["Staff Invites"])

_admin_only = require_roles(UserRole.admin)


def _build_invite_link(raw_token: str) -> str:
    """Build the frontend URL that staff will click in the invitation email.

    The raw token is a high-entropy URL-safe string (secrets.token_urlsafe(32)).
    It acts as bearer proof — anyone with this URL can start the verification flow.
    That's why we also require the access code as a second factor.
    """
    return f"https://pat-stat.com/staff/join/{raw_token}"


# ── Admin Endpoints ──────────────────────────────────────────────────────────


@router.post(
    "/invites",
    response_model=StaffInviteCreateResponse,
    status_code=201,
    summary="Create a staff invitation",
)
@limiter.limit(settings.write_rate_limit)
async def create_invite(
    request: Request,
    body: StaffInviteCreateRequest,
    current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates an invite for a new doctor or nurse.

    The system generates a secure invite link + access code, persists the
    invite, and dispatches an async email to the invited staff member.
    The admin also receives the access code in the response so they can
    share it via a secondary channel (verbal, chat, etc.).
    """
    _ = request  # Required by rate limiter

    invite, raw_token = await create_staff_invite(
        hospital_id=str(current_user.hospital_id),
        inviter=current_user,
        staff_name=body.staff_name,
        email=str(body.email),
        role=UserRole(body.role),
        db=db,
    )

    invite_link = _build_invite_link(raw_token)

    # [Fire-and-Forget Pattern]: Dispatch the email via Celery so the API
    # responds in ~50ms instead of waiting 5-15s for SMTP. If the email
    # fails, Celery retries 3x with exponential backoff. The admin still
    # gets the access code in the response as a fallback delivery channel.
    from src.tasks.celery_app import celery_app

    celery_app.send_task(
        "src.tasks.notifications.send_staff_invite_email",
        kwargs={
            "email": invite.email,
            "staff_name": invite.staff_name,
            "role": invite.role.value,
            "invite_link": invite_link,
            "access_code": invite.access_code,
            "hospital_name": current_user.hospital_name or "Your Hospital",
            "inviter_name": current_user.full_name,
        },
    )

    return StaffInviteCreateResponse(
        invitation_id=invite.id,
        email=invite.email,
        staff_name=invite.staff_name,
        role=invite.role.value,
        invite_link=invite_link,
        access_code=invite.access_code,
        expires_at=invite.expires_at,
    )


@router.get(
    "/invites",
    response_model=List[StaffInviteOut],
    summary="List staff invitations for this hospital",
)
@limiter.limit(settings.api_rate_limit_default)
async def list_invites(
    request: Request,
    status: Optional[str] = Query(
        None,
        description="Filter by status: pending, accepted, expired, revoked",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """List staff invites for the admin's hospital.

    Automatically scoped to the admin's hospital_id — an admin can never
    see invites for a different hospital (tenant isolation).
    """
    _ = request
    invites = await list_hospital_invites(
        hospital_id=str(current_user.hospital_id),
        status_filter=status,
        skip=skip,
        limit=limit,
        db=db,
    )
    return [
        StaffInviteOut(
            id=inv.id,
            email=inv.email,
            staff_name=inv.staff_name,
            role=inv.role.value,
            status=inv.status,
            access_code=inv.access_code,
            expires_at=inv.expires_at,
            created_at=inv.created_at,
            inviter_name=inv.inviter.full_name if inv.inviter else None,
        )
        for inv in invites
    ]


@router.delete(
    "/invites/{invite_id}",
    status_code=200,
    summary="Revoke a pending staff invitation",
)
@limiter.limit(settings.write_rate_limit)
async def revoke_invite(
    request: Request,
    invite_id: str,
    current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Revoke a pending invite so the token/code pair can no longer be used.

    Only pending invites can be revoked. To deactivate a staff member who
    already accepted, use the user deactivation endpoint instead.
    """
    _ = request
    await revoke_staff_invite(
        invite_id=invite_id,
        hospital_id=str(current_user.hospital_id),
        db=db,
    )
    return {"message": "Invitation revoked"}


# ── Public Endpoints (no auth — staff doesn't have an account yet) ───────────


@router.post(
    "/invites/verify",
    response_model=StaffInviteVerifyResponse,
    summary="Verify a staff invitation",
)
@limiter.limit(settings.auth_rate_limit)
async def verify_invite(
    request: Request,
    body: StaffInviteVerifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Staff member verifies their invite before seeing the account setup form.

    This is a read-only validation — no state is mutated. The frontend uses
    the response to pre-fill the setup form (name, role, hospital).

    Rate limited at auth_rate_limit (100/min) to prevent brute-force
    enumeration of access codes.
    """
    _ = request
    invite = await verify_staff_invite(
        email=str(body.email),
        raw_token=body.token,
        access_code=body.access_code,
        db=db,
    )
    return StaffInviteVerifyResponse(
        valid=True,
        staff_name=invite.staff_name,
        role=invite.role.value,
        hospital_name=invite.hospital.name if invite.hospital else "Unknown",
        email=invite.email,
    )


@router.post(
    "/invites/setup",
    response_model=StaffInviteSetupResponse,
    status_code=201,
    summary="Complete staff account setup",
)
@limiter.limit(settings.auth_rate_limit)
async def setup_account(
    request: Request,
    body: StaffInviteSetupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Staff member creates their account after successful verification.

    Re-validates all three factors (defense in depth) before creating the
    User row. After this endpoint succeeds, the staff member can log in
    via POST /auth/login with their chosen password.
    """
    _ = request
    invite, user = await setup_staff_account(
        email=str(body.email),
        raw_token=body.token,
        access_code=body.access_code,
        full_name=body.full_name,
        password=body.password,
        phone=body.phone,
        db=db,
    )
    return StaffInviteSetupResponse(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.value,
        hospital_name=invite.hospital.name if invite.hospital else "Unknown",
    )


__all__ = ["router"]
