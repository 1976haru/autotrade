"""add shadow_trade table (#43 — Live Shadow finalization)

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # #43: LIVE_SHADOW 모드의 추정 기록 테이블. OrderAuditLog는 RiskManager가
    # LIVE_SHADOW를 항상 REJECTED로 변환하기 때문에 reject 이력만 남고,
    # would-have-passed 정보(다른 가드 통과 여부 + 추정 체결가)가 사라진다 —
    # 본 테이블이 그 위에 추정 정보를 영구화한다.
    #
    # 절대 원칙: actual_broker_order_sent는 invariant False — broker.place_order는
    # LIVE_SHADOW에서 절대 호출되지 않는다. 추정 체결가는 실 체결과 다를 수 있다.
    op.create_table(
        "shadow_trade",
        sa.Column("id",                       sa.Integer(),  nullable=False),
        sa.Column("created_at",               sa.DateTime(), nullable=False),
        sa.Column("audit_id",                 sa.Integer(),  nullable=False),
        sa.Column("mode",                     sa.String(length=32), nullable=False),
        sa.Column("requested_by_ai",          sa.Boolean(),  nullable=False),
        sa.Column("symbol",                   sa.String(length=16), nullable=False),
        sa.Column("side",                     sa.String(length=8),  nullable=False),
        sa.Column("quantity",                 sa.Integer(),  nullable=False),
        sa.Column("order_type",               sa.String(length=16), nullable=False),
        sa.Column("limit_price",              sa.Integer(),  nullable=True),
        sa.Column("latest_price",             sa.Integer(),  nullable=False),
        sa.Column("would_have_decision",      sa.String(length=32), nullable=False),
        sa.Column("would_have_reasons",       sa.JSON(),     nullable=False),
        sa.Column("actual_broker_order_sent", sa.Boolean(),  nullable=False),
        sa.Column("estimated_fill_price",     sa.Integer(),  nullable=False),
        sa.Column("estimated_slippage_bps",   sa.Float(),    nullable=False),
        sa.Column("estimation_method",        sa.String(length=32), nullable=False),
        sa.Column("confidence_note",          sa.String(length=255), nullable=True),
        sa.Column("strategy",                 sa.String(length=64),  nullable=True),
        sa.Column("trade_reason",             sa.String(length=64),  nullable=True),
        sa.Column("source",                   sa.String(length=32),  nullable=True),
        sa.Column("client_order_id",          sa.String(length=64),  nullable=True),
        sa.Column("ai_decision_meta",         sa.JSON(),     nullable=True),
        sa.ForeignKeyConstraint(["audit_id"], ["order_audit_log.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_shadow_trade_created_at"),          "shadow_trade", ["created_at"])
    op.create_index(op.f("ix_shadow_trade_audit_id"),            "shadow_trade", ["audit_id"])
    op.create_index(op.f("ix_shadow_trade_mode"),                "shadow_trade", ["mode"])
    op.create_index(op.f("ix_shadow_trade_symbol"),              "shadow_trade", ["symbol"])
    op.create_index(op.f("ix_shadow_trade_would_have_decision"), "shadow_trade", ["would_have_decision"])
    op.create_index(op.f("ix_shadow_trade_strategy"),            "shadow_trade", ["strategy"])
    op.create_index(op.f("ix_shadow_trade_source"),              "shadow_trade", ["source"])
    op.create_index(op.f("ix_shadow_trade_client_order_id"),     "shadow_trade", ["client_order_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_shadow_trade_client_order_id"),     table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_source"),              table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_strategy"),            table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_would_have_decision"), table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_symbol"),              table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_mode"),                table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_audit_id"),            table_name="shadow_trade")
    op.drop_index(op.f("ix_shadow_trade_created_at"),          table_name="shadow_trade")
    op.drop_table("shadow_trade")
