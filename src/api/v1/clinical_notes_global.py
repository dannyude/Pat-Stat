"""Global Clinical Notes endpoint — hospital-wide recent staff notes."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from datetime import datetime

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.patients.models import Admission, StaffNote
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/clinical-notes", tags=["Clinical Notes (Global)"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------


class GlobalStaffNoteOut(BaseModel):
    """A single staff note in the hospital-wide clinical notes timeline.

    Matches the Figma "Clinical Notes" page which lists recent notes
    across all patients with patient name, content, and timestamp.
    """

    id: str
    patient_id: str
    patient_name: str
    content: str
    category: str
    is_urgent: bool
    author_name: Optional[str] = None
    author_role: Optional[UserRole] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/recent", response_model=List[GlobalStaffNoteOut])
async def list_recent_clinical_notes(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None, description="Filter by note category"),
    urgent_only: bool = False,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Hospital-wide recent clinical notes, newest first.

    Powers the Figma "Clinical Notes" page which shows a combined list
    of staff notes across all patients with patient name context.
    """
    if current_user.hospital_id is None:
        return []

    q = (
        select(StaffNote)
        .join(Admission, StaffNote.admission_id == Admission.id)
        .options(
            selectinload(StaffNote.authored_by),
            selectinload(StaffNote.admission).selectinload(Admission.patient),
        )
        .where(Admission.hospital_id == current_user.hospital_id)
    )
    if category is not None:
        q = q.where(StaffNote.category == category)
    if urgent_only:
        q = q.where(StaffNote.is_urgent.is_(True))

    result = await db.execute(
        q.order_by(StaffNote.created_at.desc()).offset(skip).limit(limit)
    )
    notes = result.scalars().all()
    return [
        GlobalStaffNoteOut(
            id=n.id,
            patient_id=n.admission.patient_id,
            patient_name=(
                n.admission.patient.full_name if n.admission.patient else "Unknown"
            ),
            content=n.content,
            category=n.category.value if hasattr(n.category, "value") else n.category,
            is_urgent=n.is_urgent,
            author_name=(n.authored_by.full_name if n.authored_by else None),
            author_role=(n.authored_by.role if n.authored_by else None),
            created_at=n.created_at,
        )
        for n in notes
    ]


__all__ = ["router"]
