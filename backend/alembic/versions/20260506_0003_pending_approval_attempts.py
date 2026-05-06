"""add pending_approval.attempts

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 076: persist re-eval-blocked approve attempts on the row itself.
    # server_default='[]' so existing PENDING rows get a sensible value
    # without needing a backfill pass.
    with op.batch_alter_table("pending_approval", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("attempts", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("pending_approval", schema=None) as batch_op:
        batch_op.drop_column("attempts")
