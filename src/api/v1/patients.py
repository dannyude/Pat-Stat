"""Patient CRUD endpoints.

Handles patient creation, listing, retrieval, and updates.
Clinical update endpoints are in ``clinical_updates.py``.
Shared helpers live in ``patient_helpers.py``.
"""

from datetime import date, datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.security import get_current_user, require_roles
from src.domains.assignments.models import CareAssignment
from src.domains.patients.enums import PatientStatus
from src.domains.patients.models import Admission, EmergencyFlag, FamilyPatientLink, Patient
from src.domains.patients.schemas import (
    NoteCategoryCatalogOut,
    PatientCreate,
    PatientResponse,
    PatientUpdate,
)
from src.domains.patients.services import get_note_category_catalog
from src.domains.users.enums import UserRole
from src.domains.users.models import User

from src.api.v1.patient_helpers import (
    admission_to_patient_out,
    generate_pat_stat_id,
    get_active_admission,
    sync_nurse_assignments,
    validate_primary_doctor,
)

router = APIRouter(prefix="/patients", tags=["Patients"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)
_doctor_or_admin = require_roles(UserRole.admin, UserRole.doctor)


@router.get("", response_model=List[PatientResponse])
async def list_patients(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    active_only: bool = True,
    q: str | None = Query(
        None, description="Search by name, pat_stat_id, ward, or diagnosis"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all patients with their admission details.

    - Filters by ``active_only`` by default, returning patients who have not been discharged.
    - Supports text search via ``q`` parameter (matches name, pat_stat_id, ward, diagnosis).
    - **Clinical Staff**: Can see all patients in the system.
    - **Family Members**: Can only see patients they are linked to.
    """
    # [Performance/DB]: Eager load relationships right away so that transforming
    # the SQLAlchemy ORM objects into Pydantic models does not cause N+1 query issues.
    q_stmt = select(Admission).options(
        selectinload(Admission.patient),
        selectinload(Admission.primary_doctor),
        selectinload(Admission.care_assignments).selectinload(CareAssignment.staff),
    )
    if active_only:
        q_stmt = q_stmt.where(Admission.discharged_at.is_(None))

    # [Security/Defense-in-depth]: Each PatStat instance serves one hospital, but
    # we still scope queries to current_user.hospital_id so that a misconfigured
    # account (e.g. a super_admin with no hospital) can never accidentally see data.
    if current_user.role != UserRole.family:
        if current_user.hospital_id is None:
            return []
        q_stmt = q_stmt.where(Admission.hospital_id == current_user.hospital_id)

    if current_user.role == UserRole.family:
        linked = await db.execute(
            select(FamilyPatientLink.patient_id).where(
                FamilyPatientLink.family_user_id == current_user.id
            )
        )
        patient_ids = [row[0] for row in linked.all()]
        if not patient_ids:
            return []
        q_stmt = q_stmt.where(Admission.patient_id.in_(patient_ids))

    if q:
        search_term = f"%{q}%"
        # [DB/Logic]: Use a subquery to find matching patient IDs.
        # This explicitly avoids a 'MissingGreenlet' error that happens when
        # mixing an explicit JOIN (to filter by patient name) and a selectinload
        # on the exact same relationship (Admission.patient).
        matching_patient_ids = (
            select(Patient.id)
            .where(
                Patient.full_name.ilike(search_term)
                | Patient.pat_stat_id.ilike(search_term)
            )
            .scalar_subquery()
        )
        q_stmt = q_stmt.where(
            Admission.patient_id.in_(matching_patient_ids)
            | Admission.ward.ilike(search_term)
            | Admission.diagnosis.ilike(search_term)
        )

    # [DB]: Execute the final query with pagination (offset/limit)
    result = await db.execute(q_stmt.offset(skip).limit(limit))
    admissions = result.scalars().all()
    # Serialize DB models to response schema DTOs
    return [admission_to_patient_out(a) for a in admissions]


@router.post("", response_model=PatientResponse, status_code=201)
async def create_patient(
    body: PatientCreate,
    current_user: User = Depends(_doctor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new patient and their initial admission record.
    Both the global Patient profile and the hospital-specific Admission
    are created atomically in one transaction.
    """
    if current_user.hospital_id is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Current user is not assigned to a hospital. "
                "Assign a hospital before creating admissions."
            ),
        )

    date_of_birth = body.date_of_birth
    if date_of_birth is None and body.age is not None and body.age >= 0:
        date_of_birth = date(
            max(1900, datetime.now(timezone.utc).year - body.age), 1, 1
        )

    # [Logic]: Validate referential integrity BEFORE writing anything to DB.
    # This avoids flushing rows that would then be rolled back on a 400 error.
    await validate_primary_doctor(
        doctor_id=body.primary_doctor_id,
        hospital_id=current_user.hospital_id,
        db=db,
    )

    patient = Patient(
        pat_stat_id=body.pat_stat_id or generate_pat_stat_id(),
        national_id=body.national_id,
        full_name=body.full_name,
        date_of_birth=date_of_birth,
        gender=body.gender,
        phone=body.phone,
        email=body.email,
        address=body.address,
        blood_group=body.blood_group,
        allergies=body.allergies,
        chronic_conditions=body.chronic_conditions,
        emergency_contact_name=body.emergency_contact_name,
        emergency_contact_phone=body.emergency_contact_phone,
        registered_by_hospital_id=current_user.hospital_id,
    )
    db.add(patient)
    await db.flush()  # generate patient.id for the admission FK

    admission = Admission(
        patient_id=patient.id,
        hospital_id=current_user.hospital_id,
        ward=body.ward,
        bed_number=body.bed_number,
        diagnosis=body.diagnosis,
        status=body.status,
        primary_doctor_id=body.primary_doctor_id,
    )
    db.add(admission)
    await db.flush()  # generate admission.id for nurse assignment FKs

    await sync_nurse_assignments(
        admission=admission,
        nurse_ids=body.assigned_nurse_ids,
        hospital_id=current_user.hospital_id,
        db=db,
    )

    # [Async/SQLAlchemy]: Re-fetch with selectinload to avoid MissingGreenlet
    # when admission_to_patient_out accesses relationship attributes synchronously.
    result = await db.execute(
        select(Admission)
        .options(
            selectinload(Admission.patient),
            selectinload(Admission.primary_doctor),
            selectinload(Admission.care_assignments).selectinload(CareAssignment.staff),
        )
        .where(Admission.id == admission.id)
    )
    return admission_to_patient_out(result.scalar_one())


@router.post("/{patient_id}/discharge", response_model=PatientResponse)
async def discharge_patient(
    patient_id: str,
    current_user: User = Depends(_doctor_or_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Discharge a patient from their active admission.

    - Sets ``discharged_at`` to the current UTC time.
    - Sets ``status`` to ``discharged``.
    - Resolves any open emergency flags on the admission so the dashboard
      no longer counts them as active.
    """
    admission = await get_active_admission(
        patient_id, db, hospital_id=current_user.hospital_id
    )

    now = datetime.now(timezone.utc)
    admission.discharged_at = now
    admission.status = PatientStatus.discharged

    # Resolve any unresolved emergency flags on this admission.
    await db.execute(
        sa_update(EmergencyFlag)
        .where(
            EmergencyFlag.admission_id == admission.id,
            EmergencyFlag.is_resolved.is_(False),
        )
        .values(is_resolved=True, resolved_by_id=current_user.id, resolved_at=now)
    )

    await db.flush()
    return admission_to_patient_out(admission)


@router.get("/note-categories", response_model=NoteCategoryCatalogOut)
async def list_note_categories(
    _current_user: User = Depends(_clinical_staff),  # noqa: ARG001 — enforces auth
):
    """Return staff note category metadata for doctor/nurse/admin clients."""
    return get_note_category_catalog()


@router.get("/{patient_id}", response_model=PatientResponse)
async def get_patient(
    patient_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve a single patient's active admission details."""
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)
    return admission_to_patient_out(admission)


@router.patch("/{patient_id}", response_model=PatientResponse)
async def update_patient(
    patient_id: str,
    body: PatientUpdate,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing patient's admission and profile information."""
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)

    if body.full_name is not None:
        admission.patient.full_name = body.full_name
    if body.date_of_birth is not None:
        admission.patient.date_of_birth = body.date_of_birth
    if body.gender is not None:
        admission.patient.gender = body.gender
    if body.national_id is not None:
        admission.patient.national_id = body.national_id
    if body.phone is not None:
        admission.patient.phone = body.phone
    if body.email is not None:
        admission.patient.email = body.email
    if body.address is not None:
        admission.patient.address = body.address
    if body.blood_group is not None:
        admission.patient.blood_group = body.blood_group
    if body.allergies is not None:
        admission.patient.allergies = body.allergies
    if body.chronic_conditions is not None:
        admission.patient.chronic_conditions = body.chronic_conditions
    if body.emergency_contact_name is not None:
        admission.patient.emergency_contact_name = body.emergency_contact_name
    if body.emergency_contact_phone is not None:
        admission.patient.emergency_contact_phone = body.emergency_contact_phone
    if body.ward is not None:
        admission.ward = body.ward
    if body.bed_number is not None:
        admission.bed_number = body.bed_number
    if body.diagnosis is not None:
        admission.diagnosis = body.diagnosis
    if body.status is not None:
        admission.status = body.status
    if body.is_active is not None:
        admission.patient.is_active = body.is_active

    if body.primary_doctor_id is not None:
        await validate_primary_doctor(
            doctor_id=body.primary_doctor_id,
            hospital_id=admission.hospital_id,
            db=db,
        )
        admission.primary_doctor_id = body.primary_doctor_id

    if body.assigned_nurse_ids is not None:
        await sync_nurse_assignments(
            admission=admission,
            nurse_ids=body.assigned_nurse_ids,
            hospital_id=admission.hospital_id,
            db=db,
        )
    return admission_to_patient_out(admission)


__all__ = ["router"]
