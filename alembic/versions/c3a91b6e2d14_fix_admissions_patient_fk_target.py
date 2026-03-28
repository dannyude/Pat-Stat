"""fix admissions patient FK target

Revision ID: c3a91b6e2d14
Revises: 9d2c7e1a4b55
Create Date: 2026-03-21 21:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3a91b6e2d14"
down_revision: Union[str, None] = "9d2c7e1a4b55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "admissions"):
        return
    if not _has_table(bind, "patient_profiles"):
        return
    if not _has_column(bind, "admissions", "patient_id"):
        return

    op.execute(
        "ALTER TABLE admissions DROP CONSTRAINT IF EXISTS fk_admission_patient_id"
    )
    op.execute(
        """
        ALTER TABLE admissions
        ADD CONSTRAINT fk_admission_patient_id
        FOREIGN KEY (patient_id)
        REFERENCES patient_profiles (id)
        ON DELETE CASCADE
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "admissions"):
        return
    if not _has_column(bind, "admissions", "patient_id"):
        return

    op.execute(
        "ALTER TABLE admissions DROP CONSTRAINT IF EXISTS fk_admission_patient_id"
    )

    if _has_table(bind, "patients"):
        op.execute(
            """
            ALTER TABLE admissions
            ADD CONSTRAINT fk_admission_patient_id
            FOREIGN KEY (patient_id)
            REFERENCES patients (id)
            ON DELETE CASCADE
            """
        )
