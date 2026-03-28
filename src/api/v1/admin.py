"""
Admin account-management endpoints.

Handles the one-time system bootstrap for the first admin,
and subsequent staff registrations (doctors, nurses, admins) by an existing admin.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.database import get_db
from src.core.rate_limit import limiter
from src.core.security import hash_password, require_admin
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from src.domains.users.schemas import RegisterRequest, UserOut

router = APIRouter(prefix="/auth", tags=["Admin"])


@router.post("/register-staff", response_model=UserOut, status_code=201)
@limiter.limit(settings.auth_rate_limit)
async def register_staff(
    request: Request,
    body: RegisterRequest,
    current_user: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Create doctor, nurse, or admin accounts (admin only).
    """
    _ = request
    # [Validation]: Prevent admins from creating Family users here,
    # as Family users require a different linking flow.
    if body.role not in (
        UserRole.doctor,
        UserRole.nurse,
        UserRole.admin,
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Only doctor, nurse, and admin roles are allowed " "for this endpoint"
            ),
        )

    # [DB/Logic]: Prevent duplicate accounts across the entire platform.
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        phone=body.phone,
        # [Business Rule]: Newly registered staff inherit the hospital_id of the admin creating them.
        hospital_id=current_user.hospital_id,
    )
    db.add(user)
    await db.flush()
    result = await db.execute(
        select(User).options(selectinload(User.hospital)).where(User.id == user.id)
    )
    return result.scalar_one()


__all__ = ["router"]
