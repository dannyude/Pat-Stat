"""Family read-model endpoints for dashboard, overview, and updates."""

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.family.schemas import (
    FamilyCareTeamOut,
    FamilyLatestUpdateOut,
    FamilyPatientMobileDashboardOut,
    FamilyPatientOverviewOut,
    FamilyPatientOut,
)
from src.domains.family.services import (
    get_family_patient_overview_admission,
    list_family_patient_updates,
    list_family_user_active_admissions,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/family", tags=["Family"])

_family_only = require_roles(UserRole.family)


def _compute_age(date_of_birth: datetime | None) -> int | None:
    # [Logic]: Calculate precise age based on whether the patient has had their
    # birthday yet this calendar year.
    if date_of_birth is None:
        return None
    today = datetime.now(timezone.utc).date()
    dob = date_of_birth.date()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


@router.get("/me/patients", response_model=List[FamilyPatientOut])
async def list_my_family_patients(
    current_user: User = Depends(_family_only),
    db: AsyncSession = Depends(get_db),
):
    admissions = await list_family_user_active_admissions(current_user.id, db)
    return [
        FamilyPatientOut(
            patient_id=a.patient_id,
            full_name=a.patient.full_name,
            status=a.status.value,
            diagnosis=a.diagnosis,
            ward=a.ward,
            bed_number=a.bed_number,
            admitted_at=a.admitted_at,
        )
        for a in admissions
    ]


@router.get(
    "/me/patients/{patient_id}/overview", response_model=FamilyPatientOverviewOut
)
async def get_my_patient_overview(
    patient_id: str,
    current_user: User = Depends(_family_only),
    db: AsyncSession = Depends(get_db),
):
    admission = await get_family_patient_overview_admission(
        family_user_id=current_user.id,
        patient_id=patient_id,
        db=db,
    )
    nurse_names = [
        ca.staff.full_name
        for ca in admission.care_assignments
        if ca.staff is not None
        and ca.staff.role == UserRole.nurse
        and ca.unassigned_at is None
    ]

    latest_update = next(iter(admission.updates), None)
    latest_update_out = (
        FamilyLatestUpdateOut(
            id=latest_update.id,
            status=latest_update.status.value,
            note=latest_update.note,
            created_at=latest_update.created_at,
            authored_by_name=(
                latest_update.authored_by.full_name
                if latest_update.authored_by is not None
                else None
            ),
        )
        if latest_update is not None
        else None
    )

    return FamilyPatientOverviewOut(
        patient_id=admission.patient_id,
        full_name=admission.patient.full_name,
        age=_compute_age(admission.patient.date_of_birth),
        gender=admission.patient.gender,
        status=admission.status.value,
        diagnosis=admission.diagnosis,
        ward=admission.ward,
        bed_number=admission.bed_number,
        admitted_at=admission.admitted_at,
        care_team=FamilyCareTeamOut(
            doctor_name=(
                admission.primary_doctor.full_name
                if admission.primary_doctor is not None
                else None
            ),
            nurse_names=nurse_names,
        ),
        latest_update=latest_update_out,
    )


@router.get(
    "/me/patients/{patient_id}/updates", response_model=List[FamilyLatestUpdateOut]
)
async def get_my_patient_updates(
    patient_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_family_only),
    db: AsyncSession = Depends(get_db),
):
    updates = await list_family_patient_updates(
        family_user_id=current_user.id,
        patient_id=patient_id,
        skip=skip,
        limit=limit,
        db=db,
    )
    return [
        FamilyLatestUpdateOut(
            id=u.id,
            status=u.status.value,
            note=u.note,
            created_at=u.created_at,
            authored_by_name=(
                u.authored_by.full_name if u.authored_by is not None else None
            ),
        )
        for u in updates
    ]


@router.get(
    "/me/patients/{patient_id}/mobile-dashboard",
    response_model=FamilyPatientMobileDashboardOut,
)
async def get_my_patient_mobile_dashboard(
    patient_id: str,
    updates_limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(_family_only),
    db: AsyncSession = Depends(get_db),
):
    # [Performance/Mobile Optimization]: The mobile app needs both the patient overview
    # and the most recent N updates to render the entire dashboard in a single screen.
    # We combine them here so the mobile client doesn't need to make two round trips.
    admission = await get_family_patient_overview_admission(
        family_user_id=current_user.id,
        patient_id=patient_id,
        db=db,
    )

    nurse_names = [
        ca.staff.full_name
        for ca in admission.care_assignments
        if ca.staff is not None
        and ca.staff.role == UserRole.nurse
        and ca.unassigned_at is None
    ]

    latest_update = next(iter(admission.updates), None)
    latest_update_out = (
        FamilyLatestUpdateOut(
            id=latest_update.id,
            status=latest_update.status.value,
            note=latest_update.note,
            created_at=latest_update.created_at,
            authored_by_name=(
                latest_update.authored_by.full_name
                if latest_update.authored_by is not None
                else None
            ),
        )
        if latest_update is not None
        else None
    )

    overview = FamilyPatientOverviewOut(
        patient_id=admission.patient_id,
        full_name=admission.patient.full_name,
        age=_compute_age(admission.patient.date_of_birth),
        gender=admission.patient.gender,
        status=admission.status.value,
        diagnosis=admission.diagnosis,
        ward=admission.ward,
        bed_number=admission.bed_number,
        admitted_at=admission.admitted_at,
        care_team=FamilyCareTeamOut(
            doctor_name=(
                admission.primary_doctor.full_name
                if admission.primary_doctor is not None
                else None
            ),
            nurse_names=nurse_names,
        ),
        latest_update=latest_update_out,
    )

    updates = await list_family_patient_updates(
        family_user_id=current_user.id,
        patient_id=patient_id,
        skip=0,
        limit=updates_limit,
        db=db,
    )
    return FamilyPatientMobileDashboardOut(
        overview=overview,
        updates=[
            FamilyLatestUpdateOut(
                id=u.id,
                status=u.status.value,
                note=u.note,
                created_at=u.created_at,
                authored_by_name=(
                    u.authored_by.full_name if u.authored_by is not None else None
                ),
            )
            for u in updates
        ],
    )


__all__ = ["router"]
