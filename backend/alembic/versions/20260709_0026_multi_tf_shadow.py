"""MULTI-TF-SHADOW-V1: multi_tf_shadow_tick + multi_tf_shadow_signal (read-only 관측)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-09 00:00:00.000000

5m entry + 60m confirm(Agent Council) 콤보를 shadow로 관측(results/multi_timeframe/
design.md 후속, adverse_selection_measurement.md 의 "실측 슬리피지"를 이 콤보
자체의 5분 빈도로 재측정). 두 테이블 모두 *기록 전용* — 봇 매매 결정·route_order·
OrderExecutor 가 본 값을 읽어 주문을 만들지 않는다(동작 변경 0). is_order_signal=False.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "multi_tf_shadow_tick",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("symbols_scanned", sa.Integer(), nullable=False),
        sa.Column("combo_signals_found", sa.Integer(), nullable=False),
        sa.Column("fetch_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rate_limited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tick_duration_seconds", sa.Float(), nullable=False),
        sa.Column("is_order_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_multi_tf_shadow_tick_created_at", "multi_tf_shadow_tick", ["created_at"])

    op.create_table(
        "multi_tf_shadow_signal",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("entry_tf", sa.String(length=8), nullable=False),
        sa.Column("confirm_tf", sa.String(length=8), nullable=False),
        sa.Column("entry_signal", sa.String(length=16), nullable=False),
        sa.Column("confirm_signal", sa.String(length=16), nullable=False),
        sa.Column("p1_price", sa.Float(), nullable=False),
        sa.Column("p1_timestamp", sa.DateTime(), nullable=False),
        sa.Column("p2_price", sa.Float(), nullable=True),
        sa.Column("p2_timestamp", sa.DateTime(), nullable=True),
        sa.Column("elapsed_seconds", sa.Float(), nullable=True),
        sa.Column("slippage_bps", sa.Float(), nullable=True),
        sa.Column("prev_close", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_order_signal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_live_authorization", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_multi_tf_shadow_signal_created_at", "multi_tf_shadow_signal", ["created_at"])
    op.create_index("ix_multi_tf_shadow_signal_symbol", "multi_tf_shadow_signal", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_multi_tf_shadow_signal_symbol", table_name="multi_tf_shadow_signal")
    op.drop_index("ix_multi_tf_shadow_signal_created_at", table_name="multi_tf_shadow_signal")
    op.drop_table("multi_tf_shadow_signal")
    op.drop_index("ix_multi_tf_shadow_tick_created_at", table_name="multi_tf_shadow_tick")
    op.drop_table("multi_tf_shadow_tick")
