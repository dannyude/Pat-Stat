"""Backoffice application services for internal Pat-Stat operations."""

from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, literal, select
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.functions import count
from sqlalchemy.ext.asyncio import AsyncSession

from src.domains.backoffice.models import (
    HospitalApplication,
    HospitalDocument,
    HospitalVerificationEvent,
    PlatformSettings,
    SuperAdminActionLog,
    VerificationEventType,
)
from src.domains.backoffice.schemas import (
    BackofficeOverviewOut,
    DocumentOut,
    HospitalVerificationEventOut,
    OnboardingTrendPointOut,
    OnboardingTrendsOut,
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    SuperAdminCreateRequest,
    SuperAdminOut,
    SuperAdminActionOut,
)
from src.core.security import hash_password

MAX_SUPER_ADMINS: int = 3
from src.domains.users.enums import UserRole
from src.domains.hospital.models import Hospital
from src.domains.users.models import User


async def get_platform_overview(db: AsyncSession) -> BackofficeOverviewOut:
    """Return summary counters for platform operations dashboards."""

    total_hospitals = (
        await db.execute(select(count()).select_from(Hospital))
    ).scalar_one()
    pending_hospitals = (
        await db.execute(
            select(count())
            .select_from(Hospital)
            .where(Hospital.status == "pending_verification")
        )
    ).scalar_one()
    active_hospitals = (
        await db.execute(
            select(count()).select_from(Hospital).where(Hospital.status == "active")
        )
    ).scalar_one()
    suspended_hospitals = (
        await db.execute(
            select(count()).select_from(Hospital).where(Hospital.status == "suspended")
        )
    ).scalar_one()
    total_platform_users = (
        await db.execute(select(count()).select_from(User))
    ).scalar_one()

    # New this week: hospitals created in the last 7 days
    one_week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_this_week = (
        await db.execute(
            select(count())
            .select_from(Hospital)
            .where(Hospital.created_at >= one_week_ago)
        )
    ).scalar_one()

    return BackofficeOverviewOut(
        total_hospitals=total_hospitals,
        pending_hospitals=pending_hospitals,
        active_hospitals=active_hospitals,
        suspended_hospitals=suspended_hospitals,
        total_platform_users=total_platform_users,
        new_this_week=new_this_week,
    )


async def list_hospital_verification_events(
    db: AsyncSession, hospital_id: str, limit: int = 100
) -> list[HospitalVerificationEventOut]:
    """Return verification timeline entries for a single hospital."""

    result = await db.execute(
        select(HospitalVerificationEvent)
        .where(HospitalVerificationEvent.hospital_id == hospital_id)
        .order_by(HospitalVerificationEvent.created_at.desc())
        .limit(limit)
    )

    return [
        HospitalVerificationEventOut(
            id=str(item.id),
            hospital_id=str(item.hospital_id),
            actor_id=str(item.actor_id) if item.actor_id else None,
            event_type=item.event_type,
            note=item.note,
            event_metadata=item.event_metadata,
            created_at=item.created_at,
        )
        for item in result.scalars().all()
    ]


async def list_super_admin_actions(
    db: AsyncSession,
    limit: int = 100,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    action_filter: str | None = None,
) -> list[SuperAdminActionOut]:
    """Return latest global super-admin audit actions with optional filters."""

    query = (
        select(SuperAdminActionLog, User.full_name.label("actor_name"))
        .outerjoin(User, SuperAdminActionLog.actor_id == User.id)
    )

    filters = []
    if date_from:
        filters.append(SuperAdminActionLog.created_at >= date_from)
    if date_to:
        filters.append(SuperAdminActionLog.created_at <= date_to)
    if action_filter:
        filters.append(SuperAdminActionLog.action == action_filter)

    if filters:
        query = query.where(*filters)

    query = query.order_by(SuperAdminActionLog.created_at.desc()).limit(limit)
    result = await db.execute(query)

    return [
        SuperAdminActionOut(
            id=str(log.id),
            actor_id=str(log.actor_id) if log.actor_id else None,
            actor_name=actor_name,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            note=log.note,
            ip_address=log.ip_address,
            action_metadata=log.action_metadata,
            created_at=log.created_at,
        )
        for log, actor_name in result.all()
    ]


async def create_super_admin(
    db: AsyncSession,
    actor_user_id: str,
    payload: SuperAdminCreateRequest,
) -> SuperAdminOut:
    """Create a super-admin account, enforcing a hard platform cap."""

    existing_user = await db.execute(select(User).where(User.email == payload.email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    super_admin_count = (
        await db.execute(
            select(count()).select_from(User).where(User.role == UserRole.super_admin)
        )
    ).scalar_one()
    if super_admin_count >= MAX_SUPER_ADMINS:
        raise HTTPException(
            status_code=400,
            detail=f"Super admin limit reached ({MAX_SUPER_ADMINS})",
        )

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=UserRole.super_admin,
        phone=payload.phone,
        hospital_id=None,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    db.add(
        SuperAdminActionLog(
            actor_id=actor_user_id,
            action="super_admin.create",
            target_type="user",
            target_id=str(user.id),
            note="Created super admin from backoffice",
            action_metadata={"email": payload.email},
        )
    )
    await db.flush()

    return SuperAdminOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role="super_admin",
        phone=user.phone,
        is_active=user.is_active,
        created_at=user.created_at,
    )


async def list_super_admins(db: AsyncSession) -> list[SuperAdminOut]:
    """Return all platform super-admin users."""

    result = await db.execute(
        select(User)
        .where(User.role == UserRole.super_admin)
        .order_by(User.created_at.desc())
    )

    return [
        SuperAdminOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role="super_admin",
            phone=user.phone,
            is_active=user.is_active,
            created_at=user.created_at,
        )
        for user in result.scalars().all()
    ]


async def toggle_super_admin_status(
    db: AsyncSession,
    target_user_id: str,
    is_active: bool,
    actor_user_id: str,
) -> SuperAdminOut:
    """Activate or deactivate a super-admin account."""

    result = await db.execute(
        select(User).where(User.id == target_user_id, User.role == UserRole.super_admin)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Super admin not found")

    if str(user.id) == str(actor_user_id):
        raise HTTPException(
            status_code=400, detail="Cannot change your own active status"
        )

    user.is_active = is_active
    action_label = "super_admin.activate" if is_active else "super_admin.deactivate"

    db.add(
        SuperAdminActionLog(
            actor_id=actor_user_id,
            action=action_label,
            target_type="user",
            target_id=str(user.id),
            note=f"{'Activated' if is_active else 'Deactivated'} super admin",
            action_metadata={"email": user.email, "is_active": is_active},
        )
    )
    await db.flush()

    return SuperAdminOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role="super_admin",
        phone=user.phone,
        is_active=user.is_active,
        created_at=user.created_at,
    )


async def list_hospital_documents(
    db: AsyncSession, hospital_id: str
) -> list[DocumentOut]:
    """Return all uploaded documents for a hospital application."""

    # Find the application for this hospital
    app_result = await db.execute(
        select(HospitalApplication)
        .where(HospitalApplication.hospital_id == hospital_id)
        .options(selectinload(HospitalApplication.documents))
    )
    application = app_result.scalar_one_or_none()

    if not application:
        return []

    return [
        DocumentOut(
            id=doc.id,
            document_type=doc.document_type.value if hasattr(doc.document_type, 'value') else str(doc.document_type),
            file_name=doc.file_name,
            file_url=doc.file_url,
            file_size_bytes=doc.file_size_bytes,
            mime_type=doc.mime_type,
            is_verified=doc.is_verified,
            reviewer_note=doc.reviewer_note,
        )
        for doc in application.documents
    ]


# ── Onboarding trends ───────────────────────────────────────────────────────────


def _iso_week_monday(d: date) -> date:
    """Return the Monday of the ISO week containing ``d`` (weekday 0-indexed)."""
    return d - timedelta(days=d.weekday())


async def get_onboarding_trends(
    db: AsyncSession, weeks: int = 4
) -> OnboardingTrendsOut:
    """Return weekly onboarding activity over the last ``weeks`` ISO weeks.

    For each bucket we return:
        • submitted — hospitals whose record was created in that week
        • approved  — number of "Approved" verification events in that week
        • rejected  — number of "Rejected" verification events in that week

    Buckets are anchored to Monday (ISO week start) in UTC.
    """

    if weeks < 1:
        weeks = 1
    if weeks > 52:
        weeks = 52

    today_utc = datetime.now(timezone.utc).date()
    current_monday = _iso_week_monday(today_utc)
    window_start = current_monday - timedelta(weeks=weeks - 1)
    window_start_dt = datetime.combine(window_start, datetime.min.time(), tzinfo=timezone.utc)

    # ── Submitted: bucket Hospital.created_at by week ───────────────────────────
    # `date_trunc('week', …)` in Postgres also anchors weeks to Monday, so this
    # aligns cleanly with `_iso_week_monday` above.
    submitted_bucket = func.date_trunc("week", Hospital.created_at)
    submitted_rows = (
        await db.execute(
            select(submitted_bucket.label("bucket"), count().label("n"))
            .where(Hospital.created_at >= window_start_dt)
            .group_by(submitted_bucket)
        )
    ).all()

    # ── Approved / rejected: bucket verification events by week ────────────────
    event_bucket = func.date_trunc("week", HospitalVerificationEvent.created_at)
    approved_rows = (
        await db.execute(
            select(event_bucket.label("bucket"), count().label("n"))
            .where(
                HospitalVerificationEvent.created_at >= window_start_dt,
                HospitalVerificationEvent.event_type == VerificationEventType.approved,
            )
            .group_by(event_bucket)
        )
    ).all()
    rejected_rows = (
        await db.execute(
            select(event_bucket.label("bucket"), count().label("n"))
            .where(
                HospitalVerificationEvent.created_at >= window_start_dt,
                HospitalVerificationEvent.event_type == VerificationEventType.rejected,
            )
            .group_by(event_bucket)
        )
    ).all()

    def _to_map(rows) -> dict[date, int]:
        # `date_trunc` returns timestamptz; normalise to a plain date key.
        return {r.bucket.date() if hasattr(r.bucket, "date") else r.bucket: r.n for r in rows}

    submitted_map = _to_map(submitted_rows)
    approved_map = _to_map(approved_rows)
    rejected_map = _to_map(rejected_rows)

    # ── Emit a contiguous series so the UI can draw without gaps ──────────────
    points: list[OnboardingTrendPointOut] = []
    for i in range(weeks):
        bucket_start = window_start + timedelta(weeks=i)
        points.append(
            OnboardingTrendPointOut(
                week_start=bucket_start,
                submitted=submitted_map.get(bucket_start, 0),
                approved=approved_map.get(bucket_start, 0),
                rejected=rejected_map.get(bucket_start, 0),
            )
        )

    return OnboardingTrendsOut(weeks=weeks, points=points)


# ── Platform settings (singleton) ───────────────────────────────────────────────


async def _get_or_create_platform_settings(db: AsyncSession) -> PlatformSettings:
    """Return the singleton settings row, creating it lazily on first read."""

    result = await db.execute(
        select(PlatformSettings).order_by(PlatformSettings.created_at.asc()).limit(1)
    )
    settings = result.scalar_one_or_none()
    if settings is not None:
        return settings

    settings = PlatformSettings(platform_name="Pat-Stat")
    db.add(settings)
    await db.flush()
    return settings


async def get_platform_settings(db: AsyncSession) -> PlatformSettingsOut:
    """Return the platform-wide settings (lazy-create on first call)."""

    settings = await _get_or_create_platform_settings(db)
    return PlatformSettingsOut.model_validate(settings)


async def update_platform_settings(
    db: AsyncSession,
    payload: PlatformSettingsUpdate,
    actor_user_id: str,
) -> PlatformSettingsOut:
    """Apply a partial update to the platform settings and audit the change."""

    settings = await _get_or_create_platform_settings(db)

    changes: dict[str, object] = {}
    if payload.platform_name is not None:
        if payload.platform_name != settings.platform_name:
            changes["platform_name"] = payload.platform_name
        settings.platform_name = payload.platform_name
    if payload.support_email is not None:
        if payload.support_email != settings.support_email:
            changes["support_email"] = payload.support_email
        settings.support_email = payload.support_email
    if payload.default_region is not None:
        if payload.default_region != settings.default_region:
            changes["default_region"] = payload.default_region
        settings.default_region = payload.default_region

    settings.updated_by_id = actor_user_id

    if changes:
        db.add(
            SuperAdminActionLog(
                actor_id=actor_user_id,
                action="platform_settings.update",
                target_type="settings",
                target_id=str(settings.id),
                note="Updated platform settings",
                action_metadata={"changes": changes},
            )
        )

    await db.flush()
    return PlatformSettingsOut.model_validate(settings)
