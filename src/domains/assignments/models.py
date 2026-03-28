"""Care assignment infrastructure - native SQLAlchemy model."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


class CareAssignment(Base, UUIDPrimaryKey):
    """
    Maps a doctor/nurse to a specific Admission (not globally to a patient).
    Dr. Tobenna is assigned to *this admission* at Lagos General.
    He is NOT automatically assigned if the patient is re-admitted elsewhere.
    """

    __tablename__ = "care_assignments"

    admission_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "admissions.id", ondelete="CASCADE", name="fk_care_assignment_admission_id"
        ),
        nullable=False,
    )
    staff_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_care_assignment_staff_id"),
        nullable=False,
    )
    is_primary = Column(Boolean, default=False)
    assigned_at = Column(DateTime(timezone=True), default=utcnow)
    unassigned_at = Column(DateTime(timezone=True), nullable=True)

    admission = relationship("Admission", back_populates="care_assignments")
    staff = relationship("User", back_populates="care_assignments")

    __table_args__ = (
        Index("ix_care_assignments_staff", "staff_id"),
        Index("ix_care_assignments_admission", "admission_id"),
        # [DB]: A single staff member can only be assigned ONCE to a given admission.
        UniqueConstraint(
            "admission_id", "staff_id", name="uq_care_assignment_admission_staff"
        ),
    )


__all__ = ["CareAssignment"]
