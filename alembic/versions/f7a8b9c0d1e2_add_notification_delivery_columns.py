"""add delivery tracking columns to notification_logs

Revision ID: f7a8b9c0d1e2
Revises: a1f2b3c4d5e6
Create Date: 2026-04-26 09:00:00.000000

Why this migration exists
-------------------------
Adds the columns required to satisfy the "every push attempt is auditable"
healthcare compliance requirement, plus a reconciler-friendly state machine
for the notification delivery lifecycle:

  • delivery_status   — coarse lifecycle state. Values are not enforced at
                        the DB level; see ``NotificationDeliveryStatus`` in
                        ``src/domains/patients/models/audit.py`` for the
                        canonical list (queued, sent, failed, no_devices,
                        skipped_routine, unknown_outcome, deferred_quiet_hours).
  • delivered_at      — when FCM accepted the push (≠ sent_at, which is the
                        moment the row was queued).
  • deferred_until    — reserved for a future per-user "Do Not Disturb"
                        feature. v1 never writes to this column; the
                        column is shipped now so we don't need a second
                        migration when the feature lands.
  • fcm_message_ids   — pipe-delimited list of FCM message IDs for cross-
                        referencing in the Firebase console.
  • last_error        — last failure reason for triage. Populated by the
                        reconciler sweep when a row is stuck in 'queued'
                        past the staleness threshold.

The composite index ``(delivery_status, deferred_until)`` is a forward-
compat index for the same future feature — it's cheap to ship now and
avoids an online-index rebuild later when (if) we wire up DnD.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, Sequence[str]] = "a1f2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "notification_logs",
        sa.Column(
            "delivery_status",
            sa.String(length=30),
            nullable=False,
            server_default="queued",
        ),
    )
    op.add_column(
        "notification_logs",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("fcm_message_ids", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "notification_logs",
        sa.Column("last_error", sa.String(length=500), nullable=True),
    )

    op.create_index(
        "ix_notification_logs_delivery_status",
        "notification_logs",
        ["delivery_status"],
    )
    op.create_index(
        "ix_notification_logs_deferred_until",
        "notification_logs",
        ["deferred_until"],
    )
    op.create_index(
        "ix_notification_logs_deferred_pending",
        "notification_logs",
        ["delivery_status", "deferred_until"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_logs_deferred_pending")
    op.drop_index("ix_notification_logs_deferred_until")
    op.drop_index("ix_notification_logs_delivery_status")
    op.drop_column("notification_logs", "last_error")
    op.drop_column("notification_logs", "fcm_message_ids")
    op.drop_column("notification_logs", "deferred_until")
    op.drop_column("notification_logs", "delivered_at")
    op.drop_column("notification_logs", "delivery_status")
