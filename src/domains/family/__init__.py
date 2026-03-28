"""Family domain package."""

from src.domains.family.models import FamilyInvite, FamilyPatientLink
from src.domains.family.schemas import (
    FamilyInviteAcceptRequest,
    FamilyInviteCreateRequest,
    FamilyInviteCreateResponse,
    FamilyMemberLinkRequest,
    FamilyMemberOut,
    FamilyPatientOut,
)
from src.domains.family.services import (
    accept_family_invite,
    create_family_invite,
    get_active_admission,
    get_patient_with_links,
    link_family_member_to_patient,
    list_family_user_active_admissions,
    unlink_family_member_from_patient,
    validate_family_user,
)

__all__ = [
    "FamilyInvite",
    "FamilyPatientLink",
    "FamilyInviteCreateRequest",
    "FamilyInviteCreateResponse",
    "FamilyInviteAcceptRequest",
    "FamilyMemberLinkRequest",
    "FamilyMemberOut",
    "FamilyPatientOut",
    "accept_family_invite",
    "create_family_invite",
    "get_active_admission",
    "get_patient_with_links",
    "link_family_member_to_patient",
    "list_family_user_active_admissions",
    "unlink_family_member_from_patient",
    "validate_family_user",
]
