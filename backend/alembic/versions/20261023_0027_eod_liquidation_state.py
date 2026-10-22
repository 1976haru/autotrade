"""KIS-PAPER-INTRADAY-FLAT-BY-CLOSE-V1: eod_liquidation_state (장마감 청산 상태/lease)

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-23 00:00:00.000000

거래일(KST)별 1행 — EOD 청산 coordinator 의 상태·DB lease·진행 요약·FLATTEN_INCOMPLETE
운영자 확인 여부. 주문 권한이 아니다(is_live_authorization=False). 주문은 기존
route_order 경로만 사용.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "eod_liquidation_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trading_date", sa.String(length=10), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_owner", sa.String(length=96), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_reconcile_at", sa.DateTime(), nullable=True),
        sa.Column("last_reconcile_ok", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("final_verdict_at", sa.DateTime(), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("incident_acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_by", sa.String(length=64), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(), nullable=True),
        sa.Column("ack_note", sa.String(length=255), nullable=True),
        sa.Column("is_live_authorization", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_eod_liquidation_state_trading_date", "eod_liquidation_state",
                    ["trading_date"], unique=True)
    op.create_index("ix_eod_liquidation_state_state", "eod_liquidation_state", ["state"])


def downgrade() -> None:
    op.drop_index("ix_eod_liquidation_state_state", table_name="eod_liquidation_state")
    op.drop_index("ix_eod_liquidation_state_trading_date", table_name="eod_liquidation_state")
    op.drop_table("eod_liquidation_state")
