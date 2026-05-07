"""Clinical update endpoints for patient admissions."""

from typing import List

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.rate_limit import limiter
from src.core.redis_client import publish_patient_event
from src.core.security import get_current_user, require_roles
from src.domains.notifications import policy
from src.domains.notifications.dispatch import dispatch_family_notification
from src.domains.patients.enums import PatientStatus
from src.domains.patients.models import ClinicalUpdate, EmergencyFlag
from src.domains.patients.schemas import ClinicalUpdateCreate, ClinicalUpdateOut
from src.domains.users.enums import UserRole
from src.domains.users.models import User

from src.api.v1.patient_helpers import (
    clinical_update_out,
    get_active_admission,
)

router = APIRouter(prefix="/patients", tags=["Clinical Updates"])

_clinical_staff = require_roles(UserRole.admin, UserRole.doctor, UserRole.nurse)


@router.post("/{patient_id}/updates", response_model=ClinicalUpdateOut, status_code=201)
@limiter.limit(settings.write_rate_limit)
async def add_clinical_update(
    request: Request,
    patient_id: str,
    body: ClinicalUpdateCreate,
    current_user: User = Depends(_clinical_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Add a new clinical update (e.g., vitals, notes) for a patient.

    - This endpoint is restricted to clinical staff.
    - The update is associated with the patient's current active admission.
    - If ``mark_emergency`` is True, an emergency flag is also created on the
      admission so the frontend can handle both in a single call.
    """
    _ = request
    # [DB/Logic]: Always associate updates with the *active* admission, not just the patient.
    # get_active_admission handles the 404 logic if there's no ongoing admission.
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)

    # Determine whether this update changed the patient's clinical status —
    # used both for the WS payload below and for picking the notification tier.
    status_actually_changed = body.status != admission.status

    update = ClinicalUpdate(
        admission_id=admission.id,
        authored_by_id=current_user.id,
        status=body.status,
        note=body.note,
        blood_pressure=body.blood_pressure,
        heart_rate=body.heart_rate,
        temperature=body.temperature,
        oxygen_level=body.oxygen_level,
    )
    db.add(update)

    if body.mark_emergency:
        flag = EmergencyFlag(
            admission_id=admission.id,
            flagged_by_id=current_user.id,
            reason=body.emergency_reason or body.note,
        )
        db.add(flag)

    # [DB/Logic]: Flush to generate IDs, but commit happens automatically
    # at the end of the request via middleware/dependency injection setup.
    await db.flush()
    out = clinical_update_out(update, patient_id)
    await publish_patient_event(patient_id, {
        "type": "status_changed",
        "patient_id": patient_id,
        "status": body.status.value,
        "note": body.note,
        "update_id": update.id,
        "created_at": update.created_at.isoformat(),
    })

    # ── Family fan-out via Celery + FCM (per notification policy) ────────────
    # We pick the policy ``event_kind`` based on what actually happened so
    # routine vitals don't push, status moves do, and "moved to Critical"
    # always wakes people up regardless of quiet hours.
    if body.mark_emergency:
        event_kind = policy.EVENT_EMERGENCY_FLAG
    elif body.status == PatientStatus.critical and status_actually_changed:
        event_kind = policy.EVENT_STATUS_TO_CRITICAL
    elif status_actually_changed:
        event_kind = policy.EVENT_STATUS_CHANGED
    else:
        event_kind = policy.EVENT_VITALS_ONLY

    dispatch_family_notification(
        patient_id=patient_id,
        patient_name=admission.patient.full_name if admission.patient else "Patient",
        event_kind=event_kind,
        new_status=body.status.value,
        update_id=update.id,
        note_preview=body.note or "",
        author_name=current_user.full_name,
    )
    return out


@router.get("/{patient_id}/updates", response_model=List[ClinicalUpdateOut])
async def list_clinical_updates(
    patient_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List all clinical updates for a patient, in reverse chronological order.
    """
    admission = await get_active_admission(patient_id, db, hospital_id=current_user.hospital_id)

    # [DB]: Fetch updates for THIS specific admission, sorted newest-first.
    result = await db.execute(
        select(ClinicalUpdate)
        .where(ClinicalUpdate.admission_id == admission.id)
        .order_by(ClinicalUpdate.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    updates = result.scalars().all()
    return [clinical_update_out(u, patient_id) for u in updates]


__all__ = ["router"]
