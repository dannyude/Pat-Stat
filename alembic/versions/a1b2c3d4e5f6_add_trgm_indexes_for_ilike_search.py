"""add pg_trgm GIN indexes for ILIKE patient search

Revision ID: a1b2c3d4e5f6
Revises: 69638fe44fc3
Create Date: 2026-03-25 00:00:00.000000

Plain B-tree indexes do not help with ILIKE/LIKE '%term%' queries.
pg_trgm GIN indexes split strings into trigrams so PostgreSQL can use the
index for any substring pattern, reducing patient search from a full-table
scan to a fast index lookup.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "69638fe44fc3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable the extension (idempotent — safe to run multiple times).
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # patient_profiles — searched by full_name and pat_stat_id
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_profiles_name_trgm
        ON patient_profiles USING GIN (full_name gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_patient_profiles_pat_stat_id_trgm
        ON patient_profiles USING GIN (pat_stat_id gin_trgm_ops)
        """
    )

    # admissions — searched by ward and diagnosis
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_admissions_ward_trgm
        ON admissions USING GIN (ward gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_admissions_diagnosis_trgm
        ON admissions USING GIN (diagnosis gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_admissions_diagnosis_trgm")
    op.execute("DROP INDEX IF EXISTS ix_admissions_ward_trgm")
    op.execute("DROP INDEX IF EXISTS ix_patient_profiles_pat_stat_id_trgm")
    op.execute("DROP INDEX IF EXISTS ix_patient_profiles_name_trgm")
    # Leave pg_trgm extension installed — other indexes may depend on it.
