"""Global Update History endpoint — hospital-wide clinical update timeline."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from datetime import datetime

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.patients.enums import PatientStatus
from src.domains.patients.models import Admission, ClinicalUpdate
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/update-history", tags=["Update History"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class UpdateHistoryItemOut(BaseModel):
    """A single entry in the hospital-wide update history timeline.

    Matches the Figma "Update History" screen which shows patient name,
    author info, timestamp, note, status badge, and vitals.
    """

    id: str
    patient_id: str
    patient_name: str
    ward: str
    bed_number: str
    author_name: str
    author_role: UserRole
    status: PatientStatus
    note: str
    blood_pressure: Optional[str] = None
    heart_rate: Optional[str] = None
    temperature: Optional[str] = None
    oxygen_level: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=List[UpdateHistoryItemOut])
async def list_update_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Hospital-wide clinical update history, newest first.

    Returns all clinical updates across all patients in the user's hospital,
    with patient info and author details — powering the Figma "Update History"
    page that shows a combined timeline with vitals.
    """
    if current_user.hospital_id is None:
        return []

    result = await db.execute(
        select(ClinicalUpdate)
        .join(Admission, ClinicalUpdate.admission_id == Admission.id)
        .options(
            selectinload(ClinicalUpdate.authored_by),
            selectinload(ClinicalUpdate.admission).selectinload(Admission.patient),
        )
        .where(Admission.hospital_id == current_user.hospital_id)
        .order_by(ClinicalUpdate.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    updates = result.scalars().all()
    return [
        UpdateHistoryItemOut(
            id=u.id,
            patient_id=u.admission.patient_id,
            patient_name=(
                u.admission.patient.full_name if u.admission.patient else "Unknown"
            ),
            ward=u.admission.ward,
            bed_number=u.admission.bed_number,
            author_name=(u.authored_by.full_name if u.authored_by else "System"),
            author_role=(u.authored_by.role if u.authored_by else UserRole.admin),
            status=u.status,
            note=u.note,
            blood_pressure=u.blood_pressure,
            heart_rate=u.heart_rate,
            temperature=u.temperature,
            oxygen_level=u.oxygen_level,
            created_at=u.created_at,
        )
        for u in updates
    ]


__all__ = ["router"]
