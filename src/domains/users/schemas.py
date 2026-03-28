"""Users domain — native Pydantic schemas."""

from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

from src.domains.users.enums import UserRole


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: UserRole
    phone: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_token: Optional[str] = None
    device_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v


class UpdateMeRequest(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


class DeviceTokenRegister(BaseModel):
    token: str
    device_name: Optional[str] = None


class PatStatStaffOut(BaseModel):
    """
    Response payload for Staff/Admin User entities.
    Enforces that hospital-scoped users always return their hospital info.
    """

    id: UUID
    email: EmailStr
    full_name: str
    role: Literal[UserRole.admin, UserRole.doctor, UserRole.nurse]
    is_active: bool
    hospital_id: Optional[UUID] = None
    hospital_name: Optional[str] = None
    avatar_url: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class PatStatFamilyOut(BaseModel):
    """
    Response payload for Family User entities.
    Enforces that family tags directly to patients without a hospital organization scope.
    """

    id: UUID
    email: EmailStr
    full_name: str
    role: Literal[UserRole.family]
    is_active: bool
    hospital_name: Optional[str] = None
    avatar_url: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


class PatStatSuperAdminOut(BaseModel):
    """
    Response payload for Super Admin (HQ) User entities.
    Enforces that headquarters staff never have a hospital scope.
    """

    id: UUID
    email: EmailStr
    full_name: str
    role: Literal[UserRole.super_admin]
    is_active: bool
    hospital_name: Optional[str] = None
    avatar_url: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime
    model_config = {"from_attributes": True}


# Discriminated Union: Pydantic automatically selects the correct schema based on the 'role' field
UserOut = Annotated[
    Union[PatStatStaffOut, PatStatFamilyOut, PatStatSuperAdminOut],
    Field(discriminator="role"),
]


__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "TokenResponse",
    "RefreshRequest",
    "ChangePasswordRequest",
    "UpdateMeRequest",
    "DeviceTokenRegister",
    "UserOut",
]
