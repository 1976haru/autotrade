"""add emergency_stop_event.level (#37 — 3-level kill switch)

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # #37: 3단계 Kill Switch — OFF / LEVEL_1 / LEVEL_2 / LEVEL_3.
    # NULL = 0017 이전 row (legacy enabled=True/False 토글). app/risk/
    # emergency_stop.py의 normalize_legacy_level이 NULL+enabled=True를
    # LEVEL_1으로 표시 — 기존 의미 보존.
    with op.batch_alter_table("emergency_stop_event", schema=None) as batch_op:
        batch_op.add_column(sa.Column("level", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("emergency_stop_event", schema=None) as batch_op:
        batch_op.drop_column("level")
