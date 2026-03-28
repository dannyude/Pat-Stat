"""
Patients domain enumerations tracking clinical state, notes, and flags.
"""

import enum


class PatientStatus(str, enum.Enum):
    getting_better = "Getting Better"
    stable = "Stable"
    being_monitored = "Being Monitored"
    critical = "Critical"
    discharged = "Discharged"


class BloodGroup(str, enum.Enum):
    a_pos = "A+"
    a_neg = "A-"
    b_pos = "B+"
    b_neg = "B-"
    ab_pos = "AB+"
    ab_neg = "AB-"
    o_pos = "O+"
    o_neg = "O-"
    unknown = "Unknown"


class NoteCategory(str, enum.Enum):
    general = "General"
    handover = "Handover"
    procedure = "Procedure"
    consultation = "Consultation"
    lab_result = "Lab Result"
    urgent = "Urgent"


class EmergencyPriority(str, enum.Enum):
    high = "High"
    critical = "Critical"
