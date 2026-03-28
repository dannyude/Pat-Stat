"""Backoffice router for internal Pat-Stat platform endpoints."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import require_super_admin
from src.domains.backoffice.schemas import (
    BackofficeOverviewOut,
    DocumentOut,
    HospitalActionRequest,
    HospitalVerificationEventOut,
    SuperAdminCreateRequest,
    SuperAdminOut,
    SuperAdminActionOut,
    SuperAdminStatusUpdate,
)
from src.domains.backoffice.services import (
    create_super_admin,
    get_platform_overview,
    list_hospital_documents,
    list_hospital_verification_events,
    list_super_admin_actions,
    list_super_admins,
    toggle_super_admin_status,
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
