"""add staff_invites table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-15 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str]] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff_invites",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "hospital_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "hospitals.id", ondelete="CASCADE", name="fk_staff_invite_hospital_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "inviter_user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_staff_invite_inviter_user_id"
            ),
            nullable=True,
        ),
        sa.Column(
            "accepted_user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_staff_invite_accepted_user_id"
            ),
            nullable=True,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("staff_name", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "super_admin", "admin", "doctor", "nurse", "family", name="userrole"
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("access_code", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Individual column indexes for common lookup patterns
    op.create_index("ix_staff_invites_hospital_id", "staff_invites", ["hospital_id"])
    op.create_index(
        "ix_staff_invites_inviter_user_id", "staff_invites", ["inviter_user_id"]
    )
    op.create_index(
        "ix_staff_invites_accepted_user_id", "staff_invites", ["accepted_user_id"]
    )
    op.create_index("ix_staff_invites_email", "staff_invites", ["email"])
    op.create_index(
        "ix_staff_invites_token_hash", "staff_invites", ["token_hash"], unique=True
    )
    op.create_index("ix_staff_invites_access_code", "staff_invites", ["access_code"])
    op.create_index("ix_staff_invites_status", "staff_invites", ["status"])

    # Composite index for the admin dashboard query: "pending invites for my hospital"
    op.create_index(
        "ix_staff_invites_hospital_status",
        "staff_invites",
        ["hospital_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_staff_invites_hospital_status")
    op.drop_index("ix_staff_invites_status")
    op.drop_index("ix_staff_invites_access_code")
    op.drop_index("ix_staff_invites_token_hash")
    op.drop_index("ix_staff_invites_email")
    op.drop_index("ix_staff_invites_accepted_user_id")
    op.drop_index("ix_staff_invites_inviter_user_id")
    op.drop_index("ix_staff_invites_hospital_id")
    op.drop_table("staff_invites")
