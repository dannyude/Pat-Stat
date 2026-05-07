"""add platform_settings table

Revision ID: a1f2b3c4d5e6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-24 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "a1f2b3c4d5e6"
down_revision: Union[str, Sequence[str]] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_settings",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "platform_name",
            sa.String(100),
            nullable=False,
            server_default="Pat-Stat",
        ),
        sa.Column("support_email", sa.String(255), nullable=True),
        sa.Column("default_region", sa.String(100), nullable=True),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_platform_settings_updated_by_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("platform_settings")
