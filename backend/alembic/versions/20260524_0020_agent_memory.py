"""add agent_memory table (Agent Memory — read-only learning store)

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-24 00:00:00.000000

본 테이블은 *주문 신호가 아니다*. Agent / 운영자가 과거 손실 원인 / 전략 변경
이력 / 위험 사례 / 운영자 메모를 검색 가능한 형태로 보관한다.

절대 원칙:
- API key / Secret / 계좌번호 / 인증 토큰 / 개인정보 저장 금지 (caller가
  app.agents.agent_memory.sanitize_text로 sanitize 후 INSERT).
- 본 테이블은 BUY/SELL/HOLD 결정 신호를 만들지 않는다.
- RiskManager / PermissionGate / OrderExecutor 우회 금지.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("id",          sa.Integer(),  nullable=False),
        sa.Column("created_at",  sa.DateTime(), nullable=False),
        sa.Column("updated_at",  sa.DateTime(), nullable=False),

        sa.Column("memory_type", sa.String(length=32), nullable=False),
        sa.Column("source_kind", sa.String(length=32), nullable=True),
        sa.Column("source_id",   sa.Integer(),         nullable=True),

        sa.Column("strategy",    sa.String(length=64), nullable=True),
        sa.Column("symbol",      sa.String(length=16), nullable=True),
        sa.Column("mode",        sa.String(length=32), nullable=True),

        sa.Column("severity",    sa.String(length=16), nullable=False),

        sa.Column("title",       sa.String(length=200), nullable=False),
        sa.Column("summary",     sa.Text(),  nullable=False),
        sa.Column("lessons",     sa.Text(),  nullable=True),
        sa.Column("next_action", sa.Text(),  nullable=True),

        sa.Column("tags",        sa.JSON(), nullable=False),
        sa.Column("meta",        sa.JSON(), nullable=False),

        sa.Column("author",      sa.String(length=64), nullable=True),
        sa.Column("archived",    sa.Boolean(),  nullable=False),

        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_memory_created_at", "agent_memory", ["created_at"])
    op.create_index("ix_agent_memory_memory_type", "agent_memory", ["memory_type"])
    op.create_index("ix_agent_memory_source_kind", "agent_memory", ["source_kind"])
    op.create_index("ix_agent_memory_source_id",   "agent_memory", ["source_id"])
    op.create_index("ix_agent_memory_strategy",    "agent_memory", ["strategy"])
    op.create_index("ix_agent_memory_symbol",      "agent_memory", ["symbol"])
    op.create_index("ix_agent_memory_mode",        "agent_memory", ["mode"])
    op.create_index("ix_agent_memory_severity",    "agent_memory", ["severity"])
    op.create_index("ix_agent_memory_archived",    "agent_memory", ["archived"])


def downgrade() -> None:
    op.drop_index("ix_agent_memory_archived",    table_name="agent_memory")
    op.drop_index("ix_agent_memory_severity",    table_name="agent_memory")
    op.drop_index("ix_agent_memory_mode",        table_name="agent_memory")
    op.drop_index("ix_agent_memory_symbol",      table_name="agent_memory")
    op.drop_index("ix_agent_memory_strategy",    table_name="agent_memory")
    op.drop_index("ix_agent_memory_source_id",   table_name="agent_memory")
    op.drop_index("ix_agent_memory_source_kind", table_name="agent_memory")
    op.drop_index("ix_agent_memory_memory_type", table_name="agent_memory")
    op.drop_index("ix_agent_memory_created_at",  table_name="agent_memory")
    op.drop_table("agent_memory")
