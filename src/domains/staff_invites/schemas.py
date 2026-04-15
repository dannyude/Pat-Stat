"""Staff invitation schemas.

These Pydantic models define the API contract for the staff invitation flow.
The flow has three phases:

1. CREATE  — Admin sends invite (name, email, role)
2. VERIFY  — Staff validates their invite (token + access code + email)
3. SETUP   — Staff creates their account (password + optional profile info)

Splitting verify and setup into separate requests improves UX: the frontend
can show an error immediately if the invite is invalid/expired, before the
user wastes time filling out a password form.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Phase 1: Admin Creates Invite ────────────────────────────────────────────


class StaffInviteCreateRequest(BaseModel):
    """Payload for an admin to invite a new staff member."""

    email: EmailStr
    staff_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Full name of the person being invited",
    )
    # [Validation]: Only doctor and nurse are valid staff roles.
    # admin/super_admin/family are excluded because:
    # - super_admin is platform-level (created via CLI seed script)
    # - admin is created during hospital onboarding (backoffice flow)
    # - family uses the separate FamilyInvite flow
    role: Literal["doctor", "nurse"]


class StaffInviteCreateResponse(BaseModel):
    """Returned to the admin after creating an invite.

    Contains the invite link and access code so the admin can share them
    with the new staff member (email is sent automatically, but the admin
    may also want to share the access code verbally as a secondary channel).
    """

    invitation_id: str
    email: EmailStr
    staff_name: str
    role: str
    invite_link: str
    access_code: str
    expires_at: datetime


# ── Phase 2: Staff Verifies Identity ─────────────────────────────────────────


class StaffInviteVerifyRequest(BaseModel):
    """Staff submits this to verify their invite before seeing the setup form.

    All three factors must match:
    - email: proves the recipient is the intended person
    - token: proves they received the invite link (high-entropy URL param)
    - access_code: proves they received the out-of-band code from the admin
    """

    email: EmailStr
    token: str = Field(..., min_length=1, description="Raw token from the invite URL")
    access_code: str = Field(
        ..., min_length=1, max_length=20, description="8-char code shared by admin"
    )


class StaffInviteVerifyResponse(BaseModel):
    """Returned when verification succeeds.

    Provides enough context for the frontend to render the "Account Setup"
    screen with pre-filled information (name, role, hospital).
    """

    valid: bool = True
    staff_name: str
    role: str
    hospital_name: str
    email: str


# ── Phase 3: Staff Creates Account ──────────────────────────────────────────


class StaffInviteSetupRequest(BaseModel):
    """Staff submits this to finalize their account.

    Re-validates all three factors (defense in depth — the verify step could
    have been called from a different session/device, so we don't trust it
    implicitly).
    """

    email: EmailStr
    token: str = Field(..., min_length=1)
    access_code: str = Field(..., min_length=1, max_length=20)
    full_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Staff member's chosen display name (can differ from invite name)",
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Must be at least 8 characters",
    )
    phone: Optional[str] = Field(
        None, max_length=30, description="Optional contact number"
    )


class StaffInviteSetupResponse(BaseModel):
    """Returned after successful account creation."""

    user_id: str
    email: str
    full_name: str
    role: str
    hospital_name: str
    message: str = "Account created successfully. You can now log in."


# ── Admin: List Invites ──────────────────────────────────────────────────────


class StaffInviteOut(BaseModel):
    """Summary of an invite for admin dashboards."""

    id: str
    email: str
    staff_name: str
    role: str
    status: str
    access_code: str
    expires_at: datetime
    created_at: datetime
    inviter_name: Optional[str] = None


__all__ = [
    "StaffInviteCreateRequest",
    "StaffInviteCreateResponse",
    "StaffInviteVerifyRequest",
    "StaffInviteVerifyResponse",
    "StaffInviteSetupRequest",
    "StaffInviteSetupResponse",
    "StaffInviteOut",
]
