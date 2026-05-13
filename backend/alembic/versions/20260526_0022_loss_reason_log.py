"""add loss_reason_log table (Loss Tagging #79 — append-only, review only)

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-26 00:00:00.000000

체크리스트 #79: 손실 거래 *추정* 원인 태그 영구화.

절대 원칙:
- 본 테이블 row 는 *추정값*이며 *확정 원인이 아니다* (`is_estimated`=True
  영구). 운영자 검토 시 review_note 컬럼만 갱신 — 원본 row 삭제/수정 금지.
- DELETE API 추가 0건 — append + 운영자 review only.
- broker / OrderExecutor / route_order 어느 코드 경로에서도 본 테이블에
  주문 의사결정을 의존하지 않는다.
- 본 테이블의 태그는 *주문 차단 / 실행 트리거*로 사용 금지 — advisory only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "loss_reason_log",
        sa.Column("id",          sa.Integer(),  primary_key=True),
        sa.Column("created_at",  sa.DateTime(), nullable=False),

        # 출처 식별 — order_audit / virtual_order / futures_audit / manual / agent.
        sa.Column("source_table", sa.String(length=32), nullable=False),
        sa.Column("source_id",    sa.Integer(),         nullable=True),

        # 거래 기본 정보 (검색 / 집계용).
        sa.Column("symbol",       sa.String(length=16), nullable=False),
        sa.Column("strategy",     sa.String(length=64), nullable=True),
        sa.Column("mode",         sa.String(length=32), nullable=True),

        # 손익 (절댓값이 아니라 부호 포함 — 손실은 음수).
        sa.Column("trade_pnl",    sa.Integer(),         nullable=False),
        sa.Column("is_loss",      sa.Boolean(),         nullable=False),

        # 추정 결과.
        sa.Column("primary_tag",  sa.String(length=48), nullable=True),
        sa.Column("primary_category", sa.String(length=16), nullable=True),
        sa.Column("tags",         sa.JSON(),            nullable=False),
        sa.Column("rationale",    sa.JSON(),            nullable=False),
        sa.Column("confidence",   sa.Integer(),         nullable=False),

        # invariant — 본 row 는 *추정*. 항상 True. UI / API surface 시 명시.
        sa.Column("is_estimated", sa.Boolean(),         nullable=False),

        # 운영자 review (원본 row 갱신 X — 본 컬럼만 update).
        sa.Column("review_status", sa.String(length=16), nullable=True),
        sa.Column("reviewed_by",   sa.String(length=64), nullable=True),
        sa.Column("review_note",   sa.String(length=500), nullable=True),
        sa.Column("reviewed_at",   sa.DateTime(),        nullable=True),
    )

    op.create_index("ix_loss_reason_log_created_at",   "loss_reason_log", ["created_at"])
    op.create_index("ix_loss_reason_log_source_table", "loss_reason_log", ["source_table"])
    op.create_index("ix_loss_reason_log_source_id",    "loss_reason_log", ["source_id"])
    op.create_index("ix_loss_reason_log_symbol",       "loss_reason_log", ["symbol"])
    op.create_index("ix_loss_reason_log_strategy",     "loss_reason_log", ["strategy"])
    op.create_index("ix_loss_reason_log_mode",         "loss_reason_log", ["mode"])
    op.create_index("ix_loss_reason_log_primary_tag",  "loss_reason_log", ["primary_tag"])
    op.create_index("ix_loss_reason_log_primary_category", "loss_reason_log", ["primary_category"])
    op.create_index("ix_loss_reason_log_is_loss",      "loss_reason_log", ["is_loss"])


def downgrade() -> None:
    # downgrade는 정의되어 있지만 본 PR 정책상 운영 환경에서 호출하지 않는다.
    # 손실 태그 row 삭제는 사후 분석 손실이므로 *upgrade* 방향만 권장.
    op.drop_index("ix_loss_reason_log_is_loss",         table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_primary_category", table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_primary_tag",     table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_mode",            table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_strategy",        table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_symbol",          table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_source_id",       table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_source_table",    table_name="loss_reason_log")
    op.drop_index("ix_loss_reason_log_created_at",      table_name="loss_reason_log")
    op.drop_table("loss_reason_log")
