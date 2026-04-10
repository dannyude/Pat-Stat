"""Shift Handover endpoints — create and retrieve shift handover notes."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.redis_client import publish_patient_event
from src.core.security import require_roles
from src.domains.patients.models import Admission, ShiftHandover
from src.domains.patients.schemas import ShiftHandoverCreate, ShiftHandoverOut
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/shift-handovers", tags=["Shift Handover"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handover_to_out(h: ShiftHandover) -> ShiftHandoverOut:
    admission = h.admission
    return ShiftHandoverOut(
        id=h.id,
        admission_id=h.admission_id,
        patient_id=admission.patient_id,
        patient_name=admission.patient.full_name if admission.patient else "Unknown",
        from_staff_name=(h.from_staff.full_name if h.from_staff else None),
        to_staff_name=(h.to_staff.full_name if h.to_staff else None),
        summary=h.summary,
        pending_actions=h.pending_actions,
        created_at=h.created_at,
    )


def _handover_query():
    """
    Base query with all required eager loads.
    [Performance/DB]: We always load admission (and patient), from_staff,
    and to_staff to avoid N+1 queries during Pydantic serialization.
    """
    return select(ShiftHandover).options(
        selectinload(ShiftHandover.admission).selectinload(Admission.patient),
        selectinload(ShiftHandover.from_staff),
        selectinload(ShiftHandover.to_staff),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=List[ShiftHandoverOut])
async def list_shift_handovers(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """List shift handovers for the current user's hospital, newest first."""
    if current_user.hospital_id is None:
        return []

    result = await db.execute(
        _handover_query()
        .join(Admission, ShiftHandover.admission_id == Admission.id)
        .where(Admission.hospital_id == current_user.hospital_id)
        .order_by(ShiftHandover.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    handovers = result.scalars().all()
    return [_handover_to_out(h) for h in handovers]


@router.post("", response_model=ShiftHandoverOut, status_code=201)
async def create_shift_handover(
    body: ShiftHandoverCreate,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new shift handover note for an admission."""
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
    )
    admission = result.scalar_one_or_none()
    if not admission:
        raise HTTPException(status_code=404, detail="Admission not found")

    # [Validation]: Validate to_staff_id if provided.
    # We must ensure the receiving staff is active and in the same hospital.
    if body.to_staff_id:
        staff_result = await db.execute(
            select(User).where(
                User.id == body.to_staff_id,
                User.hospital_id == current_user.hospital_id,
                User.is_active.is_(True),
            )
        )
        if not staff_result.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Invalid to_staff_id")

    handover = ShiftHandover(
        admission_id=body.admission_id,
        from_staff_id=current_user.id,
        to_staff_id=body.to_staff_id,
        summary=body.summary,
        pending_actions=body.pending_actions,
    )
    db.add(handover)
    await db.flush()

    # Re-fetch with relationships
    result = await db.execute(_handover_query().where(ShiftHandover.id == handover.id))
    out = _handover_to_out(result.scalar_one())
    await publish_patient_event(str(admission.patient_id), {
        "type": "handover_recorded",
        "patient_id": str(admission.patient_id),
        "handover_id": handover.id,
        "summary": body.summary,
        "created_at": handover.created_at.isoformat(),
    })
    return out


@router.get("/{handover_id}", response_model=ShiftHandoverOut)
async def get_shift_handover(
    handover_id: str,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Get a single shift handover by ID."""
    if current_user.hospital_id is None:
        raise HTTPException(
            status_code=403, detail="User is not assigned to a hospital"
        )

    result = await db.execute(_handover_query().where(ShiftHandover.id == handover_id))
    handover = result.scalar_one_or_none()
    if not handover:
        raise HTTPException(status_code=404, detail="Shift handover not found")
    if str(handover.admission.hospital_id) != str(current_user.hospital_id):
        raise HTTPException(status_code=404, detail="Shift handover not found")
    return _handover_to_out(handover)


__all__ = ["router"]
