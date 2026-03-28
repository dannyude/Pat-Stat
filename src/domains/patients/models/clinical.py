"""Clinical episode models for the patients domain."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey, utcnow
from src.domains.patients.enums import NoteCategory, PatientStatus


class Admission(Base, UUIDPrimaryKey, TimestampMixin):
    """
    A single clinical episode for a patient at one hospital.
    [Architecture/DDD]: This is the true Aggregate Root for clinical workflows.
    Staff assignments, clinical updates, flags, and handovers belong to the ADMISSION,
    not the patient. This ensures history is preserved when a patient is re-admitted later.
    """

    __tablename__ = "admissions"

    patient_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "patient_profiles.id",
            ondelete="CASCADE",
            name="fk_admission_patient_id",
        ),
        nullable=False,
        index=True,
    )
    hospital_id = Column(
        UUID(as_uuid=False),
        ForeignKey("hospitals.id", ondelete="CASCADE", name="fk_admission_hospital_id"),
        nullable=False,
        index=True,
    )

    ward = Column(String(120), nullable=False)
    bed_number = Column(String(20), nullable=False)
    diagnosis = Column(Text, nullable=False)
    status = Column(
        SAEnum(PatientStatus),
        nullable=False,
        default=PatientStatus.being_monitored,
    )
    primary_doctor_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_admission_primary_doctor_id"
        ),
        nullable=True,
        index=True,
    )

    admitted_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    
    # [Logic]: A null discharged_at indicates this is the currently ACTIVE admission
    # for the patient. A patient can only have one active admission at a time.
    discharged_at = Column(DateTime(timezone=True), nullable=True)

    admitted_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    patient = relationship("PatientProfile", back_populates="admissions")
    hospital = relationship("Hospital", back_populates="admissions")
    primary_doctor = relationship("User", foreign_keys=[primary_doctor_id])
    updates = relationship(
        "ClinicalUpdate",
        back_populates="admission",
        cascade="all, delete-orphan",
        order_by="ClinicalUpdate.created_at.desc()",
    )
    admitted_by = relationship("User", foreign_keys=[admitted_by_id])
    care_assignments = relationship(
        "CareAssignment", back_populates="admission", cascade="all, delete-orphan"
    )
    staff_notes = relationship(
        "StaffNote",
        back_populates="admission",
        cascade="all, delete-orphan",
        order_by="StaffNote.created_at.desc()",
    )
    emergency_flags = relationship(
        "EmergencyFlag",
        back_populates="admission",
        cascade="all, delete-orphan",
        order_by="EmergencyFlag.created_at.desc()",
    )
    shift_handovers = relationship(
        "ShiftHandover",
        back_populates="admission",
        cascade="all, delete-orphan",
        order_by="ShiftHandover.created_at.desc()",
    )

    __table_args__ = (
        Index("ix_admissions_hospital_status", "hospital_id", "status"),
        Index("ix_admissions_patient_hospital", "patient_id", "hospital_id"),
        Index("ix_admissions_discharged_at", "discharged_at"),
    )

    @property
    def pat_stat_id(self):
        return self.patient.pat_stat_id if self.patient else None

    @property
    def full_name(self):
        return self.patient.full_name if self.patient else None

    @property
    def date_of_birth(self):
        return self.patient.date_of_birth if self.patient else None

    @property
    def phone(self):
        return self.patient.phone if self.patient else None

    @property
    def has_active_admission(self) -> bool:
        return self.discharged_at is None


class ClinicalUpdate(Base, UUIDPrimaryKey):
    """Point-in-time vitals and clinical notes for an admission."""

    __tablename__ = "clinical_updates"

    admission_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "admissions.id", ondelete="CASCADE", name="fk_clinical_update_admission_id"
        ),
        nullable=False,
        index=True,
    )
    authored_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_clinical_update_authored_by_id"
        ),
        nullable=True,
    )
    status = Column(SAEnum(PatientStatus), nullable=False)
    note = Column(Text, nullable=False)
    blood_pressure = Column(String(20), nullable=True)
    heart_rate = Column(String(20), nullable=True)
    temperature = Column(String(20), nullable=True)
    oxygen_level = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    admission = relationship("Admission", back_populates="updates")
    authored_by = relationship("User", back_populates="updates")

    __table_args__ = (
        Index("ix_clinical_updates_created", "created_at"),
        Index("ix_clinical_updates_admission_created", "admission_id", "created_at"),
    )


class StaffNote(Base, UUIDPrimaryKey, TimestampMixin):
    """
    Internal clinical notes for an admission (not family-facing).
    [Access Control]: These are completely hidden from the family app.
    """

    __tablename__ = "staff_notes"

    admission_id = Column(
        UUID(as_uuid=False),
        ForeignKey("admissions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    authored_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    content = Column(Text, nullable=False)
    category = Column(
        SAEnum(NoteCategory), nullable=False, default=NoteCategory.general
    )
    is_urgent = Column(Boolean, default=False)

    admission = relationship("Admission", back_populates="staff_notes")
    authored_by = relationship("User", back_populates="staff_notes")

    __table_args__ = (
        Index("ix_staff_notes_admission_urgent", "admission_id", "is_urgent"),
    )


__all__ = ["Admission", "ClinicalUpdate", "StaffNote"]
