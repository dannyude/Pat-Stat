"""add notification category column

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-15 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # [Performance/DB]: server_default='general' makes this a zero-downtime
    # migration — Postgres updates the catalog metadata (O(1)) rather than
    # rewriting every existing row.
    op.add_column(
        "notification_logs",
        sa.Column("category", sa.String(30), nullable=False, server_default="general"),
    )
    op.create_index(
        "ix_notification_logs_user_category",
        "notification_logs",
        ["user_id", "category"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_logs_user_category", table_name="notification_logs")
    op.drop_column("notification_logs", "category")
