"""Backoffice Models Registry.

Imports all sub-models to ensure SQLAlchemy registers them properly for
Alembic and relationships.
"""

from src.domains.backoffice.models.audit import (
    HospitalVerificationEvent,
    SuperAdminActionLog,
    VerificationEventType,
)
from src.domains.backoffice.models.onboarding import (
    DocumentType,
    HospitalApplication,
    HospitalDocument,
)

__all__ = [
    "DocumentType",
    "HospitalApplication",
    "HospitalDocument",
    "VerificationEventType",
    "HospitalVerificationEvent",
    "SuperAdminActionLog",
]
