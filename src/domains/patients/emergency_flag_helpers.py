"""Helper functions for emergency flag API routes."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.domains.patients.models import Admission, EmergencyFlag
from src.domains.patients.schemas import EmergencyFlagOut
from src.domains.users.models import User


def _flag_to_out(flag: EmergencyFlag) -> EmergencyFlagOut:
    admission = flag.admission
    return EmergencyFlagOut(
        id=flag.id,
        admission_id=admission.id,
        patient_id=admission.patient_id,
        patient_name=admission.patient.full_name if admission.patient else "Unknown",
        ward=admission.ward,
        bed_number=admission.bed_number,
        priority=flag.priority,
        reason=flag.reason,
        is_resolved=flag.is_resolved,
        flagged_by_name=(flag.flagged_by.full_name if flag.flagged_by else None),
        resolved_by_name=(flag.resolved_by.full_name if flag.resolved_by else None),
        resolved_at=flag.resolved_at,
        created_at=flag.created_at,
    )


async def _get_flag(flag_id: str, db: AsyncSession) -> EmergencyFlag:
    # [Performance/DB]: Eager load all related entities (admission, patient, author, resolver)
    # needed for Pydantic serialization over the _flag_to_out helper.
    result = await db.execute(
        select(EmergencyFlag)
        .options(
            selectinload(EmergencyFlag.admission).selectinload(Admission.patient),
            selectinload(EmergencyFlag.flagged_by),
            selectinload(EmergencyFlag.resolved_by),
        )
        .where(EmergencyFlag.id == flag_id)
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise HTTPException(status_code=404, detail="Emergency flag not found")
    return flag


def _ensure_flag_visible_to_user(flag: EmergencyFlag, current_user: User) -> None:
    # [Security/RBAC]: Sub-resource tenancy check. Ensure the flag belongs to an
    # admission in the same hospital as the current user. Returns 404 to avoid leaking existence.
    if current_user.hospital_id is None:
        raise HTTPException(
            status_code=403, detail="User is not assigned to a hospital"
        )
    if str(flag.admission.hospital_id) != str(current_user.hospital_id):
        raise HTTPException(status_code=404, detail="Emergency flag not found")
