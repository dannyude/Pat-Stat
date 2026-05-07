"""Audit and notification log models for the patients domain."""

import enum

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


class NotificationCategory(str, enum.Enum):
    """Maps to the three Figma notification tabs + a catch-all default.

    Using a Python-level enum (stored as a plain String column) rather than a
    PostgreSQL ENUM so that adding a new category is a one-line code change —
    no ``ALTER TYPE`` migration required.
    """

    critical_alert = "critical_alert"
    system = "system"
    shift_log = "shift_log"
    general = "general"


class NotificationDeliveryStatus(str, enum.Enum):
    """Lifecycle state of a single notification's outbound push attempt.

    Stored as a plain String column for the same reason as NotificationCategory —
    adding a state should never require an ALTER TYPE migration.
    """

    queued = "queued"  # NotificationLog row written, FCM not yet attempted
    skipped_routine = "skipped_routine"  # Tier=routine, in-app only, no push
    deferred_quiet_hours = "deferred_quiet_hours"  # Reserved for future per-user quiet-hours preferences
    sent = "sent"  # FCM accepted (success_count > 0 for this token)
    failed = "failed"  # FCM rejected (transient or permanent)
    no_devices = "no_devices"  # User has no DeviceToken rows
    # Reconciler-assigned: the row sat in `queued` past the staleness
    # threshold without an outcome being recorded. Most likely cause: the
    # worker crashed between FCM ACK and the audit-stamp UPDATE.
    unknown_outcome = "unknown_outcome"


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
    category = Column(String(30), nullable=False, server_default="general")
    is_read = Column(Boolean, default=False)
    sent_at = Column(DateTime(timezone=True), default=utcnow)
    read_at = Column(DateTime(timezone=True), nullable=True)

    # ── Delivery audit fields (added 2026-04 to close the compliance gap) ──────
    # Why each of these exists:
    #   • delivery_status — single-source-of-truth answer to "did this go out?"
    #   • delivered_at    — when FCM accepted the message; ≠ sent_at (which is
    #                       when the row was *queued* — they can differ by hours
    #                       under quiet-hours deferral).
    #   • deferred_until  — wall-clock time we plan to release this push.
    #   • fcm_message_ids — comma-separated FCM message IDs (one per device).
    #                       Useful for cross-referencing FCM dashboard reports.
    #   • last_error      — last failure reason for triage (FCM error code,
    #                       SMTP rejection, etc.). Truncated to 500 chars.
    delivery_status = Column(
        String(30), nullable=False, server_default="queued", index=True
    )
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    deferred_until = Column(DateTime(timezone=True), nullable=True, index=True)
    fcm_message_ids = Column(String(2000), nullable=True)
    last_error = Column(String(500), nullable=True)

    __table_args__ = (
        # [Performance/DB]: Composite index on (user_id, is_read) optimizes the
        # unread count queries shown in the UI sidebar.
        Index("ix_notification_logs_user_unread", "user_id", "is_read"),
        # [Performance/DB]: Composite index on (user_id, category) optimizes
        # tab-filtered queries (e.g. "show only Critical Alerts for this user").
        Index("ix_notification_logs_user_category", "user_id", "category"),
        # [Performance/DB]: Lets the morning-digest worker quickly find every
        # notification whose deferred_until has elapsed.
        Index(
            "ix_notification_logs_deferred_pending",
            "delivery_status",
            "deferred_until",
        ),
    )


__all__ = [
    "NotificationCategory",
    "NotificationDeliveryStatus",
    "NotificationLog",
]
