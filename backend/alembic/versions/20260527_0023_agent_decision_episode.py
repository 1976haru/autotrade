"""add agent_decision_episode table (P-21 — Agent Decision Episode logging)

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-27 00:00:00.000000

P-21: 하나의 BUY/SELL/HOLD 판단을 episode 단위로 연결 (판단 → 주문 → 체결 →
성과). 에이전트 성능 개선용 데이터셋.

절대 원칙:
- 본 테이블 row 는 *기록 전용* — broker / OrderExecutor / route_order 가 본
  테이블을 읽어 주문을 만들지 않는다 (advisory dataset only).
- `is_live_authorization`=False 영구. secret / 계좌번호 carry 0건.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_decision_episode",
        sa.Column("id",          sa.Integer(),  primary_key=True),
        sa.Column("created_at",  sa.DateTime(), nullable=False),
        sa.Column("updated_at",  sa.DateTime(), nullable=False),

        sa.Column("episode_id",   sa.String(length=64), nullable=False),

        sa.Column("symbol",       sa.String(length=16), nullable=True),
        sa.Column("mode",         sa.String(length=32), nullable=False),
        sa.Column("final_action", sa.String(length=16), nullable=False),
        sa.Column("confidence",   sa.Integer(),         nullable=True),
        sa.Column("quality_score", sa.Integer(),        nullable=True),
        sa.Column("reason_code",  sa.String(length=64), nullable=True),

        # 단계별 JSON 스냅샷.
        sa.Column("market_snapshot",   sa.JSON(), nullable=True),
        sa.Column("votes",             sa.JSON(), nullable=True),
        sa.Column("council",           sa.JSON(), nullable=True),
        sa.Column("risk_result",       sa.JSON(), nullable=True),
        sa.Column("permission_result", sa.JSON(), nullable=True),
        sa.Column("kis_order_result",  sa.JSON(), nullable=True),
        sa.Column("fill_result",       sa.JSON(), nullable=True),
        sa.Column("portfolio_delta",   sa.JSON(), nullable=True),
        sa.Column("outcome",           sa.JSON(), nullable=True),

        sa.Column("broker_order_no", sa.String(length=64), nullable=True),
        sa.Column("audit_id",        sa.Integer(),         nullable=True),
        sa.Column("decision_log_id", sa.Integer(),         nullable=True),

        # invariant — 영구 False.
        sa.Column("is_live_authorization", sa.Boolean(), nullable=False),
    )

    op.create_index("ix_agent_decision_episode_created_at",   "agent_decision_episode", ["created_at"])
    op.create_index("ix_agent_decision_episode_episode_id",   "agent_decision_episode", ["episode_id"], unique=True)
    op.create_index("ix_agent_decision_episode_symbol",       "agent_decision_episode", ["symbol"])
    op.create_index("ix_agent_decision_episode_mode",         "agent_decision_episode", ["mode"])
    op.create_index("ix_agent_decision_episode_final_action", "agent_decision_episode", ["final_action"])
    op.create_index("ix_agent_decision_episode_reason_code",  "agent_decision_episode", ["reason_code"])
    op.create_index("ix_agent_decision_episode_broker_order_no", "agent_decision_episode", ["broker_order_no"])
    op.create_index("ix_agent_decision_episode_audit_id",     "agent_decision_episode", ["audit_id"])
    op.create_index("ix_agent_decision_episode_decision_log_id", "agent_decision_episode", ["decision_log_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_decision_episode_decision_log_id", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_audit_id", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_broker_order_no", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_reason_code", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_final_action", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_mode", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_symbol", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_episode_id", table_name="agent_decision_episode")
    op.drop_index("ix_agent_decision_episode_created_at", table_name="agent_decision_episode")
    op.drop_table("agent_decision_episode")
