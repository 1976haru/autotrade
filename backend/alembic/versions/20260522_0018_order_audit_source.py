"""add order_audit_log.source (#40 — Order Executor)

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # #40: 주문 source 분류 컬럼. STRATEGY / AI / MANUAL / OPERATOR_OVERRIDE /
    # UNKNOWN. app/execution/order_executor.py의 OrderSource enum과 1:1.
    # NULL = 0018 이전 row 또는 호출자 미명시 — `OrderSource.UNKNOWN`로 surface.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source", sa.String(length=32), nullable=True))
        batch_op.create_index(
            op.f("ix_order_audit_log_source"),
            ["source"],
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_order_audit_log_source"))
        batch_op.drop_column("source")
