"""Hospital domain schemas."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr


class HospitalApplicationCreate(BaseModel):
    """Public application payload for the Hybrid Verification Flow."""

    hospital_name: str
    hospital_address: str
    hospital_phone: str
    hospital_email: EmailStr
    admin_full_name: str
    admin_email: EmailStr
    admin_password: str


class HospitalOut(BaseModel):
    """Response shape for a hospital record (directory listing)."""

    id: UUID
    name: str
    status: str
    verification_status: str
    address: dict[str, Any] | None = None
    phone: str | None = None
    email: str | None = None
    hospital_code: str | None = None
    state: str | None = None
    hierarchy_level: str | None = None
    subscription_tier: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class HospitalDetailOut(HospitalOut):
    """Full hospital detail including admin contact and application data."""

    admin_name: str | None = None
    admin_email: str | None = None
    admin_phone: str | None = None
    hospital_type: str | None = None
    cac_registration_number: str | None = None
    hospital_license_number: str | None = None
    verified_at: datetime | None = None
    reason_for_joining: str | None = None
    additional_notes: str | None = None


class AllHospitalsMeta(BaseModel):
    """Pagination metadata for the full hospital directory."""

    total: int
    page: int
    page_size: int
    total_pages: int


class AllHospitalsResponse(BaseModel):
    """Paginated hospital directory with metadata."""

    items: list[HospitalOut]
    meta: AllHospitalsMeta


class PendingHospitalsMeta(BaseModel):
    """Summary metadata for paginated pending hospitals dashboard view."""

    total_pending: int
    page: int
    page_size: int
    total_pages: int
    most_recent_application_at: Optional[datetime] = None


class PendingHospitalsResponse(BaseModel):
    """Paginated pending hospital records plus dashboard summary metadata."""

    items: list[HospitalOut]
    meta: PendingHospitalsMeta
