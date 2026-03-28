"""fix admissions ward type

Revision ID: 9d2c7e1a4b55
Revises: 4f8b7f2a1c90
Create Date: 2026-03-21 21:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9d2c7e1a4b55"
down_revision: Union[str, None] = "4f8b7f2a1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "admissions") and _has_column(bind, "admissions", "ward"):
        # Force ward to plain VARCHAR(120) to remove any legacy custom/domain type drift.
        op.execute(
            "ALTER TABLE admissions ALTER COLUMN ward TYPE VARCHAR(120) USING ward::text"
        )


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "admissions") and _has_column(bind, "admissions", "ward"):
        op.execute(
            "ALTER TABLE admissions ALTER COLUMN ward TYPE VARCHAR(10) USING ward::text"
        )
