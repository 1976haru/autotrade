"""trailing stop stage 1-2: position_high_watermark + trailing_shadow_outcome (measure/shadow)

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-17 00:00:00.000000

트레일링 스탑 1·2단계(측정+섀도). 설계 docs/design/trailing_stop.md.
- position_high_watermark: 보유 종목 장중 최고가 추적(매 틱 갱신, 청산 시 삭제).
- trailing_shadow_outcome: 청산된 포지션의 최고점 도달 기록(T3 비교용).

절대 원칙: 두 테이블 모두 *측정/기록 전용* — 봇 매도 결정·route_order·OrderExecutor 가
본 값을 읽어 주문을 만들지 않는다(동작 변경 0). is_order_signal=False.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "position_high_watermark",
        sa.Column("symbol", sa.String(length=16), primary_key=True),
        sa.Column("entry_price", sa.Integer(), nullable=False),
        sa.Column("high_watermark", sa.Integer(), nullable=False),
        sa.Column("activated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_position_high_watermark_updated_at",
                    "position_high_watermark", ["updated_at"])
    op.create_table(
        "trailing_shadow_outcome",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("entry_price", sa.Integer(), nullable=False),
        sa.Column("peak_high_watermark", sa.Integer(), nullable=False),
        sa.Column("peak_return_pct", sa.Float(), nullable=False),
        sa.Column("activated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("closed_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_trailing_shadow_outcome_symbol", "trailing_shadow_outcome", ["symbol"])
    op.create_index("ix_trailing_shadow_outcome_closed_at", "trailing_shadow_outcome", ["closed_at"])


def downgrade() -> None:
    op.drop_index("ix_trailing_shadow_outcome_closed_at", table_name="trailing_shadow_outcome")
    op.drop_index("ix_trailing_shadow_outcome_symbol", table_name="trailing_shadow_outcome")
    op.drop_table("trailing_shadow_outcome")
    op.drop_index("ix_position_high_watermark_updated_at", table_name="position_high_watermark")
    op.drop_table("position_high_watermark")
