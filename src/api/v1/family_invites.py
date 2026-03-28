"""Family invitation endpoints."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.rate_limit import limiter
from src.core.security import require_roles
from src.domains.family.schemas import (
    FamilyInviteAcceptRequest,
    FamilyInviteCreateRequest,
    FamilyInviteCreateResponse,
    FamilyMemberOut,
)
from src.domains.family.services import accept_family_invite, create_family_invite
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/family", tags=["Family"])

_admin_only = require_roles(UserRole.admin)


def _build_invite_link(raw_token: str) -> str:
    return f"https://pat-stat.com/family/join/{raw_token}"


@router.post(
    "/patients/{patient_id}/invites",
    response_model=FamilyInviteCreateResponse,
    status_code=201,
)
@limiter.limit(settings.write_rate_limit)
async def create_family_member_invite(
    request: Request,
    patient_id: str,
    body: FamilyInviteCreateRequest,
    current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    # [Logic]: Delegate invite creation to the service which generates
    # cryptographically secure tokens and an access_code.
    _ = request
    invite, raw_token = await create_family_invite(
        patient_id=patient_id,
        inviter=current_user,
        family_member_name=body.family_member_name,
        email=str(body.email),
        phone=body.phone,
        relationship_to_patient=body.relationship_to_patient,
        db=db,
    )
    invite_link = _build_invite_link(raw_token)

    # [Async Background Task]: Dispatch an email sending task to the Celery worker
    # so the API responds instantly without waiting for SMTP delays.
    from src.tasks.celery_app import celery_app

    celery_app.send_task(
        "src.tasks.notifications.send_family_invite_email",
        kwargs={
            "email": invite.email,
            "invite_link": invite_link,
            "access_code": invite.access_code,
            "patient_id": patient_id,
            "inviter_name": current_user.full_name,
        },
    )

    return FamilyInviteCreateResponse(
        invitation_id=invite.id,
        email=invite.email,
        invite_link=invite_link,
        access_code=invite.access_code,
        expires_at=invite.expires_at,
    )


@router.post("/invites/accept", response_model=FamilyMemberOut)
@limiter.limit(settings.auth_rate_limit)
async def accept_invite(
    request: Request,
    body: FamilyInviteAcceptRequest,
    db: AsyncSession = Depends(get_db),
):
    """Accept a family invite after verifying email, token, and access code."""
    _ = request
    # [Security/Logic]: The service validates the raw token against the hashed token
    # in the database, checks the access_code, verifies expiration, creates a new User,
    # and links them to the patient in a single transaction.
    link, user = await accept_family_invite(
        email=str(body.email),
        raw_token=body.token,
        access_code=body.access_code,
        full_name=body.full_name,
        password=body.password,
        phone=body.phone,
        db=db,
    )
    return FamilyMemberOut(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        relationship_to_patient=link.relationship_to_patient,
    )


__all__ = ["router"]
