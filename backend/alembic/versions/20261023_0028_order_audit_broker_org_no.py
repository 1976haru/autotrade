"""V3: order_audit_log.broker_org_no (KRX_FWDG_ORD_ORGNO, 정정/취소용)

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-23 00:00:00.000000

KIS order-cash 응답 output.KRX_FWDG_ORD_ORGNO 를 주문 시점에 보존 — 미체결 취소
(order-rvsecncl, 모의 VTTC0013U)의 KRX_FWDG_ORD_ORGNO 1순위 출처. nullable(기존 행 NULL).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("order_audit_log") as b:
        b.add_column(sa.Column("broker_org_no", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log") as b:
        b.drop_column("broker_org_no")
