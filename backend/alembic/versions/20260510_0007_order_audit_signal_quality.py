"""add order_audit_log.signal_strength + signal_confidence

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 139: 136이 LiveEngine TickResult에 quality(0-100 strength/confidence)를
    # 도입했지만 audit row에는 미저장 — view-time 표시만 가능했다. 사후 분석
    # ('어떤 신뢰도에서 만든 주문이 결국 손익이 어떻게 됐나') 위해 OrderAuditLog
    # 에 두 컬럼을 추가해 영구화.
    #
    # nullable: 0007 이전 row + signal quality가 산출되지 않는 경로(수동 주문
    # 등)는 NULL. quality는 0-100 정수라 SmallInteger(또는 Integer)로 충분.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column("signal_strength",   sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("signal_confidence", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_column("signal_confidence")
        batch_op.drop_column("signal_strength")
