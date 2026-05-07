"""Emergency Flags endpoints — CRUD for urgent patient alerts."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.redis_client import publish_patient_event
from src.core.security import require_roles
from src.domains.notifications import policy
from src.domains.notifications.dispatch import dispatch_family_notification
from src.domains.patients.models import Admission, EmergencyFlag
from src.domains.patients.schemas import (
    EmergencyFlagCreate,
    EmergencyFlagOut,
    EmergencyFlagResolve,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User
from src.domains.patients.emergency_flag_helpers import (
    _ensure_flag_visible_to_user,
    _flag_to_out,
    _get_flag,
)

router = APIRouter(prefix="/emergency-flags", tags=["Emergency Flags"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


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
    out = _flag_to_out(await _get_flag(flag.id, db))
    await publish_patient_event(str(admission.patient_id), {
        "type": "emergency_flag_raised",
        "patient_id": str(admission.patient_id),
        "flag_id": flag.id,
        "priority": body.priority.value,
        "reason": body.reason,
        "created_at": flag.created_at.isoformat(),
    })

    # Emergency flags are always tier=critical — quiet hours are ignored.
    dispatch_family_notification(
        patient_id=str(admission.patient_id),
        patient_name=admission.patient.full_name if admission.patient else "Patient",
        event_kind=policy.EVENT_EMERGENCY_FLAG,
        new_status=admission.status.value if admission.status else "Critical",
        update_id=None,
        note_preview=body.reason or "",
        author_name=current_user.full_name,
    )
    return out


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
