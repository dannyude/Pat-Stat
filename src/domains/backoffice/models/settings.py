"""Platform-wide settings (singleton row pattern).

Only one row is ever expected to exist in this table — it stores the
global configuration that super-admins can tune from the backoffice UI.
Accessing it through :func:`get_or_create_platform_settings` in the
services layer guarantees the singleton invariant at the application
level; the database itself does not enforce it.
"""

from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from src.core.database import Base
from src.core.mixins import TimestampMixin, UUIDPrimaryKey


class PlatformSettings(Base, UUIDPrimaryKey, TimestampMixin):
    """Singleton configuration row for global platform settings."""

    __tablename__ = "platform_settings"

    platform_name = Column(String(100), nullable=False, default="Pat-Stat")
    support_email = Column(String(255), nullable=True)
    default_region = Column(String(100), nullable=True)

    # Audit trail — which super-admin last touched the settings.
    updated_by_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_platform_settings_updated_by_id",
        ),
        nullable=True,
    )

    updated_by = relationship("User", foreign_keys=[updated_by_id])


__all__ = ["PlatformSettings"]
