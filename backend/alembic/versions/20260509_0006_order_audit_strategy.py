"""add order_audit_log.strategy

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 138: 어떤 전략이 만든 주문인지 audit row에서 식별 가능하게 한다. LiveEngine
    # 이 자체 신호로 만든 주문, 운영자 수동 주문, 외부 호출 주문이 audit에서
    # 모두 같은 layout으로 surface되는데 출처는 구분되어야 운영자가 사후 분석
    # 시 strategy 별 흐름을 trace할 수 있다.
    #
    # 자유 문자열, nullable — 0006 이전 row와 strategy 미명시 호출(예: 운영자
    # 수동 주문)은 NULL. backtest_run.strategy(NOT NULL)와 의미는 같지만 audit
    # 도메인은 nullable로 두는 게 자연스럽다. 향후 OrderAuditLog 기반 scoreboard
    # 통합(137-followup) 시 이 컬럼이 Strategy Scoreboard에 합산된다.
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("strategy", sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_column("strategy")
