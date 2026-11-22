"""P0 ACCOUNT-ISOLATION: order_audit_log.account_id (계좌 스코프)

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-27 00:00:00.000000

nullable 컬럼 + 인덱스 추가만. 기존 행은 NULL 그대로(= LEGACY_ACCOUNT) — 실제 계좌 근거가
없으므로 백필/UPDATE 0건, 기존 값 변경 0건. 신규 주문부터 route_order 가 스코프를 기록한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("order_audit_log") as b:
        b.add_column(sa.Column("account_id", sa.String(length=48), nullable=True))
        b.create_index("ix_order_audit_log_account_id", ["account_id"])


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log") as b:
        b.drop_index("ix_order_audit_log_account_id")
        b.drop_column("account_id")
