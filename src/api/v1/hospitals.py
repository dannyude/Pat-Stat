"""
Hospital routing components.
Endpoints for the Hybrid Verification Flow.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.domains.hospital import services
from src.domains.hospital.schemas import (
    HospitalApplicationCreate,
    HospitalOut,
)

router = APIRouter(prefix="/hospitals", tags=["Hospitals"])


@router.post("/apply", status_code=201, response_model=HospitalOut)
async def apply_for_hospital_account(
    body: HospitalApplicationCreate, db: AsyncSession = Depends(get_db)
):
    """
    Public endpoint for a hospital admin to submit their data.
    Creates Hospital (status: pending_verification)
    Creates User (role: admin, linked to hospital)
    """
    hospital = await services.submit_hospital_application(body, db)
    await db.commit()
    return hospital
