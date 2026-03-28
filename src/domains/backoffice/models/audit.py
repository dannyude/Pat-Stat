import enum

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


class VerificationEventType(str, enum.Enum):
    """Internal event stream of each onboarding and verification action."""

    application_submitted = "Application Submitted"
    document_uploaded = "Document Uploaded"
    moved_to_review = "Moved to Under Review"
    verification_note_added = "Note Added"
    approved = "Approved"
    rejected = "Rejected"
    suspended = "Suspended"
    reinstated = "Reinstated"


class HospitalVerificationEvent(Base, UUIDPrimaryKey):
    """Append-only verification timeline for each hospital onboarding lifecycle."""

    __tablename__ = "hospital_verification_events"

    hospital_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "hospitals.id",
            ondelete="CASCADE",
            name="fk_hospital_verification_event_hospital_id",
        ),
        nullable=False,
        index=True,
    )
    actor_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_hospital_verification_event_actor_id",
        ),
        nullable=True,
    )

    event_type = Column(SAEnum(VerificationEventType), nullable=False)
    note = Column(Text, nullable=True)
    event_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    hospital = relationship("Hospital")
    actor = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_verification_events_hospital_time", "hospital_id", "created_at"),
    )


class SuperAdminActionLog(Base, UUIDPrimaryKey):
    """Global immutable audit trail of super-admin platform actions."""

    __tablename__ = "super_admin_action_logs"

    actor_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_super_admin_action_log_actor_id"
        ),
        nullable=True,
    )
    action = Column(String(100), nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(100), nullable=True)
    note = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    action_metadata = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    actor = relationship("User", foreign_keys=[actor_id])

    __table_args__ = (
        Index("ix_super_admin_log_actor", "actor_id"),
        Index("ix_super_admin_log_time", "created_at"),
        Index("ix_super_admin_log_action", "action"),
    )


__all__ = [
    "VerificationEventType",
    "HospitalVerificationEvent",
    "SuperAdminActionLog",
]
