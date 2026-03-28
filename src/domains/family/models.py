"""Family domain models."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow
from src.domains.patients.models import FamilyPatientLink


class FamilyInvite(Base, UUIDPrimaryKey):
    """One-time invitation for family access to a patient."""

    __tablename__ = "family_invites"

    patient_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "patient_profiles.id",
            ondelete="CASCADE",
            name="fk_family_invite_patient_id",
        ),
        nullable=False,
        index=True,
    )
    inviter_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_family_invite_inviter_user_id"
        ),
        nullable=True,
        index=True,
    )
    accepted_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_family_invite_accepted_user_id"
        ),
        nullable=True,
        index=True,
    )
    email = Column(String(255), nullable=False, index=True)
    family_member_name = Column(String(255), nullable=True)
    phone = Column(String(30), nullable=True)
    relationship_to_patient = Column(String(100), nullable=True)
    
    # [Security]: We never store the raw URL token in the database.
    # We store a SHA-256 hash to prevent invite hijacking if the DB leaks.
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    
    # [Security]: A short (typically 8-char alphanumeric) code required in addition
    # to the URL token. Passed to the user via a secondary channel or displayed on
    # the success screen for the inviter to share verbally.
    access_code = Column(String(20), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    patient = relationship("PatientProfile")
    inviter = relationship("User", foreign_keys=[inviter_user_id])
    accepted_user = relationship("User", foreign_keys=[accepted_user_id])

    __table_args__ = (
        Index("ix_family_invites_patient_status", "patient_id", "status"),
    )


__all__ = ["FamilyPatientLink", "FamilyInvite"]
