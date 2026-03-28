"""Users domain - native SQLAlchemy models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy import Enum as SAEnum

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey, utcnow
from src.domains.users.enums import UserRole


class User(Base, UUIDPrimaryKey, TimestampMixin):
    """
    Core identity model used for Authentication and RBAC.
    Represents standard Staff (Doctors, Nurses), Admins, Family members,
    and platform Super Admins.
    """
    __tablename__ = "users"

    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=False)
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.family)
    is_active = Column(Boolean, default=True, nullable=False)
    avatar_url = Column(String(500), nullable=True)
    phone = Column(String(30), nullable=True)

    # [DB/Design]: `hospital_id` is nullable.
    # 1. Platform `super_admin`s belong to no specific hospital.
    # 2. `family` role users are linked directly to `patients` via `FamilyPatientLink`,
    #    so they also have a null `hospital_id`.
    # 3. Clinical staff MUST have this set.
    hospital_id = Column(
        UUID(as_uuid=True),
        ForeignKey("hospitals.id", ondelete="SET NULL", name="fk_user_hospital_id"),
        nullable=True,
        index=True,
    )
    hospital = relationship(
        "Hospital", back_populates="users", foreign_keys=[hospital_id]
    )

    device_tokens = relationship(
        "DeviceToken", back_populates="user", cascade="all, delete-orphan"
    )
    care_assignments = relationship(
        "CareAssignment", back_populates="staff", foreign_keys="CareAssignment.staff_id"
    )
    family_links = relationship("FamilyPatientLink", back_populates="family_user")
    updates = relationship("ClinicalUpdate", back_populates="authored_by")
    staff_notes = relationship("StaffNote", back_populates="authored_by")

    @property
    def hospital_name(self) -> str | None:
        """Return the hospital name for serialisation into UserOut."""
        return self.hospital.name if self.hospital else None


class DeviceToken(Base, UUIDPrimaryKey):
    """FCM push token per device."""

    __tablename__ = "device_tokens"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_device_token_user_id"),
        nullable=False,
    )
    # [Design]: Tracks the FCM token provided by mobile devices for push notifications.
    # A user can have multiple devices (e.g., an iPad and an iPhone) concurrently.
    token = Column(String(500), nullable=False, unique=True)
    device_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    
    # [Logic]: Upserted on login/refresh. Used to prune dead tokens.
    last_used_at = Column(DateTime(timezone=True), default=utcnow)

    user = relationship("User", back_populates="device_tokens")


__all__ = ["User", "DeviceToken", "UserRole"]
