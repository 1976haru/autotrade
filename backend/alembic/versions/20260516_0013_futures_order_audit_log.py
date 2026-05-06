"""add futures_order_audit_log table (169)

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 169: 선물 주문/청산 감사 로그. OrderAuditLog는 주식 전용 스키마라 분리.
    op.create_table(
        "futures_order_audit_log",
        sa.Column("id",                  sa.Integer(), nullable=False),
        sa.Column("created_at",          sa.DateTime(), nullable=False),
        sa.Column("mode",                sa.String(length=32), nullable=False),
        sa.Column("contract",            sa.String(length=32), nullable=False),
        sa.Column("side",                sa.String(length=8),  nullable=False),
        sa.Column("quantity",            sa.Integer(), nullable=False),
        sa.Column("order_type",          sa.String(length=16), nullable=False),
        sa.Column("limit_price",         sa.Integer(), nullable=True),
        sa.Column("leverage",            sa.Float(),  nullable=False, server_default="1.0"),
        sa.Column("decision",            sa.String(length=32), nullable=False),
        sa.Column("reasons",             sa.JSON(),  nullable=False),
        sa.Column("executed",            sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("broker_status",       sa.String(length=32), nullable=True),
        sa.Column("filled_quantity",     sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_fill_price",      sa.Integer(), nullable=True),
        sa.Column("margin_delta",        sa.Integer(), nullable=False, server_default="0"),
        sa.Column("liquidation_price",   sa.Integer(), nullable=True),
        sa.Column("forced_liquidation",  sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("message",             sa.String(length=255), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_futures_order_audit_log_created_at"),
                    "futures_order_audit_log", ["created_at"])
    op.create_index(op.f("ix_futures_order_audit_log_mode"),
                    "futures_order_audit_log", ["mode"])
    op.create_index(op.f("ix_futures_order_audit_log_contract"),
                    "futures_order_audit_log", ["contract"])
    op.create_index(op.f("ix_futures_order_audit_log_decision"),
                    "futures_order_audit_log", ["decision"])
    op.create_index(op.f("ix_futures_order_audit_log_forced_liquidation"),
                    "futures_order_audit_log", ["forced_liquidation"])


def downgrade() -> None:
    op.drop_index(op.f("ix_futures_order_audit_log_forced_liquidation"),
                  table_name="futures_order_audit_log")
    op.drop_index(op.f("ix_futures_order_audit_log_decision"),
                  table_name="futures_order_audit_log")
    op.drop_index(op.f("ix_futures_order_audit_log_contract"),
                  table_name="futures_order_audit_log")
    op.drop_index(op.f("ix_futures_order_audit_log_mode"),
                  table_name="futures_order_audit_log")
    op.drop_index(op.f("ix_futures_order_audit_log_created_at"),
                  table_name="futures_order_audit_log")
    op.drop_table("futures_order_audit_log")
