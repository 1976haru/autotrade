"""Persist account scoped intraday recovery proof separately from EOD state.

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "morning_recovery_verification",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("incident_date", sa.String(length=10), nullable=False),
        sa.Column("recovery_date", sa.String(length=10), nullable=False),
        sa.Column("account_key", sa.String(length=48), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("lease_owner", sa.String(length=96), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "incident_date", "recovery_date", "account_key",
            name="uq_morning_recovery_incident_date_account",
        ),
    )
    op.create_index(
        "ix_morning_recovery_verification_incident_date",
        "morning_recovery_verification", ["incident_date"],
    )
    op.create_index(
        "ix_morning_recovery_verification_recovery_date",
        "morning_recovery_verification", ["recovery_date"],
    )
    op.create_index(
        "ix_morning_recovery_verification_status",
        "morning_recovery_verification", ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_morning_recovery_verification_status", table_name="morning_recovery_verification")
    op.drop_index("ix_morning_recovery_verification_recovery_date", table_name="morning_recovery_verification")
    op.drop_index("ix_morning_recovery_verification_incident_date", table_name="morning_recovery_verification")
    op.drop_table("morning_recovery_verification")
