"""Patient identity and CRUD schemas."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from src.domains.patients.enums import BloodGroup, PatientStatus


class PatientProfileCreate(BaseModel):
    """Schema for creating the human identity/profile record."""

    full_name: str
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    pat_stat_id: Optional[str] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class AdmissionCreate(BaseModel):
    """Schema for creating one hospital admission/episode."""

    ward: str
    bed_number: str
    diagnosis: str
    status: PatientStatus = PatientStatus.being_monitored
    primary_doctor_id: Optional[str] = None
    assigned_nurse_ids: List[str] = Field(default_factory=list)


class PatientCreate(BaseModel):
    """Schema for creating a new patient."""

    full_name: str
    age: Optional[int] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    pat_stat_id: Optional[str] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    ward: str
    bed_number: str
    diagnosis: str
    status: PatientStatus = PatientStatus.being_monitored
    primary_doctor_id: Optional[str] = None
    assigned_nurse_ids: List[str] = Field(default_factory=list)


class PatientUpdate(BaseModel):
    """Schema for updating an existing patient."""

    full_name: Optional[str] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    national_id: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    ward: Optional[str] = None
    bed_number: Optional[str] = None
    diagnosis: Optional[str] = None
    status: Optional[PatientStatus] = None
    is_active: Optional[bool] = None
    primary_doctor_id: Optional[str] = None
    assigned_nurse_ids: Optional[List[str]] = None


class PatientOut(BaseModel):
    """Schema for returning patient data to the client."""

    id: str
    pat_stat_id: Optional[str] = None
    national_id: Optional[str] = None
    full_name: str
    age: Optional[int] = None
    date_of_birth: Optional[date] = None
    gender: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    blood_group: Optional[BloodGroup] = None
    allergies: Optional[str] = None
    chronic_conditions: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    ward: str
    bed_number: str
    diagnosis: str
    status: PatientStatus
    primary_doctor_id: Optional[str] = None
    primary_doctor_name: Optional[str] = None
    primary_doctor: Optional["CareTeamMemberOut"] = None
    assigned_nurse_ids: List[str] = Field(default_factory=list)
    assigned_nurses: List["CareTeamMemberOut"] = Field(default_factory=list)
    assigned_nurses_count: int = 0
    admitted_at: datetime
    discharged_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class PatientSearchResponse(BaseModel):
    """
    Lightweight response shape used by patient-search flows.
    [Performance/DB]: We avoid serializing full care teams and updates here
    because the global search endpoint fetches many rows at once.
    """

    id: str
    pat_stat_id: str
    full_name: str
    date_of_birth: Optional[date] = None
    phone: Optional[str] = None
    ward: Optional[str] = None
    bed_number: Optional[str] = None
    diagnosis: Optional[str] = None
    status: Optional[PatientStatus] = None
    admitted_at: Optional[datetime] = None
    discharged_at: Optional[datetime] = None
    hospital_id: Optional[str] = None
    has_active_admission: bool = False

    model_config = {"from_attributes": True}


class PatientWithTeamOut(PatientOut):
    """Schema for returning patient data along with their care team and latest clinical update."""

    care_team: List["CareTeamMemberOut"] = Field(default_factory=list)
    latest_update: Optional["ClinicalUpdateOut"] = None


class PatientResponse(PatientOut):
    """Explicit read schema: backend returns nested staff details for display."""


# Import here to avoid circular — needed for forward references above
from src.domains.patients.schemas.clinical import (  # noqa: E402, F401
    CareTeamMemberOut,
    ClinicalUpdateOut,
)
