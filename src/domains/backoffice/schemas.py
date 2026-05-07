from datetime import date, datetime
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


class OnboardingTrendPointOut(BaseModel):
    """One weekly data point in the hospital onboarding trend chart."""

    week_start: date  # ISO date (Monday of the ISO week)
    submitted: int  # Hospitals whose record was created in this week
    approved: int  # Hospitals approved in this week
    rejected: int  # Hospitals rejected in this week


class OnboardingTrendsOut(BaseModel):
    """Response envelope for ``GET /backoffice/overview/trends``."""

    weeks: int
    points: list[OnboardingTrendPointOut]


class PlatformSettingsOut(BaseModel):
    """Response model for the singleton platform settings record."""

    model_config = ConfigDict(from_attributes=True)

    platform_name: str
    support_email: EmailStr | None = None
    default_region: str | None = None
    updated_at: datetime | None = None
    updated_by_id: UUID | None = None


class PlatformSettingsUpdate(BaseModel):
    """Partial-update payload for platform settings.

    All fields are optional; omitted fields leave the existing value unchanged.
    """

    platform_name: str | None = Field(default=None, min_length=1, max_length=100)
    support_email: EmailStr | None = None
    default_region: str | None = Field(default=None, max_length=100)


class HospitalActionRequest(BaseModel):
    """Payload for reject / suspend / reinstate actions on a hospital."""

    reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)


class HospitalVerificationEventOut(BaseModel):
    """Serializable view of a hospital verification timeline item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
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
    # ``target_id`` is intentionally typed as ``str | None`` rather than
    # ``UUID | None`` to mirror the underlying ``SuperAdminActionLog.target_id``
    # column (``String(100)`` in the model).
    #
    # Why we don't tighten this to UUID:
    #   1. The model deliberately allows non-UUID identifiers for future
    #      target types (e.g. configuration keys, well-known singleton ids,
    #      external system references). Tightening the schema would 500
    #      every read of the audit log if any such row exists.
    #   2. The current consumers (frontend audit table, CSV export) treat
    #      target_id as an opaque identifier — they don't parse it, just
    #      display or filter on it.
    #   3. If a future caller really needs a UUID-typed value, they should
    #      cast / parse client-side rather than have the API gate every
    #      historical row on whether it happens to match the UUID format.
    target_id: str | None = None
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
