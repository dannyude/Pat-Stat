"""
Hospital domain services.

Handles the Hybrid Verification Flow where hospitals publicly apply
and Pat-Stat Super Admins internally approve them.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core import security
from src.domains.backoffice.models import (
    HospitalApplication,
    HospitalVerificationEvent,
    SuperAdminActionLog,
    VerificationEventType,
)
from src.domains.hospital.models import Hospital
from src.domains.hospital.schemas import (
    AllHospitalsMeta,
    AllHospitalsResponse,
    HospitalApplicationCreate,
    HospitalDetailOut,
    PendingHospitalsMeta,
    PendingHospitalsResponse,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User


async def submit_hospital_application(
    body: HospitalApplicationCreate, db: AsyncSession
) -> Hospital:
    """
    Public entry point for a hospital to apply for the platform.
    Creates the hospital as 'pending_verification' and the admin user.
    """
    # 1. Check if admin email is already taken across the platform
    existing_user = await db.execute(select(User).where(User.email == body.admin_email))
    if existing_user.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Admin email already registered")

    # 2. Create the Hospital
    subdomain_base = body.hospital_name.lower().replace(" ", "-")[:40]
    unique_suffix = str(uuid.uuid4())[:6]
    
    hospital = Hospital(
        name=body.hospital_name,
        address={"text": body.hospital_address},
        phone=body.hospital_phone,
        email=body.hospital_email,
        hospital_code=f"HOSP-{unique_suffix.upper()}",
        state="Pending",  # Can be updated later by the admin
        subdomain=f"{subdomain_base}-{unique_suffix}",
        status="pending_verification",
    )
    db.add(hospital)
    await db.flush()

    # 3. Create the Admin user
    admin_user = User(
        email=body.admin_email,
        hashed_password=security.hash_password(body.admin_password),
        full_name=body.admin_full_name,
        role=UserRole.admin,
        phone=body.hospital_phone,
        hospital_id=hospital.id,
    )
    db.add(admin_user)
    await db.flush()

    # 4. Persist backoffice onboarding payload for super-admin review.
    application = HospitalApplication(
        hospital_id=hospital.id,
        admin_full_name=body.admin_full_name,
        admin_email=body.admin_email,
        admin_phone=body.hospital_phone,
    )
    db.add(application)

    # 5. Seed immutable verification timeline with the submission event.
    db.add(
        HospitalVerificationEvent(
            hospital_id=hospital.id,
            event_type=VerificationEventType.application_submitted,
            note="Hospital application submitted",
            event_metadata={"hospital_email": body.hospital_email},
        )
    )

    return hospital


async def approve_hospital_record(
    hospital_id: str, super_admin_id: str, db: AsyncSession
) -> Hospital:
    """
    Internal Pat-Stat endpoint (Super Admin) to approve a hospital application.
    """
    result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = result.scalar_one_or_none()
    
    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
        
    if hospital.status == "active":
        raise HTTPException(status_code=400, detail="Hospital is already active")

    # Approve the hospital
    hospital.status = "active"
    hospital.verification_status = "verified"
    hospital.verified_by_admin_id = super_admin_id
    hospital.verified_at = datetime.now(timezone.utc)

    db.add(
        HospitalVerificationEvent(
            hospital_id=hospital.id,
            actor_id=super_admin_id,
            event_type=VerificationEventType.approved,
            note="Hospital approved by super admin",
            event_metadata={"status": "active"},
        )
    )
    db.add(
        SuperAdminActionLog(
            actor_id=super_admin_id,
            action="hospital.approve",
            target_type="hospital",
            target_id=str(hospital.id),
            note="Approved hospital onboarding",
        )
    )
    
    await db.flush()
    
    # Ideally, trigger an email notification here: "Welcome to Pat-Stat!"
    
    return hospital


async def reject_hospital_record(
    hospital_id: str,
    super_admin_id: str,
    reason: str | None,
    note: str | None,
    db: AsyncSession,
) -> Hospital:
    """Reject a hospital application."""
    result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    if hospital.status not in ("pending_verification",):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject a hospital with status '{hospital.status}'",
        )

    hospital.status = "inactive"
    hospital.verification_status = "rejected"

    db.add(
        HospitalVerificationEvent(
            hospital_id=hospital.id,
            actor_id=super_admin_id,
            event_type=VerificationEventType.rejected,
            note=reason or "Rejected by super admin",
            event_metadata={"internal_note": note} if note else None,
        )
    )
    db.add(
        SuperAdminActionLog(
            actor_id=super_admin_id,
            action="hospital.reject",
            target_type="hospital",
            target_id=str(hospital.id),
            note=reason or "Rejected hospital application",
        )
    )
    await db.flush()
    return hospital


async def suspend_hospital_record(
    hospital_id: str,
    super_admin_id: str,
    reason: str | None,
    note: str | None,
    db: AsyncSession,
) -> Hospital:
    """Suspend an active hospital's access."""
    result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    if hospital.status != "active":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot suspend a hospital with status '{hospital.status}'",
        )

    hospital.status = "suspended"
    hospital.verification_status = "suspended"

    db.add(
        HospitalVerificationEvent(
            hospital_id=hospital.id,
            actor_id=super_admin_id,
            event_type=VerificationEventType.suspended,
            note=reason or "Suspended by super admin",
            event_metadata={"internal_note": note} if note else None,
        )
    )
    db.add(
        SuperAdminActionLog(
            actor_id=super_admin_id,
            action="hospital.suspend",
            target_type="hospital",
            target_id=str(hospital.id),
            note=reason or "Suspended hospital access",
        )
    )
    await db.flush()
    return hospital


async def reinstate_hospital_record(
    hospital_id: str,
    super_admin_id: str,
    note: str | None,
    db: AsyncSession,
) -> Hospital:
    """Reinstate a suspended hospital."""
    result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")
    if hospital.status != "suspended":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reinstate a hospital with status '{hospital.status}'",
        )

    hospital.status = "active"
    hospital.verification_status = "verified"

    db.add(
        HospitalVerificationEvent(
            hospital_id=hospital.id,
            actor_id=super_admin_id,
            event_type=VerificationEventType.reinstated,
            note=note or "Reinstated by super admin",
        )
    )
    db.add(
        SuperAdminActionLog(
            actor_id=super_admin_id,
            action="hospital.reinstate",
            target_type="hospital",
            target_id=str(hospital.id),
            note=note or "Reinstated hospital access",
        )
    )
    await db.flush()
    return hospital


async def list_all_hospitals(
    db: AsyncSession,
    search: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> AllHospitalsResponse:
    """Return all hospitals with optional search, status filter, and pagination."""
    filters = []
    if status_filter:
        filters.append(Hospital.status == status_filter)
    if search:
        term = f"%{search}%"
        filters.append(
            or_(
                Hospital.name.ilike(term),
                Hospital.hospital_code.ilike(term),
                Hospital.email.ilike(term),
            )
        )

    total_result = await db.execute(
        select(func.count()).select_from(Hospital).where(*filters) if filters
        else select(func.count()).select_from(Hospital)
    )
    total = total_result.scalar_one()

    offset = (page - 1) * page_size
    query = (
        select(Hospital)
        .order_by(Hospital.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    if filters:
        query = query.where(*filters)

    result = await db.execute(query)
    items = list(result.scalars().all())
    total_pages = (total + page_size - 1) // page_size if total else 0

    return AllHospitalsResponse(
        items=items,
        meta=AllHospitalsMeta(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        ),
    )


async def get_hospital_detail(
    db: AsyncSession, hospital_id: str
) -> HospitalDetailOut:
    """Return full hospital detail including admin contact and application data."""
    result = await db.execute(select(Hospital).where(Hospital.id == hospital_id))
    hospital = result.scalar_one_or_none()

    if not hospital:
        raise HTTPException(status_code=404, detail="Hospital not found")

    # Fetch associated admin user
    admin_result = await db.execute(
        select(User).where(
            User.hospital_id == hospital.id,
            User.role == UserRole.admin,
        )
    )
    admin_user = admin_result.scalars().first()

    # Fetch application data
    app_result = await db.execute(
        select(HospitalApplication).where(
            HospitalApplication.hospital_id == hospital.id
        )
    )
    application = app_result.scalar_one_or_none()

    return HospitalDetailOut(
        id=hospital.id,
        name=hospital.name,
        status=hospital.status,
        verification_status=hospital.verification_status or "pending_review",
        address=hospital.address,
        phone=hospital.phone,
        email=hospital.email,
        hospital_code=hospital.hospital_code,
        state=hospital.state,
        hierarchy_level=hospital.hierarchy_level,
        subscription_tier=hospital.subscription_tier,
        created_at=hospital.created_at,
        cac_registration_number=hospital.cac_registration_number,
        hospital_license_number=hospital.hospital_license_number,
        verified_at=hospital.verified_at,
        admin_name=admin_user.full_name if admin_user else None,
        admin_email=admin_user.email if admin_user else None,
        admin_phone=admin_user.phone if admin_user else None,
        hospital_type=application.hospital_type if application else None,
        reason_for_joining=application.reason_for_joining if application else None,
        additional_notes=application.additional_notes if application else None,
    )


async def list_pending_hospitals(
    db: AsyncSession, page: int = 1, page_size: int = 20
) -> PendingHospitalsResponse:
    """
    Internal Pat-Stat endpoint (Super Admin) to list hospitals pending verification
    with pagination and dashboard summary metadata.
    """
    pending_filter = Hospital.status == "pending_verification"

    total_result = await db.execute(select(Hospital.id).where(pending_filter))
    total_pending = len(total_result.scalars().all())

    most_recent_result = await db.execute(
        select(Hospital.created_at)
        .where(pending_filter)
        .order_by(Hospital.created_at.desc())
        .limit(1)
    )
    most_recent_application_at = most_recent_result.scalar_one_or_none()

    offset = (page - 1) * page_size
    result = await db.execute(
        select(Hospital)
        .where(pending_filter)
        .order_by(Hospital.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    items = list(result.scalars().all())

    total_pages = (total_pending + page_size - 1) // page_size if total_pending else 0

    return PendingHospitalsResponse(
        items=items,
        meta=PendingHospitalsMeta(
            total_pending=total_pending,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            most_recent_application_at=most_recent_application_at,
        ),
    )
