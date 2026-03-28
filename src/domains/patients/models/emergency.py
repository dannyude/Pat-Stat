"""Emergency flag model for urgent patient alerts."""

from sqlalchemy import Boolean, Column, DateTime, Enum as SAEnum, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow
from src.domains.patients.enums import EmergencyPriority


class EmergencyFlag(Base, UUIDPrimaryKey):
    """Marks an admission as requiring urgent/emergency attention."""

    __tablename__ = "emergency_flags"

    admission_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "admissions.id", ondelete="CASCADE", name="fk_emergency_flag_admission_id"
        ),
        nullable=False,
        index=True,
    )
    flagged_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_emergency_flag_flagged_by_id"
        ),
        nullable=True,
    )
    priority = Column(
        SAEnum(EmergencyPriority),
        nullable=False,
        default=EmergencyPriority.high,
    )
    reason = Column(Text, nullable=False)
    is_resolved = Column(Boolean, default=False, nullable=False)
    
    # [Logic]: Tracks who deactivated the alert. Can be different from who flagged it.
    resolved_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_emergency_flag_resolved_by_id"
        ),
        nullable=True,
    )
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    admission = relationship("Admission", back_populates="emergency_flags")
    flagged_by = relationship("User", foreign_keys=[flagged_by_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])

    __table_args__ = (
        Index("ix_emergency_flags_unresolved", "is_resolved"),
        Index("ix_emergency_flags_admission", "admission_id", "is_resolved"),
    )


__all__ = ["EmergencyFlag"]
