"""Doctor Dashboard endpoints — aggregate stats and lists for the landing page."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.functions import count, max as sql_max
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.database import get_db
from src.core.redis_client import cache_get, cache_set
from src.core.security import require_roles
from src.domains.patients.enums import PatientStatus
from src.domains.patients.models import Admission, ClinicalUpdate, EmergencyFlag
from src.domains.patients.schemas import (
    ActivityFeedItemOut,
    CriticalPatientOut,
    DashboardSummaryOut,
    NeedsAttentionPatientOut,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)

# Cache TTL for the summary card values (seconds).  30 s is short enough that
# the numbers feel live but long enough to absorb dashboard poll loops.
_SUMMARY_CACHE_TTL = 30


# Helpers


def _today_start() -> datetime:
    """Return midnight UTC of the current day."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _admission_scope(current_user: User):
    """Scope dashboard queries to a doctor's panel or to hospital-wide clinical views."""
    if current_user.role == UserRole.doctor:
        return [
            Admission.hospital_id == current_user.hospital_id,
            Admission.primary_doctor_id == current_user.id,
        ]
    return [Admission.hospital_id == current_user.hospital_id]


# Endpoints


@router.get("/summary", response_model=DashboardSummaryOut)
async def dashboard_summary(
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the four stat-card values shown on the doctor dashboard:
    my_patients, critical_count, updates_today, needs_attention.

    Results are cached in Redis for 30 s per user to absorb poll loops.
    """
    if current_user.hospital_id is None:
        return DashboardSummaryOut(
            my_patients=0, critical_count=0, updates_today=0, needs_attention=0
        )

    cache_key = f"dashboard:summary:{current_user.id}"
    cached = await cache_get(cache_key)
    if cached:
        return DashboardSummaryOut(**cached)

    scope = _admission_scope(current_user)

    # 1) My Patients — active admissions in scope
    my_patients_q = (
        select(count())
        .select_from(Admission)
        .where(*scope, Admission.discharged_at.is_(None))
    )

    # 2) Critical Count — active admissions with critical status or unresolved flag
    critical_q = (
        select(count(func.distinct(Admission.id)))
        .select_from(Admission)
        .outerjoin(
            EmergencyFlag,
            and_(
                EmergencyFlag.admission_id == Admission.id,
                EmergencyFlag.is_resolved.is_(False),
            ),
        )
        .where(
            *scope,
            Admission.discharged_at.is_(None),
            or_(
                Admission.status == PatientStatus.critical,
                EmergencyFlag.id.is_not(None),
            ),
        )
    )

    # 3) Updates Today — clinical updates since midnight UTC
    today = _today_start()
    updates_q = (
        select(count())
        .select_from(ClinicalUpdate)
        .join(Admission, ClinicalUpdate.admission_id == Admission.id)
        .where(*scope, ClinicalUpdate.created_at >= today)
    )

    # 4) Needs Attention — active admissions with no update in >12 hours
    threshold = datetime.now(timezone.utc) - timedelta(hours=12)
    latest_update = (
        select(
            ClinicalUpdate.admission_id,
            sql_max(ClinicalUpdate.created_at).label("last_update"),
        )
        .group_by(ClinicalUpdate.admission_id)
        .subquery()
    )
    needs_q = (
        select(count())
        .select_from(Admission)
        .outerjoin(latest_update, Admission.id == latest_update.c.admission_id)
        .where(
            *scope,
            Admission.discharged_at.is_(None),
            (latest_update.c.last_update < threshold)
            | (latest_update.c.last_update.is_(None)),
        )
    )

    # [Performance]: All four queries are independent — run them concurrently.
    r1, r2, r3, r4 = await asyncio.gather(
        db.execute(my_patients_q),
        db.execute(critical_q),
        db.execute(updates_q),
        db.execute(needs_q),
    )

    summary = DashboardSummaryOut(
        my_patients=r1.scalar() or 0,
        critical_count=r2.scalar() or 0,
        updates_today=r3.scalar() or 0,
        needs_attention=r4.scalar() or 0,
    )

    await cache_set(cache_key, summary.model_dump(), ttl=_SUMMARY_CACHE_TTL)
    return summary


@router.get("/critical-patients", response_model=List[CriticalPatientOut])
async def critical_patients(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Patients with critical status or active emergency flags."""
    if current_user.hospital_id is None:
        return []

    scope = _admission_scope(current_user)
    result = await db.execute(
        select(Admission)
        .outerjoin(
            EmergencyFlag,
            and_(
                EmergencyFlag.admission_id == Admission.id,
                EmergencyFlag.is_resolved.is_(False),
            ),
        )
        .options(selectinload(Admission.patient))
        .where(
            *scope,
            Admission.discharged_at.is_(None),
            or_(
                Admission.status == PatientStatus.critical,
                EmergencyFlag.id.is_not(None),
            ),
        )
        .order_by(Admission.admitted_at.desc())
        .distinct()
        .limit(limit)
    )
    admissions = result.scalars().all()
    return [
        CriticalPatientOut(
            id=a.patient_id,
            full_name=a.patient.full_name,
            ward=a.ward,
            bed_number=a.bed_number,
            status=a.status,
            diagnosis=a.diagnosis,
        )
        for a in admissions
    ]


@router.get("/needs-attention", response_model=List[NeedsAttentionPatientOut])
async def list_needs_attention(
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Patients whose status hasn't been updated in more than 12 hours."""
    if current_user.hospital_id is None:
        return []

    scope = _admission_scope(current_user)
    threshold = datetime.now(timezone.utc) - timedelta(hours=12)
    latest_update = (
        select(
            ClinicalUpdate.admission_id,
            sql_max(ClinicalUpdate.created_at).label("last_update"),
        )
        .group_by(ClinicalUpdate.admission_id)
        .subquery()
    )
    result = await db.execute(
        select(Admission, latest_update.c.last_update)
        .options(selectinload(Admission.patient))
        .outerjoin(latest_update, Admission.id == latest_update.c.admission_id)
        .where(
            *scope,
            Admission.discharged_at.is_(None),
            (latest_update.c.last_update < threshold)
            | (latest_update.c.last_update.is_(None)),
        )
        .limit(limit)
    )
    now = datetime.now(timezone.utc)
    rows = result.all()
    return [
        NeedsAttentionPatientOut(
            id=admission.patient_id,
            full_name=admission.patient.full_name,
            ward=admission.ward,
            bed_number=admission.bed_number,
            last_update_at=last_update,
            hours_since_update=(
                round((now - last_update).total_seconds() / 3600, 1)
                if last_update
                else None
            ),
        )
        for admission, last_update in rows
    ]


@router.get("/recent-activity", response_model=List[ActivityFeedItemOut])
async def recent_activity(
    limit: int = Query(20, ge=1, le=50),
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """Most recent clinical updates across the hospital (activity feed)."""
    if current_user.hospital_id is None:
        return []

    scope = _admission_scope(current_user)
    result = await db.execute(
        select(ClinicalUpdate)
        .join(Admission, ClinicalUpdate.admission_id == Admission.id)
        .options(
            selectinload(ClinicalUpdate.authored_by),
            selectinload(ClinicalUpdate.admission).selectinload(Admission.patient),
        )
        .where(*scope)
        .order_by(ClinicalUpdate.created_at.desc())
        .limit(limit)
    )
    updates = result.scalars().all()
    return [
        ActivityFeedItemOut(
            id=u.id,
            author_name=(u.authored_by.full_name if u.authored_by else "System"),
            author_role=(u.authored_by.role if u.authored_by else UserRole.admin),
            action=u.note,
            timestamp=u.created_at,
        )
        for u in updates
    ]


__all__ = ["router"]
