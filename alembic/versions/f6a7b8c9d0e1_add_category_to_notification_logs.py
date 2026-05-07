"""add category column to notification_logs

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-15 13:00:00.000000

Design note:
    The ``category`` column uses VARCHAR(30) instead of a PostgreSQL ENUM type.
    This is intentional — adding new notification categories (e.g., lab_result)
    only requires a code change, not a database migration with ALTER TYPE.

    server_default='general' ensures existing rows (created before this migration)
    get a sensible default without needing a backfill script.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str]] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add the category column with a server-side default so existing rows
    # are automatically backfilled to 'general' without a data migration.
    op.add_column(
        "notification_logs",
        sa.Column(
            "category",
            sa.String(30),
            nullable=False,
            server_default="general",
        ),
    )

    # Composite index for filtered tab queries:
    # "SELECT ... WHERE user_id = ? AND category = ?"
    op.create_index(
        "ix_notification_logs_user_category",
        "notification_logs",
        ["user_id", "category"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_logs_user_category")
    op.drop_column("notification_logs", "category")
