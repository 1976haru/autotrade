"""add review column to agent_decision_episode (P-27 — PostTradeReviewAgent)

Revision ID: 0024
Revises: 0023
Create Date: 2026-05-28 00:00:00.000000

P-27: 각 decision episode 종료 후 PostTradeReviewAgent 가 판단 품질을 복기하고
개선 후보를 `agent_decision_episode.review`(JSON) 에 기록한다.

절대 원칙:
- 본 컬럼은 *기록 전용* — broker / OrderExecutor / route_order 가 본 값을 읽어
  주문을 만들지 않는다 (advisory only).
- 복기 결과는 *다음 주문 신호가 아니다* — is_order_signal=False /
  is_live_authorization=False. secret / 계좌번호 carry 0건.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_decision_episode",
        sa.Column("review", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_decision_episode", "review")
