"""add order_audit_log.client_order_id

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 140: 같은 client_order_id로 두 번째 주문이 들어오면 거부 (idempotency).
    # broker 단의 ID(broker_order_id)와는 별개 — client_order_id는 호출자가
    # 발급한 키이고, 같은 키로 onClick double-fire 같은 사고가 생기면 두 번
    # 체결되는 위험이 있다.
    #
    # 컬럼 자체는 nullable — 호출자가 ID를 안 보낸 주문은 NULL이고 idempotency
    # 검사도 X. 호출자가 ID를 보낸 경우만 검사. unique 제약은 두지 않는다 —
    # NULL 다중 row 허용 + 검사 자체는 router에서 query로.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("client_order_id", sa.String(length=64),
                      nullable=True, index=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_column("client_order_id")
