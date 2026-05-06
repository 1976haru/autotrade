"""add order_audit_log.ai_decision_meta (152)

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 152: VIRTUAL_AI_EXECUTION에서 AI 제안의 decision context를 audit row에 영구화.
    # {"confidence": 0..100, "reasons": [...], "rejected_by_guard": bool, ...}
    # NULL = AI 외 경로 주문. 사후 분석 시 'AI 신호 강도와 PnL 상관관계' 추적.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("ai_decision_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_column("ai_decision_meta")
