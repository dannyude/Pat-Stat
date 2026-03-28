"""Staff endpoints — native DDD implementation."""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.security import get_current_user, require_roles
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from src.domains.users.schemas import UserOut

router = APIRouter(prefix="/staff", tags=["Staff"])

_admin_only = require_roles(UserRole.admin)
_clinical_or_admin = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


@router.get("", response_model=List[UserOut])
async def list_staff(
    role: UserRole | None = Query(None, description="Filter by role"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """
    List all staff members (admin only).
    Used in user-management dashboards.
    """
    # [Performance/DB]: We only return active staff to avoid displaying deleted accounts.
    q = select(User).options(selectinload(User.hospital)).where(User.is_active.is_(True))
    if role is not None:
        q = q.where(User.role == role)
    result = await db.execute(q.offset(skip).limit(limit))
    return result.scalars().all()


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    """Return the calling user's profile."""
    return current_user


@router.get("/doctors", response_model=List[UserOut])
async def list_doctors_for_hospital(
    current_user: User = Depends(_clinical_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Active doctors in the caller's hospital — used to populate assignment dropdowns."""
    # [DB/Logic]: Restrict the list to active doctors in the SAME hospital as the caller.
    result = await db.execute(
        select(User)
        .options(selectinload(User.hospital))
        .where(
            User.role == UserRole.doctor,
            User.is_active.is_(True),
            User.hospital_id == current_user.hospital_id,
        )
        .order_by(User.full_name)
    )
    return result.scalars().all()


@router.get("/nurses", response_model=List[UserOut])
async def list_nurses_for_hospital(
    current_user: User = Depends(_clinical_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """Active nurses in the caller's hospital — used to populate assignment dropdowns."""
    result = await db.execute(
        select(User)
        .options(selectinload(User.hospital))
        .where(
            User.role == UserRole.nurse,
            User.is_active.is_(True),
            User.hospital_id == current_user.hospital_id,
        )
        .order_by(User.full_name)
    )
    return result.scalars().all()


__all__ = ["router"]
