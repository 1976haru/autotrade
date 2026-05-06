"""add virtual_order table (148)

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 148: 가상 주문 라이프사이클 ledger. OrderAuditLog와 분리 — audit는 *결정*,
    # 본 테이블은 *주문 자체의 상태 전이*. 149 fill engine / 150 position engine
    # 의 기반 데이터.
    op.create_table(
        "virtual_order",
        sa.Column("id",                sa.Integer(), nullable=False),
        sa.Column("created_at",        sa.DateTime(), nullable=False),
        sa.Column("updated_at",        sa.DateTime(), nullable=False),
        sa.Column("audit_id",          sa.Integer(), nullable=True),
        sa.Column("symbol",            sa.String(length=16), nullable=False),
        sa.Column("side",              sa.String(length=8), nullable=False),
        sa.Column("quantity",          sa.Integer(), nullable=False),
        sa.Column("order_type",        sa.String(length=16), nullable=False),
        sa.Column("limit_price",       sa.Integer(), nullable=True),
        sa.Column("requested_price",   sa.Integer(), nullable=True),
        sa.Column("status",            sa.String(length=24), nullable=False),
        sa.Column("structured_reason", sa.String(length=64), nullable=True),
        sa.Column("strategy",          sa.String(length=64), nullable=True),
        sa.Column("mode",              sa.String(length=32), nullable=False),
        sa.Column("filled_quantity",   sa.Integer(), nullable=False),
        sa.Column("avg_fill_price",    sa.Integer(), nullable=True),
        sa.Column("filled_at",         sa.DateTime(), nullable=True),
        sa.Column("note",              sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["audit_id"], ["order_audit_log.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_virtual_order_audit_id"),   "virtual_order", ["audit_id"])
    op.create_index(op.f("ix_virtual_order_created_at"), "virtual_order", ["created_at"])
    op.create_index(op.f("ix_virtual_order_symbol"),     "virtual_order", ["symbol"])
    op.create_index(op.f("ix_virtual_order_status"),     "virtual_order", ["status"])
    op.create_index(op.f("ix_virtual_order_strategy"),   "virtual_order", ["strategy"])
    op.create_index(op.f("ix_virtual_order_mode"),       "virtual_order", ["mode"])


def downgrade() -> None:
    op.drop_index(op.f("ix_virtual_order_mode"),       table_name="virtual_order")
    op.drop_index(op.f("ix_virtual_order_strategy"),   table_name="virtual_order")
    op.drop_index(op.f("ix_virtual_order_status"),     table_name="virtual_order")
    op.drop_index(op.f("ix_virtual_order_symbol"),     table_name="virtual_order")
    op.drop_index(op.f("ix_virtual_order_created_at"), table_name="virtual_order")
    op.drop_index(op.f("ix_virtual_order_audit_id"),   table_name="virtual_order")
    op.drop_table("virtual_order")
