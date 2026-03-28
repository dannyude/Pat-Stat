"""Emergency Flags endpoints — CRUD for urgent patient alerts."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.patients.models import Admission, EmergencyFlag
from src.domains.patients.schemas import (
    EmergencyFlagCreate,
    EmergencyFlagOut,
    EmergencyFlagResolve,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/emergency-flags", tags=["Emergency Flags"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


# Helpers


def _flag_to_out(flag: EmergencyFlag) -> EmergencyFlagOut:
    admission = flag.admission
    return EmergencyFlagOut(
        id=flag.id,
        admission_id=admission.id,
        patient_id=admission.patient_id,
        patient_name=admission.patient.full_name if admission.patient else "Unknown",
        ward=admission.ward,
        bed_number=admission.bed_number,
        priority=flag.priority,
        reason=flag.reason,
        is_resolved=flag.is_resolved,
        flagged_by_name=(flag.flagged_by.full_name if flag.flagged_by else None),
        resolved_by_name=(flag.resolved_by.full_name if flag.resolved_by else None),
        resolved_at=flag.resolved_at,
        created_at=flag.created_at,
    )


async def _get_flag(flag_id: str, db: AsyncSession) -> EmergencyFlag:
    # [Performance/DB]: Eager load all related entities (admission, patient, author, resolver)
    # needed for Pydantic serialization over the _flag_to_out helper.
    result = await db.execute(
        select(EmergencyFlag)
        .options(
            selectinload(EmergencyFlag.admission).selectinload(Admission.patient),
            selectinload(EmergencyFlag.flagged_by),
            selectinload(EmergencyFlag.resolved_by),
        )
        .where(EmergencyFlag.id == flag_id)
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=404, detail="Emergency flag not found")
    return flag


def _ensure_flag_visible_to_user(flag: EmergencyFlag, current_user: User) -> None:
    # [Security/RBAC]: Sub-resource tenancy check. Ensure the flag belongs to an
    # admission in the same hospital as the current user. Returns 404 to avoid leaking existence.
    if current_user.hospital_id is None:
        raise HTTPException(
            status_code=403, detail="User is not assigned to a hospital"
        )
    if str(flag.admission.hospital_id) != str(current_user.hospital_id):
        raise HTTPException(status_code=404, detail="Emergency flag not found")


# Endpoints


@router.get("", response_model=List[EmergencyFlagOut])
async def list_emergency_flags(
    resolved: bool = False,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """List emergency flags for the hospital. Defaults to unresolved flags."""
    result = await db.execute(
        select(EmergencyFlag)
        .join(Admission, EmergencyFlag.admission_id == Admission.id)
        .options(
            selectinload(EmergencyFlag.admission).selectinload(Admission.patient),
            selectinload(EmergencyFlag.flagged_by),
            selectinload(EmergencyFlag.resolved_by),
        )
        .where(
            Admission.hospital_id == current_user.hospital_id,
            EmergencyFlag.is_resolved == resolved,
        )
        .order_by(EmergencyFlag.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    flags = result.scalars().all()
    return [_flag_to_out(f) for f in flags]


@router.post("", response_model=EmergencyFlagOut, status_code=201)
async def create_emergency_flag(
    body: EmergencyFlagCreate,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new emergency flag on an admission."""
    if current_user.hospital_id is None:
        raise HTTPException(
            status_code=403, detail="User is not assigned to a hospital"
        )

    result = await db.execute(
        select(Admission)
        .options(selectinload(Admission.patient))
        .where(
            Admission.id == body.admission_id,
            Admission.hospital_id == current_user.hospital_id,
        )
        .limit(1)
    )
    admission = result.scalar_one_or_none()
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")

    flag = EmergencyFlag(
        admission_id=admission.id,
        flagged_by_id=current_user.id,
        priority=body.priority,
        reason=body.reason,
    )
    db.add(flag)
    await db.flush()

    # Re-fetch with relationships for the response
    return _flag_to_out(await _get_flag(flag.id, db))


@router.get("/count")
async def emergency_flag_count(
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Count of active (unresolved) emergency flags — used for sidebar badge."""
    result = await db.execute(
        select(count())
        .select_from(EmergencyFlag)
        .join(Admission, EmergencyFlag.admission_id == Admission.id)
        .where(
            Admission.hospital_id == current_user.hospital_id,
            EmergencyFlag.is_resolved.is_(False),
        )
    )
    return {"count": result.scalar() or 0}


@router.get("/{flag_id}", response_model=EmergencyFlagOut)
async def get_emergency_flag(
    flag_id: str,
    _current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Get a single emergency flag."""
    flag = await _get_flag(flag_id, db)
    _ensure_flag_visible_to_user(flag, _current_user)
    return _flag_to_out(flag)


@router.patch("/{flag_id}/resolve", response_model=EmergencyFlagOut)
async def resolve_emergency_flag(
    flag_id: str,
    _body: EmergencyFlagResolve,  # noqa: ARG001 - reserved for future resolution notes
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Mark an emergency flag as resolved."""
    flag = await _get_flag(flag_id, db)
    _ensure_flag_visible_to_user(flag, current_user)
    if flag.is_resolved:
        raise HTTPException(status_code=400, detail="Flag is already resolved")

    flag.is_resolved = True
    flag.resolved_by_id = current_user.id
    flag.resolved_by = current_user
    flag.resolved_at = datetime.now(timezone.utc)
    await db.flush()

    return _flag_to_out(flag)


__all__ = ["router"]
