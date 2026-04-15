"""Audit and notification log models for the patients domain."""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


class NotificationCategory(str, enum.Enum):
    """Categories that map to the frontend notification tabs.

    [Design]: Using a Python str enum (not a DB-level ENUM) gives us the
    ability to add new categories without an ALTER TYPE migration. The DB
    column is VARCHAR(30) — validation happens at the application layer.

    Mapping to Figma tabs:
        Critical Alerts → critical_alert
        System Alerts   → system
        Shift Logs      → shift_log
        (uncategorized) → general
    """

    critical_alert = "critical_alert"  # Emergency flags, critical status changes
    system = "system"  # Staff invites, account changes, system notices
    shift_log = "shift_log"  # Shift handovers, routine clinical updates
    general = "general"  # Default catch-all


class NotificationLog(Base, UUIDPrimaryKey):
    """
    Audit trail for outbound patient-related notifications.
    [DB/Design]: Also powers the in-app notification bell.
    """

    __tablename__ = "notification_logs"

    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_notification_logs_user_id"),
        nullable=False,
        index=True,
    )
    patient_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "patient_profiles.id",
            ondelete="CASCADE",
            name="fk_notification_logs_patient_id",
        ),
        nullable=True,
    )
    update_id = Column(
        UUID(as_uuid=False),
        ForeignKey(
            "clinical_updates.id",
            ondelete="SET NULL",
            name="fk_notification_logs_update_id",
        ),
        nullable=True,
    )
    title = Column(String(255), nullable=False)
    body = Column(Text, nullable=False)

    # [Design]: Category maps to the frontend tab the notification appears in.
    # Stored as a string (not a DB ENUM) so new categories can be added without
    # a schema migration. Defaults to 'general' for backward compatibility with
    # existing rows that predate the category column.
    category = Column(
        String(30), nullable=False, default="general", server_default="general"
    )

    is_read = Column(Boolean, default=False)
    sent_at = Column(DateTime(timezone=True), default=utcnow)
    read_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # [Performance/DB]: Composite index on (user_id, is_read) optimizes the
        # unread count queries shown in the UI sidebar.
        Index("ix_notification_logs_user_unread", "user_id", "is_read"),
        # [Performance/DB]: Composite index on (user_id, category) optimizes the
        # filtered tab queries ("show me only critical alerts for this user").
        Index("ix_notification_logs_user_category", "user_id", "category"),
    )


__all__ = ["NotificationCategory", "NotificationLog"]
