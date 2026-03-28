"""Audit and notification log models for the patients domain."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID

from src.core.database import Base
from src.core.mixins import UUIDPrimaryKey, utcnow


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
    is_read = Column(Boolean, default=False)
    sent_at = Column(DateTime(timezone=True), default=utcnow)
    read_at = Column(DateTime(timezone=True), nullable=True)

    # [Performance/DB]: Composite index on (user_id, is_read) optimizes the
    # unread count queries shown in the UI sidebar.
    __table_args__ = (Index("ix_notification_logs_user_unread", "user_id", "is_read"),)


__all__ = ["NotificationLog"]
