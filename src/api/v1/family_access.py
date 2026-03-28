"""Family access-management endpoints (link/unlink/list members)."""

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import require_roles
from src.domains.family.schemas import FamilyMemberLinkRequest, FamilyMemberOut
from src.domains.family.services import (
    get_patient_with_links,
    link_family_member_to_patient,
    unlink_family_member_from_patient,
)
from src.domains.users.enums import UserRole
from src.domains.users.models import User

router = APIRouter(prefix="/family", tags=["Family"])

_admin_only = require_roles(UserRole.admin)


@router.get("/patients/{patient_id}/members", response_model=List[FamilyMemberOut])
async def list_patient_family_members(
    patient_id: str,
    _current_user: User = Depends(_admin_only),  # noqa: ARG001
    db: AsyncSession = Depends(get_db),
):
    """Return all active family accounts linked to a patient."""
    # [Logic]: Delegate DB lookup to service layer to handle the complex query
    # and 404/authorization logic.
    patient = await get_patient_with_links(patient_id, db)
    # [Performance]: Filter out inactive family members directly in python since
    # the relationship is eager-loaded in the service layer.
    return [
        FamilyMemberOut(
            user_id=link.family_user_id,
            full_name=link.family_user.full_name,
            email=link.family_user.email,
            relationship_to_patient=link.relationship_to_patient,
        )
        for link in patient.family_links
        if link.family_user is not None and link.family_user.is_active
    ]


@router.post("/patients/{patient_id}/members", response_model=FamilyMemberOut)
async def link_family_member(
    patient_id: str,
    body: FamilyMemberLinkRequest,
    _current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Link an existing family user account to a patient."""
    # [Logic]: Delegate the linking transaction (which requires updating both user
    # relationships and creating a FamilyPatientLink entity) to the service layer.
    link, family_user = await link_family_member_to_patient(
        family_user_id=body.family_user_id,
        patient_id=patient_id,
        relationship_to_patient=body.relationship_to_patient,
        db=db,
    )

    return FamilyMemberOut(
        user_id=family_user.id,
        full_name=family_user.full_name,
        email=family_user.email,
        relationship_to_patient=link.relationship_to_patient,
    )


@router.delete("/patients/{patient_id}/members/{family_user_id}", status_code=204)
async def unlink_family_member(
    patient_id: str,
    family_user_id: str,
    _current_user: User = Depends(_admin_only),
    db: AsyncSession = Depends(get_db),
):
    """Remove a family user's access to a patient."""
    await unlink_family_member_from_patient(
        patient_id=patient_id,
        family_user_id=family_user_id,
        db=db,
    )


__all__ = ["router"]
