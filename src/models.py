"""
Master model registry — import every ORM model here so SQLAlchemy can
resolve all relationships before metadata is accessed or tables are created.

Usage:
    from src.models import *   # noqa: F401, F403  (in Alembic env or init_db)
"""

# Order of import matters: Hospital before User, User before Patient, etc.
from src.domains.hospital.models import Hospital, HospitalIdentifier  # noqa: F401
from src.domains.users.models import User, DeviceToken, UserRole  # noqa: F401
from src.domains.patients.models import (  # noqa: F401
    PatientProfile,
    Patient,
    FamilyPatientLink,
    NotificationLog,
    Admission,
    ClinicalUpdate,
    StaffNote,
    EmergencyFlag,
    ShiftHandover,
)
from src.domains.assignments.models import CareAssignment  # noqa: F401
from src.domains.backoffice.models import (  # noqa: F401
    DocumentType,
    VerificationEventType,
    HospitalApplication,
    HospitalDocument,
    HospitalVerificationEvent,
    SuperAdminActionLog,
)
from src.domains.family.models import FamilyInvite  # noqa: F401
from src.domains.staff_invites.models import StaffInvite  # noqa: F401
from src.domains.contact_sales.models import ContactSalesSubmission  # noqa: F401

__all__ = [
    "Hospital",
    "HospitalIdentifier",
    "User",
    "DeviceToken",
    "UserRole",
    "PatientProfile",
    "Patient",
    "FamilyPatientLink",
    "NotificationLog",
    "Admission",
    "ClinicalUpdate",
    "StaffNote",
    "EmergencyFlag",
    "ShiftHandover",
    "CareAssignment",
    "DocumentType",
    "VerificationEventType",
    "HospitalApplication",
    "HospitalDocument",
    "HospitalVerificationEvent",
    "SuperAdminActionLog",
    "FamilyInvite",
    "StaffInvite",
    "ContactSalesSubmission",
]
