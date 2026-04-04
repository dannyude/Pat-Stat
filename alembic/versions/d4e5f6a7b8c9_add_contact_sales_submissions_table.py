"""add contact_sales_submissions table

Revision ID: d4e5f6a7b8c9
Revises: c3a91b6e2d14, b2c3d4e5f6a7
Create Date: 2026-04-04 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str]] = ("c3a91b6e2d14", "b2c3d4e5f6a7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_sales_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column("work_email", sa.String(255), nullable=False),
        sa.Column("hospital_name", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
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
        ),
    )
    op.create_index(
        "ix_contact_sales_submissions_work_email",
        "contact_sales_submissions",
        ["work_email"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_sales_submissions_work_email")
    op.drop_table("contact_sales_submissions")
