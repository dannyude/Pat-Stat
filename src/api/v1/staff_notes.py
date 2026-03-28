"""Staff Note (Clinical Notes) CRUD endpoints."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.patients.models import Admission, StaffNote
from src.domains.patients.schemas import StaffNoteCreate, StaffNoteOut
from src.domains.users.enums import UserRole
from src.domains.users.models import User

from src.api.v1.patient_helpers import get_active_admission

router = APIRouter(prefix="/patients", tags=["Staff Notes"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


def _note_to_out(note: StaffNote, patient_id: str) -> StaffNoteOut:
    return StaffNoteOut(
        id=note.id,
        patient_id=patient_id,
        content=note.content,
        category=note.category,
        is_urgent=note.is_urgent,
        author_name=(note.authored_by.full_name if note.authored_by else None),
        author_role=(note.authored_by.role if note.authored_by else None),
        created_at=note.created_at,
    )


@router.post("/{patient_id}/notes", response_model=StaffNoteOut, status_code=201)
async def create_staff_note(
    patient_id: str,
    body: StaffNoteCreate,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new clinical/staff note for a patient's active admission."""
    # [DB/Logic]: Always link notes directly to the currently active admission.
    # The note will remain attached to this specific admission even if the patient
    # is discharged and admitted again later.
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)
    note = StaffNote(
        admission_id=admission.id,
        authored_by_id=current_user.id,
        content=body.content,
        category=body.category,
        is_urgent=body.is_urgent,
    )
    db.add(note)
    # [DB]: Flush to assign the note's ID before we re-fetch it with relationships.
    await db.flush()

    # [Performance/DB]: Re-fetch with author relationship eager-loaded for the response mapping.
    result = await db.execute(
        select(StaffNote)
        .options(selectinload(StaffNote.authored_by))
        .where(StaffNote.id == note.id)
    )
    note = result.scalar_one()
    return _note_to_out(note, patient_id)


@router.get("/{patient_id}/notes", response_model=List[StaffNoteOut])
async def list_staff_notes(
    patient_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    category: str | None = Query(None, description="Filter by note category"),
    urgent_only: bool = False,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """List staff notes for a patient's active admission, newest first."""
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)
    q = (
        select(StaffNote)
        .options(selectinload(StaffNote.authored_by))
        .where(StaffNote.admission_id == admission.id)
    )
    if category is not None:
        q = q.where(StaffNote.category == category)
    if urgent_only:
        q = q.where(StaffNote.is_urgent.is_(True))

    result = await db.execute(
        q.order_by(StaffNote.created_at.desc()).offset(skip).limit(limit)
    )
    notes = result.scalars().all()
    return [_note_to_out(n, patient_id) for n in notes]


@router.get("/{patient_id}/notes/{note_id}", response_model=StaffNoteOut)
async def get_staff_note(
    patient_id: str,
    note_id: str,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single staff note by ID."""
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)
    result = await db.execute(
        select(StaffNote)
        .options(selectinload(StaffNote.authored_by))
        .where(
            StaffNote.id == note_id,
            StaffNote.admission_id == admission.id,
        )
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Staff note not found")
    return _note_to_out(note, patient_id)


__all__ = ["router"]
