"""add ai_analysis_log.mode

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 123: AI 호출이 발생한 시점의 운용모드를 기록 — 092/108 mode badge가 AI
    # 호출 timeline/audit row에서도 작동하고, 미래에 mode별 cost 분포 분석을
    # 가능하게 한다. 기존 row는 mode를 알 수 없으므로 nullable; backfill하지
    # 않는다 — historical NULL은 "기록 전" 의미로 그대로 surface.
    with op.batch_alter_table("ai_analysis_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("mode", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_analysis_log", schema=None) as batch_op:
        batch_op.drop_column("mode")
