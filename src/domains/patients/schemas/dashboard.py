"""Dashboard summary schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from src.domains.patients.enums import PatientStatus
from src.domains.users.enums import UserRole


class DashboardSummaryOut(BaseModel):
    """
    Response shape for the doctor dashboard stat cards.
    [Performance]: Aggregated via complex outer joins to prevent N+1 queries.
    """

    my_patients: int
    critical_count: int
    updates_today: int
    needs_attention: int


class CriticalPatientOut(BaseModel):
    """
    Lightweight read-model for the 'Critical Patients Requiring Urgent Attention' list.
    Only contains the fields necessary for the dashboard data table.
    """

    id: str
    full_name: str
    ward: str
    bed_number: str
    status: PatientStatus
    diagnosis: str

    model_config = {"from_attributes": True}


class NeedsAttentionPatientOut(BaseModel):
    """Row for patients whose status hasn't been updated in >12 hours."""

    id: str
    full_name: str
    ward: str
    bed_number: str
    last_update_at: Optional[datetime] = None
    hours_since_update: Optional[float] = None

    model_config = {"from_attributes": True}


class ActivityFeedItemOut(BaseModel):
    """A single entry in the patient activity overlay timeline."""

    id: str
    author_name: str
    author_role: UserRole
    action: str
    timestamp: datetime

    model_config = {"from_attributes": True}
