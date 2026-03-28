"""Identity models for the patients domain."""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey, utcnow
from src.domains.patients.enums import BloodGroup


class PatientProfile(Base, UUIDPrimaryKey, TimestampMixin):
    """
    Global patient identity record (the human, not the admission).
    [Architecture/DDD]: This entity persists across multiple hospital visits.
    All dynamic clinical state (ward, bed_number, diagnosis, assigned doctors) 
    is managed on the `Admission` entity, not here.
    """

    __tablename__ = "patient_profiles"

    pat_stat_id = Column(String(20), unique=True, nullable=False, index=True)
    national_id = Column(String(50), unique=True, nullable=True, index=True)

    full_name = Column(String(255), nullable=False)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(10), nullable=True)
    phone = Column(String(30), nullable=True, index=True)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)

    blood_group = Column(SAEnum(BloodGroup), nullable=True, default=BloodGroup.unknown)
    allergies = Column(Text, nullable=True)
    chronic_conditions = Column(Text, nullable=True)
    emergency_contact_name = Column(String(255), nullable=True)
    emergency_contact_phone = Column(String(30), nullable=True)

    registered_by_hospital_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "hospitals.id",
            ondelete="SET NULL",
            name="fk_patient_registered_hospital",
        ),
        nullable=True,
        index=True,
    )
    is_active = Column(Boolean, default=True, nullable=False)

    admissions = relationship(
        "Admission",
        back_populates="patient",
        cascade="all, delete-orphan",
        order_by="desc(Admission.admitted_at)",
    )
    emergency_flags = relationship(
        "EmergencyFlag",
        secondary="admissions",
        primaryjoin="PatientProfile.id == Admission.patient_id",
        secondaryjoin="Admission.id == EmergencyFlag.admission_id",
        viewonly=True,
        order_by="desc(EmergencyFlag.created_at)",
    )
    shift_handovers = relationship(
        "ShiftHandover",
        secondary="admissions",
        primaryjoin="PatientProfile.id == Admission.patient_id",
        secondaryjoin="Admission.id == ShiftHandover.admission_id",
        viewonly=True,
        order_by="desc(ShiftHandover.created_at)",
    )
    family_links = relationship(
        "FamilyPatientLink", back_populates="patient", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_patient_profiles_active", "is_active"),
        Index("ix_patient_profiles_name", "full_name"),
        Index("ix_patient_profiles_phone_dob", "phone", "date_of_birth"),
    )

    @property
    def active_admission(self):
        """
        Return current admission, falling back to most recent if needed.
        [Performance/UI]: Used heavily to render the current state of a patient in
        list views without explicitly fetching the active admission everywhere.
        """
        if not self.admissions:
            return None
        for admission in self.admissions:
            if admission.discharged_at is None:
                return admission
        return self.admissions[0]

    @property
    def ward(self):
        admission = self.active_admission
        return admission.ward if admission else None

    @property
    def bed_number(self):
        admission = self.active_admission
        return admission.bed_number if admission else None

    @property
    def diagnosis(self):
        admission = self.active_admission
        return admission.diagnosis if admission else None

    @property
    def status(self):
        admission = self.active_admission
        return admission.status if admission else None

    @property
    def admitted_at(self):
        admission = self.active_admission
        return admission.admitted_at if admission else None

    @property
    def discharged_at(self):
        admission = self.active_admission
        return admission.discharged_at if admission else None

    @property
    def hospital_id(self):
        admission = self.active_admission
        return admission.hospital_id if admission else None

    @property
    def has_active_admission(self) -> bool:
        return (
            self.active_admission is not None
            and self.active_admission.discharged_at is None
        )


class FamilyPatientLink(Base, UUIDPrimaryKey):
    """
    Grants a family member read access to a patient.
    [Security/RBAC]: This acts as the authorization gateway for the Family mobile app.
    If a link doesn't exist here, the family user cannot view the patient.
    """

    __tablename__ = "family_patient_links"

    family_user_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
            name="fk_family_patient_link_family_user_id",
        ),
        nullable=False,
    )
    patient_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "patient_profiles.id",
            ondelete="CASCADE",
            name="fk_family_patient_link_patient_id",
        ),
        nullable=False,
    )
    relationship_to_patient = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    family_user = relationship("User", back_populates="family_links")
    patient = relationship("PatientProfile", back_populates="family_links")

    __table_args__ = (
        UniqueConstraint("family_user_id", "patient_id", name="uq_family_patient_link"),
        # [Performance]: Celery's notify_family_of_update filters on patient_id
        # for every clinical update — needs an index for fast reverse-lookup.
        Index("ix_family_patient_links_patient_id", "patient_id"),
    )


__all__ = ["PatientProfile", "FamilyPatientLink"]
