"""add order_audit_log.trade_reason

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 134: 주문에 진입/청산 사유를 기록 — 'strategy_signal', 'stop_loss',
    # 'manual', 'ai_recommendation' 등 자유 문자열. 사고 분석 시 'A주문이 왜
    # 들어갔나' 질문에 audit 자체가 답할 수 있어야 한다 (CLAUDE.md '감사 로그
    # 우선'의 자연 확장).
    #
    # 자유 문자열 — enum으로 강제하지 않는다. 운영자가 새 사유를 추가할 때
    # schema 변경 없이 기록 가능. 정형이 필요하면 frontend가 select 옵션을
    # 강제하거나 별도 카테고리화 layer를 둔다.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("trade_reason", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_column("trade_reason")
