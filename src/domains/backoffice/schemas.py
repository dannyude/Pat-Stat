from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.domains.backoffice.models import VerificationEventType


class BackofficeOverviewOut(BaseModel):
    """Platform-level counters for super-admin dashboards."""

    total_hospitals: int
    pending_hospitals: int
    active_hospitals: int
    suspended_hospitals: int
    total_platform_users: int
    new_this_week: int = 0


class HospitalActionRequest(BaseModel):
    """Payload for reject / suspend / reinstate actions on a hospital."""

    reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)


class HospitalVerificationEventOut(BaseModel):
    """Serializable view of a hospital verification timeline item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID  # 👈 Stricter typing
    hospital_id: UUID
    actor_id: UUID | None = None
    event_type: VerificationEventType
    note: str | None = None
    event_metadata: dict[str, Any] | None = None
    created_at: datetime


class SuperAdminActionOut(BaseModel):
    """Serializable view of a super-admin audit action."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None = None
    actor_name: str | None = None
    action: str
    target_type: Literal["hospital", "user", "staff", "settings"] | None = None
    target_id: UUID | None = None
    note: str | None = None
    ip_address: str | None = None
    action_metadata: dict[str, Any] | None = None
    created_at: datetime


class SuperAdminCreateRequest(BaseModel):
    """Payload for creating a new platform super-admin user."""

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=30)


class SuperAdminOut(BaseModel):
    """Response model for backoffice-created super-admin users."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: Literal["super_admin"]
    phone: str | None = None
    is_active: bool
    created_at: datetime


class SuperAdminStatusUpdate(BaseModel):
    """Payload for toggling a super-admin's active status."""

    is_active: bool


class DocumentOut(BaseModel):
    """Serializable view of a hospital onboarding document."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_type: str
    file_name: str
    file_url: str
    file_size_bytes: int | None = None
    mime_type: str | None = None
    is_verified: bool | None = None
    reviewer_note: str | None = None
