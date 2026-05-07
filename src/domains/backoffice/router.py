"""Backoffice router for internal Pat-Stat platform endpoints."""

import csv
import io
from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import require_super_admin
from src.domains.backoffice.schemas import (
    BackofficeOverviewOut,
    DocumentOut,
    HospitalActionRequest,
    HospitalVerificationEventOut,
    OnboardingTrendsOut,
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    SuperAdminCreateRequest,
    SuperAdminOut,
    SuperAdminActionOut,
    SuperAdminStatusUpdate,
)
from src.domains.backoffice.services import (
    create_super_admin,
    get_onboarding_trends,
    get_platform_overview,
    get_platform_settings,
    list_hospital_documents,
    list_hospital_verification_events,
    list_super_admin_actions,
    list_super_admins,
    toggle_super_admin_status,
    update_platform_settings,
)
from src.domains.hospital import services as hospital_services
from src.domains.hospital.schemas import (
    AllHospitalsResponse,
    HospitalDetailOut,
    HospitalOut,
    PendingHospitalsResponse,
)
from src.domains.users.models import User

router = APIRouter(prefix="/backoffice", tags=["Backoffice"])


# ── Overview ────────────────────────────────────────────────────────────────────


@router.get("/overview", response_model=BackofficeOverviewOut)
async def overview(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return platform-wide metrics for super-admin dashboards."""
    return await get_platform_overview(db)


@router.get("/overview/trends", response_model=OnboardingTrendsOut)
async def overview_trends(
    weeks: int = Query(4, ge=1, le=52),
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return weekly hospital onboarding activity for the last ``weeks`` weeks.

    Used by the super-admin dashboard's "Onboarding trends" chart. Buckets
    are anchored to Monday (ISO week start) in UTC and always contiguous —
    weeks with no activity still appear with zero counts so the UI can draw
    without gaps.
    """
    return await get_onboarding_trends(db, weeks=weeks)


# ── Hospital Management ─────────────────────────────────────────────────────────


@router.get("/hospitals", response_model=AllHospitalsResponse)
async def list_all_hospitals(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
    search: str | None = Query(None),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List all hospitals with optional search, status filter, and pagination."""
    return await hospital_services.list_all_hospitals(
        db, search=search, status_filter=status, page=page, page_size=page_size
    )


@router.get("/hospitals/pending", response_model=PendingHospitalsResponse)
async def list_pending_hospitals(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List pending hospital applications for internal review."""
    return await hospital_services.list_pending_hospitals(
        db, page=page, page_size=page_size
    )


@router.get("/hospitals/{hospital_id}", response_model=HospitalDetailOut)
async def get_hospital_detail(
    hospital_id: str,
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return full hospital detail including admin contact and application data."""
    return await hospital_services.get_hospital_detail(db, hospital_id=hospital_id)


@router.post("/hospitals/{hospital_id}/approve", response_model=HospitalOut)
async def approve_hospital(
    hospital_id: str,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve a hospital application from backoffice."""
    hospital = await hospital_services.approve_hospital_record(
        hospital_id=hospital_id,
        super_admin_id=current_user.id,
        db=db,
    )
    await db.commit()
    return hospital


@router.post("/hospitals/{hospital_id}/reject", response_model=HospitalOut)
async def reject_hospital(
    hospital_id: str,
    body: HospitalActionRequest | None = None,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reject a hospital application."""
    hospital = await hospital_services.reject_hospital_record(
        hospital_id=hospital_id,
        super_admin_id=current_user.id,
        reason=body.reason if body else None,
        note=body.note if body else None,
        db=db,
    )
    await db.commit()
    return hospital


@router.post("/hospitals/{hospital_id}/suspend", response_model=HospitalOut)
async def suspend_hospital(
    hospital_id: str,
    body: HospitalActionRequest | None = None,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Suspend an active hospital's access."""
    hospital = await hospital_services.suspend_hospital_record(
        hospital_id=hospital_id,
        super_admin_id=current_user.id,
        reason=body.reason if body else None,
        note=body.note if body else None,
        db=db,
    )
    await db.commit()
    return hospital


@router.post("/hospitals/{hospital_id}/reinstate", response_model=HospitalOut)
async def reinstate_hospital(
    hospital_id: str,
    body: HospitalActionRequest | None = None,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reinstate a suspended hospital."""
    hospital = await hospital_services.reinstate_hospital_record(
        hospital_id=hospital_id,
        super_admin_id=current_user.id,
        note=body.note if body else None,
        db=db,
    )
    await db.commit()
    return hospital


@router.get(
    "/hospitals/{hospital_id}/timeline",
    response_model=list[HospitalVerificationEventOut],
)
async def hospital_timeline(
    hospital_id: str,
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=300),
):
    """Return verification events for a single hospital."""
    return await list_hospital_verification_events(
        db, hospital_id=hospital_id, limit=limit
    )


@router.get(
    "/hospitals/{hospital_id}/documents",
    response_model=list[DocumentOut],
)
async def hospital_documents(
    hospital_id: str,
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return uploaded documents for a hospital's application."""
    return await list_hospital_documents(db, hospital_id=hospital_id)


# ── Audit Log ────────────────────────────────────────────────────────────────────


@router.get("/audit-log", response_model=list[SuperAdminActionOut])
async def audit_log(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=300),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    action: str | None = Query(None),
):
    """Return latest global super-admin action logs with optional filters."""
    return await list_super_admin_actions(
        db, limit=limit, date_from=date_from, date_to=date_to, action_filter=action
    )


# [Perf]: CSV exports can include far more rows than the paginated UI view.
# Cap at 10k to keep this endpoint responsive while still supporting typical
# compliance/export workflows. A dedicated background-export job is the right
# solution if we ever need larger exports.
_AUDIT_LOG_EXPORT_MAX: int = 10_000


def _iter_audit_log_csv(rows: list[SuperAdminActionOut]) -> Iterator[str]:
    """Yield CSV chunks for the audit-log export stream.

    Using StringIO + truncate/seek lets us write each row with the stdlib
    ``csv`` module (proper quoting) without buffering the entire file in
    memory — important when exporting up to 10k rows.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    header = [
        "id",
        "created_at",
        "actor_id",
        "actor_name",
        "action",
        "target_type",
        "target_id",
        "ip_address",
        "note",
        "metadata",
    ]
    writer.writerow(header)
    yield buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    for row in rows:
        writer.writerow(
            [
                str(row.id),
                row.created_at.isoformat() if row.created_at else "",
                str(row.actor_id) if row.actor_id else "",
                row.actor_name or "",
                row.action,
                row.target_type or "",
                str(row.target_id) if row.target_id else "",
                row.ip_address or "",
                row.note or "",
                # JSON-ish string; csv writer will quote commas/quotes for us.
                str(row.action_metadata) if row.action_metadata else "",
            ]
        )
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


@router.get("/audit-log/export")
async def export_audit_log(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    action: str | None = Query(None),
    limit: int = Query(_AUDIT_LOG_EXPORT_MAX, ge=1, le=_AUDIT_LOG_EXPORT_MAX),
):
    """Stream super-admin audit log entries as a CSV download.

    Supports the same filters as ``GET /backoffice/audit-log``. The ``limit``
    default is the max so a naive caller gets the full export; explicit
    ``date_from`` / ``date_to`` can be used to scope the export to a period.
    """
    rows = await list_super_admin_actions(
        db,
        limit=limit,
        date_from=date_from,
        date_to=date_to,
        action_filter=action,
    )

    filename = f"audit-log-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        _iter_audit_log_csv(rows),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Super Admin Management ───────────────────────────────────────────────────────


@router.get("/super-admins", response_model=list[SuperAdminOut])
async def list_platform_super_admins(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all super-admin accounts."""
    return await list_super_admins(db)


@router.post("/super-admins", response_model=SuperAdminOut, status_code=201)
async def create_platform_super_admin(
    body: SuperAdminCreateRequest,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new super-admin account (hard limit enforced at service layer)."""
    user = await create_super_admin(db=db, actor_user_id=current_user.id, payload=body)
    await db.commit()
    return user


@router.patch(
    "/super-admins/{user_id}/status",
    response_model=SuperAdminOut,
)
async def update_super_admin_status(
    user_id: str,
    body: SuperAdminStatusUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Activate or deactivate a super-admin account."""
    result = await toggle_super_admin_status(
        db=db,
        target_user_id=user_id,
        is_active=body.is_active,
        actor_user_id=current_user.id,
    )
    await db.commit()
    return result


# ── Platform Settings ────────────────────────────────────────────────────────────


@router.get("/settings", response_model=PlatformSettingsOut)
async def read_platform_settings(
    _current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Return the platform-wide settings (singleton row, lazy-created)."""
    result = await get_platform_settings(db)
    # Lazy-create path may have added a row; commit so subsequent requests see it.
    await db.commit()
    return result


@router.put("/settings", response_model=PlatformSettingsOut)
async def write_platform_settings(
    body: PlatformSettingsUpdate,
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update the platform-wide settings. All fields are optional."""
    result = await update_platform_settings(
        db=db, payload=body, actor_user_id=current_user.id
    )
    await db.commit()
    return result
