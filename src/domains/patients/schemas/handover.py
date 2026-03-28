"""Shift handover schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ShiftHandoverCreate(BaseModel):
    """Create body for a new shift handover."""

    admission_id: str
    to_staff_id: Optional[str] = None
    summary: str
    pending_actions: Optional[str] = None


class ShiftHandoverOut(BaseModel):
    """Response shape for a shift handover."""

    id: str
    admission_id: str
    patient_id: str
    patient_name: str
    from_staff_name: Optional[str] = None
    to_staff_name: Optional[str] = None
    summary: str
    pending_actions: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
