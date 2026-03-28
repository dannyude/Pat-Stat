"""Emergency flag schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from src.domains.patients.enums import EmergencyPriority


class EmergencyFlagCreate(BaseModel):
    """Create body for a new emergency flag."""

    admission_id: str
    priority: EmergencyPriority = EmergencyPriority.high
    reason: str


class EmergencyFlagOut(BaseModel):
    """Response shape for an emergency flag."""

    id: str
    admission_id: str
    patient_id: str
    patient_name: str
    ward: str
    bed_number: str
    priority: EmergencyPriority
    reason: str
    is_resolved: bool
    flagged_by_name: Optional[str] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class EmergencyFlagResolve(BaseModel):
    """Body for resolving an emergency flag."""

    resolution_note: Optional[str] = None
