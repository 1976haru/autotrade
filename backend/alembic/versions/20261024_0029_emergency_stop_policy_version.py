"""P0-SAFETY-FIX-V1: emergency_stop_event.policy_version (단계별 주문 허용 범위 버전)

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-24 00:00:00.000000

기존 행은 NULL — 이전 정책(LEVEL_1 도 청산 매도·취소까지 차단)으로 기록된 정지로 간주해
복원 시 legacy strict 를 유지한다 (자동 완화 금지). 데이터 변경 0 — 컬럼 추가만.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("emergency_stop_event") as b:
        b.add_column(sa.Column("policy_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("emergency_stop_event") as b:
        b.drop_column("policy_version")
