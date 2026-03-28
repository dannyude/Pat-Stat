"""
User role enumerations for Role-Based Access Control (RBAC).
Defines the authorization boundaries across the system.
"""

import enum


class UserRole(str, enum.Enum):
    super_admin = "super_admin"  # PatStat platform staff — no hospital_id
    admin = "admin"  # Hospital admin
    doctor = "doctor"
    nurse = "nurse"
    family = "family"
