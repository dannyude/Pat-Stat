"""Clinical update and care team schemas."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from src.domains.patients.enums import PatientStatus
from src.domains.users.enums import UserRole


class ClinicalUpdateCreate(BaseModel):
    """Schema for creating a new clinical update for a patient."""

    status: PatientStatus
    note: str
    blood_pressure: Optional[str] = None
    heart_rate: Optional[str] = None
    temperature: Optional[str] = None
    oxygen_level: Optional[str] = None
    
    # [Workflow]: Emergency flag support — allows a single API call from the frontend
    # update modal to both log a clinical note AND raise an emergency flag concurrently.
    mark_emergency: bool = False
    emergency_reason: Optional[str] = None


class NoteCategoryOptionOut(BaseModel):
    """Schema for one selectable staff-note category option."""

    key: str
    label: str
    color: str
    sort_order: int


class NoteCategoryCatalogOut(BaseModel):
    """Schema for staff-note categories and related UI metadata."""

    categories: List[NoteCategoryOptionOut]
    supports_urgent_flag: bool = True


class ClinicalUpdateOut(BaseModel):
    """Schema for returning clinical update data to the client."""

    id: str
    patient_id: str
    status: PatientStatus
    note: str
    blood_pressure: Optional[str] = None
    heart_rate: Optional[str] = None
    temperature: Optional[str] = None
    oxygen_level: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CareTeamMemberOut(BaseModel):
    """Schema for returning care team member data to the client."""

    staff_id: str
    full_name: str
    role: UserRole
    email: str
    is_primary: bool
    assigned_at: datetime

    model_config = {"from_attributes": True}


class StaffNoteCreate(BaseModel):
    """Schema for creating a new staff/clinical note."""

    content: str
    category: str = "General"
    is_urgent: bool = False


class StaffNoteOut(BaseModel):
    """Schema for returning a staff/clinical note."""

    id: str
    patient_id: str
    content: str
    category: str
    is_urgent: bool
    author_name: Optional[str] = None
    author_role: Optional[UserRole] = None
    created_at: datetime

    model_config = {"from_attributes": True}
