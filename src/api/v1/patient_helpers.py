"""Shared helper functions used by patient-related API routes.

This module consolidates authorization checks, validation logic, and
response-shaping utilities that are used by both the patient CRUD and
clinical update routes.
"""

from datetime import date, datetime, timezone
from typing import List
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domains.assignments.models import CareAssignment
from src.domains.patients.models import Admission, ClinicalUpdate
from src.domains.patients.schemas import (
    CareTeamMemberOut,
    ClinicalUpdateOut,
    PatientOut,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User


async def get_active_admission(
    patient_id: str,
    db: AsyncSession,
    hospital_id: str | None = None,
) -> Admission:
    """
    Fetches the most recent, active admission for a given patient ID.

    Args:
        patient_id:  The patient's UUID.
        db:          The database session.
        hospital_id: When supplied, the query is scoped to this hospital
                     (defense-in-depth — prevents a misconfigured user from
                     reading a patient that doesn't belong to their hospital).

    Returns:
        The active Admission object.

    Raises:
        404 if no active admission exists (or if the hospital scope doesn't match).
    """
    # [DB/Logic]: Eager load patient, primary doctor, and care team to prevent N+1 downstream.
    stmt = (
        select(Admission)
        .options(
            selectinload(Admission.patient),
            selectinload(Admission.primary_doctor),
            selectinload(Admission.care_assignments).selectinload(CareAssignment.staff),
        )
        .where(
            Admission.patient_id == patient_id,
            Admission.discharged_at.is_(None),
        )
    )
    if hospital_id is not None:
        stmt = stmt.where(Admission.hospital_id == hospital_id)

    result = await db.execute(
        # Order by admitted_at desc in case a data anomaly created two active admissions
        stmt.order_by(Admission.admitted_at.desc()).limit(1)
    )
    admission = result.scalar_one_or_none()
    if not admission:
        raise HTTPException(
            status_code=404, detail="Patient or active admission not found"
        )
    return admission


async def validate_primary_doctor(
    doctor_id: str | None,
    hospital_id: str,
    db: AsyncSession,
) -> None:
    """
    Validates that a given user ID corresponds to an active doctor in the
    specified hospital. Raises 400 if invalid.
    """
    if doctor_id is None:
        return

    result = await db.execute(
        select(User).where(
            User.id == doctor_id,
            User.role == UserRole.doctor,
            User.is_active.is_(True),
            User.hospital_id == hospital_id,
        )
    )
    doctor = result.scalar_one_or_none()
    if not doctor:
        raise HTTPException(status_code=400, detail="Invalid primary doctor")


async def sync_nurse_assignments(
    admission: Admission,
    nurse_ids: List[str],
    hospital_id: str,
    db: AsyncSession,
) -> None:
    """
    Synchronizes the set of nurses assigned to a patient's admission.
    Handles un-assigning old nurses and assigning new ones.
    """
    unique_nurse_ids = list(dict.fromkeys(nurse_ids))

    if unique_nurse_ids:
        result = await db.execute(
            select(User).where(
                User.id.in_(unique_nurse_ids),
                User.role == UserRole.nurse,
                User.is_active.is_(True),
                User.hospital_id == hospital_id,
            )
        )
        nurses = result.scalars().all()
        # [Validation]: If the count doesn't match, one of the provided IDs isn't a valid, active nurse at this hospital.
        if len(nurses) != len(unique_nurse_ids):
            raise HTTPException(status_code=400, detail="Invalid assigned nurse list")

    # [Async/SQLAlchemy]: Query care assignments directly to avoid lazy-load
    # MissingGreenlet errors in async context. Relationship access on a
    # just-flushed object triggers an implicit SELECT that fails outside greenlet.
    ca_result = await db.execute(
        select(CareAssignment)
        .options(selectinload(CareAssignment.staff))
        .where(CareAssignment.admission_id == admission.id)
    )
    existing_assignments = ca_result.scalars().all()

    existing_by_staff_id = {
        ca.staff_id: ca for ca in existing_assignments if ca.staff_id is not None
    }
    active_nurse_ids = {
        ca.staff_id
        for ca in existing_assignments
        if ca.staff is not None
        and ca.staff.role == UserRole.nurse
        and ca.unassigned_at is None
    }
    target_nurse_ids = set(unique_nurse_ids)
    now = datetime.now(timezone.utc)

    # [DB/Logic]: Soft-delete (unassign) any currently active nurse who is no longer in the target list.
    for nurse_id in active_nurse_ids - target_nurse_ids:
        assignment = existing_by_staff_id[nurse_id]
        assignment.unassigned_at = now

    # [DB/Logic]: Add newly assigned nurses, or reactivate previously unassigned ones.
    for nurse_id in target_nurse_ids:
        assignment = existing_by_staff_id.get(nurse_id)
        if assignment is None:
            # Create a brand new assignment
            db.add(
                CareAssignment(
                    admission_id=admission.id,
                    staff_id=nurse_id,
                    is_primary=False,
                )
            )
            continue
        # Reactivate historically assigned nurse
        assignment.unassigned_at = None


def assigned_nurse_members(admission: Admission) -> List[CareTeamMemberOut]:
    """Build the active nurse list used by patient response payloads."""
    return [
        CareTeamMemberOut(
            staff_id=ca.staff_id,
            full_name=ca.staff.full_name,
            role=ca.staff.role,
            email=ca.staff.email,
            is_primary=ca.is_primary,
            assigned_at=ca.assigned_at,
        )
        for ca in admission.care_assignments
        if ca.staff is not None
        and ca.staff.role == UserRole.nurse
        and ca.unassigned_at is None
    ]


def _compute_age(dob: date | None) -> int | None:
    """Return whole years between dob and today, or None if dob is unknown."""
    if dob is None:
        return None
    today = date.today()
    return (
        today.year
        - dob.year
        - ((today.month, today.day) < (dob.month, dob.day))
    )


def admission_to_patient_out(admission: Admission) -> PatientOut:
    """Map an Admission ORM model into the patient API response schema."""
    nurses = assigned_nurse_members(admission)
    primary_doctor = (
        CareTeamMemberOut(
            staff_id=admission.primary_doctor.id,
            full_name=admission.primary_doctor.full_name,
            role=admission.primary_doctor.role,
            email=admission.primary_doctor.email,
            is_primary=True,
            assigned_at=admission.admitted_at,
        )
        if admission.primary_doctor is not None
        else None
    )
    return PatientOut(
        id=admission.patient_id,
        pat_stat_id=admission.patient.pat_stat_id,
        national_id=admission.patient.national_id,
        full_name=admission.patient.full_name,
        age=_compute_age(admission.patient.date_of_birth),
        date_of_birth=admission.patient.date_of_birth,
        gender=admission.patient.gender,
        phone=admission.patient.phone,
        email=admission.patient.email,
        address=admission.patient.address,
        blood_group=admission.patient.blood_group,
        allergies=admission.patient.allergies,
        chronic_conditions=admission.patient.chronic_conditions,
        emergency_contact_name=admission.patient.emergency_contact_name,
        emergency_contact_phone=admission.patient.emergency_contact_phone,
        ward=admission.ward,
        bed_number=admission.bed_number,
        diagnosis=admission.diagnosis,
        status=admission.status,
        primary_doctor_id=admission.primary_doctor_id,
        primary_doctor_name=(
            admission.primary_doctor.full_name if admission.primary_doctor else None
        ),
        primary_doctor=primary_doctor,
        assigned_nurse_ids=[member.staff_id for member in nurses],
        assigned_nurses=nurses,
        assigned_nurses_count=len(nurses),
        admitted_at=admission.admitted_at,
        discharged_at=admission.discharged_at,
        is_active=admission.patient.is_active,
        created_at=admission.patient.created_at,
    )


def clinical_update_out(update: ClinicalUpdate, patient_id: str) -> ClinicalUpdateOut:
    """Map a ClinicalUpdate ORM row into the API response schema."""
    return ClinicalUpdateOut(
        id=update.id,
        patient_id=patient_id,
        status=update.status,
        note=update.note,
        blood_pressure=update.blood_pressure,
        heart_rate=update.heart_rate,
        temperature=update.temperature,
        oxygen_level=update.oxygen_level,
        created_at=update.created_at,
    )


def generate_pat_stat_id() -> str:
    """Generate a compact, human-readable patient ID unique within this instance."""
    year = datetime.now(timezone.utc).year
    suffix = uuid.uuid4().hex[:6].upper()
    return f"PS-{year}-{suffix}"
