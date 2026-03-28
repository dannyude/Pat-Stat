"""add dashboard emergency and handover tables

Revision ID: 4f8b7f2a1c90
Revises: 69638fe44fc3
Create Date: 2026-03-21 21:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f8b7f2a1c90"
down_revision: Union[str, None] = "69638fe44fc3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    for column in inspector.get_columns(table_name):
        if column["name"] == column_name:
            return True
    return False


def upgrade() -> None:
    bind = op.get_bind()

    # Keep schema aligned with the current Admission ORM model.
    if _has_table(bind, "admissions") and not _has_column(
        bind, "admissions", "admitted_by_id"
    ):
        op.add_column(
            "admissions",
            sa.Column("admitted_by_id", sa.UUID(as_uuid=False), nullable=True),
        )
        op.create_foreign_key(
            "fk_admissions_admitted_by_id_users",
            "admissions",
            "users",
            ["admitted_by_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # PostgreSQL enum type used by emergency flags.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'emergencypriority') THEN
                CREATE TYPE emergencypriority AS ENUM ('High', 'Critical');
            END IF;
        END
        $$;
        """
    )

    if not _has_table(bind, "emergency_flags"):
        op.create_table(
            "emergency_flags",
            sa.Column("admission_id", sa.UUID(as_uuid=False), nullable=False),
            sa.Column("flagged_by_id", sa.UUID(as_uuid=False), nullable=True),
            sa.Column(
                "priority",
                sa.Enum(
                    "High", "Critical", name="emergencypriority", create_type=False
                ),
                nullable=False,
            ),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column(
                "is_resolved",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("resolved_by_id", sa.UUID(as_uuid=False), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
            sa.ForeignKeyConstraint(
                ["admission_id"],
                ["admissions.id"],
                name="fk_emergency_flag_admission_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["flagged_by_id"],
                ["users.id"],
                name="fk_emergency_flag_flagged_by_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["resolved_by_id"],
                ["users.id"],
                name="fk_emergency_flag_resolved_by_id",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_emergency_flags")),
        )
        op.create_index(
            "ix_emergency_flags_unresolved",
            "emergency_flags",
            ["is_resolved"],
            unique=False,
        )
        op.create_index(
            "ix_emergency_flags_admission",
            "emergency_flags",
            ["admission_id", "is_resolved"],
            unique=False,
        )

    if not _has_table(bind, "shift_handovers"):
        op.create_table(
            "shift_handovers",
            sa.Column("admission_id", sa.UUID(as_uuid=False), nullable=False),
            sa.Column("from_staff_id", sa.UUID(as_uuid=False), nullable=True),
            sa.Column("to_staff_id", sa.UUID(as_uuid=False), nullable=True),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("pending_actions", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
            sa.ForeignKeyConstraint(
                ["admission_id"],
                ["admissions.id"],
                name="fk_shift_handover_admission_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["from_staff_id"],
                ["users.id"],
                name="fk_shift_handover_from_staff_id",
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(
                ["to_staff_id"],
                ["users.id"],
                name="fk_shift_handover_to_staff_id",
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_shift_handovers")),
        )
        op.create_index(
            "ix_shift_handovers_admission",
            "shift_handovers",
            ["admission_id"],
            unique=False,
        )
        op.create_index(
            "ix_shift_handovers_created",
            "shift_handovers",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "shift_handovers"):
        op.drop_index("ix_shift_handovers_created", table_name="shift_handovers")
        op.drop_index("ix_shift_handovers_admission", table_name="shift_handovers")
        op.drop_table("shift_handovers")

    if _has_table(bind, "emergency_flags"):
        op.drop_index("ix_emergency_flags_admission", table_name="emergency_flags")
        op.drop_index("ix_emergency_flags_unresolved", table_name="emergency_flags")
        op.drop_table("emergency_flags")

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'emergencypriority') THEN
                DROP TYPE emergencypriority;
            END IF;
        END
        $$;
        """
    )

    if _has_table(bind, "admissions") and _has_column(
        bind, "admissions", "admitted_by_id"
    ):
        op.drop_constraint(
            "fk_admissions_admitted_by_id_users", "admissions", type_="foreignkey"
        )
        op.drop_column("admissions", "admitted_by_id")
