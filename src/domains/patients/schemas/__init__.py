"""Patients domain schemas — public re-exports.

All schemas are importable from ``src.domains.patients.schemas`` exactly as
before; existing import paths will not break.
"""

from src.domains.patients.schemas.clinical import (
    CareTeamMemberOut,
    ClinicalUpdateCreate,
    ClinicalUpdateOut,
    NoteCategoryCatalogOut,
    NoteCategoryOptionOut,
    StaffNoteCreate,
    StaffNoteOut,
)
from src.domains.patients.schemas.dashboard import (
    ActivityFeedItemOut,
    CriticalPatientOut,
    DashboardSummaryOut,
    NeedsAttentionPatientOut,
)
from src.domains.patients.schemas.emergency import (
    EmergencyFlagCreate,
    EmergencyFlagOut,
    EmergencyFlagResolve,
)
from src.domains.patients.schemas.handover import ShiftHandoverCreate, ShiftHandoverOut
from src.domains.patients.schemas.notification import NotificationOut
from src.domains.patients.schemas.patient import (
    AdmissionCreate,
    PatientCreate,
    PatientOut,
    PatientProfileCreate,
    PatientResponse,
    PatientSearchResponse,
    PatientUpdate,
    PatientWithTeamOut,
)

__all__ = [
    # patient
    "PatientProfileCreate",
    "AdmissionCreate",
    "PatientCreate",
    "PatientUpdate",
    "PatientOut",
    "PatientSearchResponse",
    "PatientWithTeamOut",
    "PatientResponse",
    # clinical
    "ClinicalUpdateCreate",
    "ClinicalUpdateOut",
    "NoteCategoryOptionOut",
    "NoteCategoryCatalogOut",
    "CareTeamMemberOut",
    "StaffNoteCreate",
    "StaffNoteOut",
    # dashboard
    "DashboardSummaryOut",
    "CriticalPatientOut",
    "NeedsAttentionPatientOut",
    "ActivityFeedItemOut",
    # emergency
    "EmergencyFlagCreate",
    "EmergencyFlagOut",
    "EmergencyFlagResolve",
    # handover
    "ShiftHandoverCreate",
    "ShiftHandoverOut",
    # notification
    "NotificationOut",
]
