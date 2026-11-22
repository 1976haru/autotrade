"""P0 ACCOUNT-ISOLATION: eod_liquidation_state 계좌별 분리 (account_key)

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-27 00:00:00.000000

거래일 1행(UNIQUE trading_date) → (거래일, 계좌) 1행. account_key = order_audit_log.account_id 와
같은 해시 스코프, '' = LEGACY/스코프 없음(기존 행). 기존 행 값은 바꾸지 않는다(새 컬럼 기본값 '').
같은 날 계좌를 바꿔도 새 계좌 EOD 가 구계좌의 확정/미완료 행을 재사용하지 않게 한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("eod_liquidation_state") as b:
        b.add_column(sa.Column("account_key", sa.String(length=48), nullable=False, server_default=""))
        b.drop_index("ix_eod_liquidation_state_trading_date")
        b.create_index("ix_eod_liquidation_state_trading_date", ["trading_date"], unique=False)
        b.create_index("uq_eod_liquidation_state_date_account", ["trading_date", "account_key"], unique=True)


def downgrade() -> None:
    # 계좌별 행이 같은 날 2개 이상이면 UNIQUE(trading_date) 복원이 불가 — 데이터 손실 대신 중단.
    bind = op.get_bind()
    dup = bind.execute(sa.text(
        "select trading_date from eod_liquidation_state group by trading_date having count(*) > 1")).first()
    if dup is not None:
        raise RuntimeError(f"downgrade 0031 refused: multiple account rows for {dup[0]} — resolve manually")
    with op.batch_alter_table("eod_liquidation_state") as b:
        b.drop_index("uq_eod_liquidation_state_date_account")
        b.drop_index("ix_eod_liquidation_state_trading_date")
        b.create_index("ix_eod_liquidation_state_trading_date", ["trading_date"], unique=True)
        b.drop_column("account_key")
