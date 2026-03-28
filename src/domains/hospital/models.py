"""Hospital infrastructure — native SQLAlchemy models."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import TimestampMixin, utcnow


class Hospital(Base, TimestampMixin):
    """
    Hospital registration and configuration.
    [Architecture]: This is the ROOT multi-tenancy entity. Almost all other entities
    (Admissions, Staff, Flags) are scoped to a specific Hospital ID to enforce data isolation.
    """

    __tablename__ = "hospitals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    address = Column(JSONB, nullable=False)
    phone = Column(String(20), nullable=False)
    email = Column(String(255), nullable=False)
    hospital_code = Column(String(20), unique=True, nullable=False)
    parent_hospital_id = Column(UUID(as_uuid=True), nullable=True)
    cac_registration_number = Column(String(20))
    firs_tin = Column(String(20))
    hospital_license_number = Column(String(50))
    hierarchy_level = Column(
        String(20),
        CheckConstraint(
            "hierarchy_level IN ('primary', 'secondary', 'tertiary')",
            name="ck_hospital_hierarchy_level_valid",
        ),
        default="secondary",
    )
    state = Column(String(50), nullable=False)
    lga = Column(String(100))
    subdomain = Column(String(63), unique=True, nullable=False)
    is_subdomain_verified = Column(Boolean, default=False)
    subdomain_verified_at = Column(DateTime)
    custom_domain = Column(String(255))
    is_custom_domain_verified = Column(Boolean, default=False)
    custom_domain_verified_at = Column(DateTime)
    theme_json = Column(JSONB, default={})
    enabled_features = Column(
        JSONB,
        default={"vital_signs": True, "medical_records": True, "medications": True},
    )
    subscription_tier = Column(
        String(20),
        CheckConstraint(
            "subscription_tier IN ('small', 'medium', 'large')",
            name="ck_hospital_subscription_tier_valid",
        ),
        default="small",
    )
    status = Column(
        String(50),
        CheckConstraint(
            "status IN ('pending_verification', 'active', 'suspended', 'inactive')",
            name="ck_hospital_status_valid",
        ),
        default="pending_verification",
        nullable=False,
    )
    verification_status = Column(
        String(30),
        CheckConstraint(
            "verification_status IN ('pending_review', 'documents_required', 'verified', 'rejected', 'suspended')",
            name="ck_hospital_verification_status_valid",
        ),
        default="pending_review",
    )
    verified_by_admin_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_hospital_verified_by_id"),
        nullable=True,
    )
    verified_at = Column(DateTime(timezone=True), nullable=True)
    settings = Column(JSONB, default={})

    users = relationship(
        "User",
        back_populates="hospital",
        cascade="all, delete-orphan",
        foreign_keys="User.hospital_id",
    )
    admissions = relationship(
        "Admission", back_populates="hospital", cascade="all, delete-orphan"
    )
    identifiers = relationship(
        "HospitalIdentifier", back_populates="hospital", cascade="all, delete-orphan"
    )
    verified_by = relationship("User", foreign_keys=[verified_by_admin_id])

    __table_args__ = (Index("idx_hospitals_status", "status"),)


class HospitalIdentifier(Base):
    """
    Stores arbitrary unique identifiers for a hospital (e.g. legacy system IDs).
    [Design]: Supports migrating or integrating with existing systems without schema changes.
    """

    __tablename__ = "hospital_identifiers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "hospitals.id",
            ondelete="CASCADE",
            name="fk_hospital_identifier_hospital_id",
        ),
        nullable=False,
    )
    identifier_type = Column(String(50), nullable=False)
    identifier_value = Column(String(100), nullable=False)
    is_primary = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)

    hospital = relationship("Hospital", back_populates="identifiers")

    __table_args__ = (Index("ix_hospital_identifiers_hospital", "hospital_id"),)


__all__ = ["Hospital", "HospitalIdentifier"]
