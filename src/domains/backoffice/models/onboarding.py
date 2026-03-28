import enum

from sqlalchemy import (
    Boolean,
    Column,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey


class DocumentType(str, enum.Enum):
    """Document classes submitted by hospitals for onboarding verification."""

    cac_certificate = "CAC Certificate"
    tax_clearance = "Tax Clearance Certificate"
    medical_license = "Medical Practice License"
    ownership_proof = "Proof of Ownership"
    accreditation_letter = "Accreditation Letter"
    other = "Other"


class HospitalApplication(Base, UUIDPrimaryKey, TimestampMixin):
    """Onboarding submission data linked one-to-one to an existing hospital record."""

    __tablename__ = "hospital_applications"

    hospital_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "hospitals.id",
            ondelete="CASCADE",
            name="fk_hospital_application_hospital_id",
        ),
        unique=True,
        nullable=False,
        index=True,
    )

    admin_full_name = Column(String(255), nullable=False)
    admin_email = Column(String(255), nullable=False)
    admin_phone = Column(String(30), nullable=True)
    admin_title = Column(String(100), nullable=True)

    hospital_type = Column(String(100), nullable=True)
    bed_capacity = Column(Integer, nullable=True)
    year_established = Column(Integer, nullable=True)
    cac_number = Column(String(100), nullable=True)
    mdcn_number = Column(String(100), nullable=True)

    reason_for_joining = Column(Text, nullable=True)
    additional_notes = Column(Text, nullable=True)

    assigned_reviewer_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_hospital_application_assigned_reviewer_id",
        ),
        nullable=True,
    )
    internal_notes = Column(Text, nullable=True)
    priority = Column(String(20), nullable=False, default="normal")

    hospital = relationship("Hospital")
    documents = relationship(
        "HospitalDocument",
        back_populates="application",
        cascade="all, delete-orphan",
    )
    assigned_reviewer = relationship("User", foreign_keys=[assigned_reviewer_id])


class HospitalDocument(Base, UUIDPrimaryKey, TimestampMixin):
    """Compliance document metadata associated with a hospital application."""

    __tablename__ = "hospital_documents"

    application_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "hospital_applications.id",
            ondelete="CASCADE",
            name="fk_hospital_document_application_id",
        ),
        nullable=False,
        index=True,
    )
    uploaded_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_hospital_document_uploaded_by_id"
        ),
        nullable=True,
    )

    document_type = Column(SAEnum(DocumentType), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_url = Column(String(500), nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)

    is_verified = Column(Boolean, nullable=True)
    reviewer_note = Column(Text, nullable=True)

    application = relationship("HospitalApplication", back_populates="documents")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])

    __table_args__ = (Index("ix_hospital_documents_application", "application_id"),)


__all__ = ["DocumentType", "HospitalApplication", "HospitalDocument"]
