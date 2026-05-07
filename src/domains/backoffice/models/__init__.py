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
from src.domains.backoffice.models.settings import PlatformSettings

__all__ = [
    "DocumentType",
    "HospitalApplication",
    "HospitalDocument",
    "VerificationEventType",
    "HospitalVerificationEvent",
    "SuperAdminActionLog",
    "PlatformSettings",
]
