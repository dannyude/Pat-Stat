"""Family domain schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr


class FamilyMemberLinkRequest(BaseModel):
    family_user_id: str
    relationship_to_patient: Optional[str] = None


class FamilyMemberOut(BaseModel):
    user_id: str
    full_name: str
    email: str
    relationship_to_patient: Optional[str] = None


class FamilyPatientOut(BaseModel):
    patient_id: str
    full_name: str
    status: str
    diagnosis: str
    ward: str
    bed_number: str
    admitted_at: datetime


class FamilyCareTeamOut(BaseModel):
    doctor_name: Optional[str] = None
    nurse_names: List[str]


class FamilyLatestUpdateOut(BaseModel):
    id: str
    status: str
    note: str
    created_at: datetime
    authored_by_name: Optional[str] = None


class FamilyPatientOverviewOut(BaseModel):
    patient_id: str
    full_name: str
    age: Optional[int] = None
    gender: Optional[str] = None
    status: str
    diagnosis: str
    ward: str
    bed_number: str
    admitted_at: datetime
    care_team: FamilyCareTeamOut
    latest_update: Optional[FamilyLatestUpdateOut] = None


class FamilyPatientMobileDashboardOut(BaseModel):
    """
    Single payload optimized for mobile patient detail rendering.
    [Performance/UI]: Allows rendering the main family app screen in one network request.
    """

    overview: FamilyPatientOverviewOut
    updates: List[FamilyLatestUpdateOut]


class FamilyInviteCreateRequest(BaseModel):
    family_member_name: str
    email: EmailStr
    phone: Optional[str] = None
    relationship_to_patient: Optional[str] = None


class FamilyInviteCreateResponse(BaseModel):
    invitation_id: str
    email: EmailStr
    invite_link: str
    access_code: str
    expires_at: datetime


class FamilyInviteAcceptRequest(BaseModel):
    email: EmailStr
    token: str
    access_code: str
    full_name: Optional[str] = None
    password: Optional[str] = None
    phone: Optional[str] = None


__all__ = [
    "FamilyMemberLinkRequest",
    "FamilyMemberOut",
    "FamilyPatientOut",
    "FamilyCareTeamOut",
    "FamilyLatestUpdateOut",
    "FamilyPatientOverviewOut",
    "FamilyPatientMobileDashboardOut",
    "FamilyInviteCreateRequest",
    "FamilyInviteCreateResponse",
    "FamilyInviteAcceptRequest",
]
