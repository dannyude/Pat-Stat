"""Shift handover model for end-of-shift clinical summaries."""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


class ShiftHandover(Base, UUIDPrimaryKey):
    """
    End-of-shift clinical summary handed from one staff member to another.
    Linked to an admission to maintain historical records when patients move wards.
    """

    __tablename__ = "shift_handovers"

    admission_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "admissions.id", ondelete="CASCADE", name="fk_shift_handover_admission_id"
        ),
        nullable=False,
        index=True,
    )
    from_staff_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_shift_handover_from_staff_id"
        ),
        nullable=True,
    )
    to_staff_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_shift_handover_to_staff_id"
        ),
        nullable=True,
    )
    summary = Column(Text, nullable=False)
    pending_actions = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    admission = relationship("Admission", back_populates="shift_handovers")
    from_staff = relationship("User", foreign_keys=[from_staff_id])
    to_staff = relationship("User", foreign_keys=[to_staff_id])

    __table_args__ = (
        Index("ix_shift_handovers_admission", "admission_id"),
        Index("ix_shift_handovers_created", "created_at"),
    )


__all__ = ["ShiftHandover"]
