"""persist explicit KIS PAPER session approvals

Revision ID: 0033
Revises: 0032
"""
from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "paper_session_approval",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trading_date", sa.String(10), nullable=False),
        sa.Column("account_fingerprint", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("record", sa.JSON(), nullable=False),
    )
    op.create_index("ix_paper_session_approval_trading_date", "paper_session_approval", ["trading_date"])
    op.create_index("ix_paper_session_approval_account_fingerprint", "paper_session_approval", ["account_fingerprint"])
    op.create_index("ix_paper_session_approval_status", "paper_session_approval", ["status"])
    op.create_index("ix_paper_session_approval_expires_at", "paper_session_approval", ["expires_at"])
    op.create_index(
        "uq_paper_session_approval_date_account", "paper_session_approval",
        ["trading_date", "account_fingerprint"], unique=True,
    )


def downgrade() -> None:
    op.drop_table("paper_session_approval")
