"""drop interoperability tables for single-hospital mode

Revision ID: 69638fe44fc3
Revises: 093268312883
Create Date: 2026-03-21 19:07:08.028485
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "69638fe44fc3"
down_revision: Union[str, None] = "093268312883"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Single-hospital mode: remove cross-hospital interoperability tables.
    op.execute("DROP TABLE IF EXISTS otp_verifications CASCADE")
    op.execute("DROP TABLE IF EXISTS hospital_patient_access CASCADE")
    op.execute("DROP TABLE IF EXISTS consent_requests CASCADE")


def downgrade() -> None:
    op.create_table(
        "consent_requests",
        sa.Column("patient_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("requesting_hospital_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("owning_hospital_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("requested_by_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_reason", sa.Text(), nullable=True),
        sa.Column("requested_scope", sa.String(length=30), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owning_hospital_id"],
            ["hospitals.id"],
            name="fk_consent_requests_owning_hospital_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name="fk_consent_requests_patient_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_consent_requests_requested_by_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requesting_hospital_id"],
            ["hospitals.id"],
            name="fk_consent_requests_requesting_hospital_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_id"],
            ["users.id"],
            name="fk_consent_requests_resolved_by_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_requests")),
    )
    op.create_index(
        "ix_consent_requests_patient_status",
        "consent_requests",
        ["patient_id", "status"],
        unique=False,
    )

    op.create_table(
        "hospital_patient_access",
        sa.Column("patient_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("hospital_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("consent_type", sa.String(length=30), nullable=False),
        sa.Column("scope", sa.String(length=30), nullable=False),
        sa.Column("granted_by_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("consent_request_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["consent_request_id"],
            ["consent_requests.id"],
            name="fk_hospital_patient_access_consent_request_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["users.id"],
            name="fk_hospital_patient_access_granted_by_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospitals.id"],
            name="fk_hospital_patient_access_hospital_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name="fk_hospital_patient_access_patient_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hospital_patient_access")),
        sa.UniqueConstraint(
            "patient_id", "hospital_id", name="uq_hospital_patient_access"
        ),
    )
    op.create_index(
        "ix_hpa_hospital_active",
        "hospital_patient_access",
        ["hospital_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "otp_verifications",
        sa.Column("consent_request_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("hashed_pin", sa.String(length=255), nullable=False),
        sa.Column("phone_sent_to", sa.String(length=30), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["consent_request_id"],
            ["consent_requests.id"],
            name="fk_otp_verifications_consent_request_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patient_profiles.id"],
            name="fk_otp_verifications_patient_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_verifications")),
    )
    op.create_index(
        op.f("ix_otp_verifications_consent_request_id"),
        "otp_verifications",
        ["consent_request_id"],
        unique=False,
    )
