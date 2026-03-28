"""add unique active admission constraint and family_patient_links patient_id index

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-26 00:00:00.000000

Two independent data-integrity / performance improvements:

1. Unique partial index on admissions(patient_id) WHERE discharged_at IS NULL
   — enforces at the DB level that a patient can have at most one active
     admission at a time. Without this, a race condition or bug can silently
     create two active rows; get_active_admission would then pick whichever
     has the latest admitted_at, hiding the anomaly.

2. B-tree index on family_patient_links(patient_id)
   — The Celery notify_family_of_update task JOINs FamilyPatientLink on
     patient_id for every clinical update. Without an index this is a full
     table scan proportional to the total number of family links.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enforce one active admission per patient at the DB level.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_one_active_admission_per_patient
        ON admissions (patient_id)
        WHERE discharged_at IS NULL
        """
    )

    # 2. Speed up the reverse lookup from patient → linked family members.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_family_patient_links_patient_id
        ON family_patient_links (patient_id)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_family_patient_links_patient_id")
    op.execute("DROP INDEX IF EXISTS uq_one_active_admission_per_patient")