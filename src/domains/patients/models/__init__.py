"""Patient domain models — public re-exports."""

from src.domains.patients.models.audit import (
    NotificationCategory,
    NotificationDeliveryStatus,
    NotificationLog,
)
from src.domains.patients.models.clinical import (
    Admission,
    ClinicalUpdate,
    StaffNote,
)
from src.domains.patients.models.emergency import EmergencyFlag
from src.domains.patients.models.handover import ShiftHandover
from src.domains.patients.models.identity import FamilyPatientLink, PatientProfile

# Legacy alias so existing code that imports ``Patient`` keeps working.
Patient = PatientProfile

__all__ = [
    "PatientProfile",
    "Patient",
    "Admission",
    "ClinicalUpdate",
    "StaffNote",
    "EmergencyFlag",
    "ShiftHandover",
    "FamilyPatientLink",
    "NotificationCategory",
    "NotificationDeliveryStatus",
    "NotificationLog",
]
