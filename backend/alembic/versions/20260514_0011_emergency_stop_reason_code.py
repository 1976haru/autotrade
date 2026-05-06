"""add emergency_stop_event.reason_code (153)

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-14 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 153: 운영자가 emergency_stop을 토글한 이유를 구조화된 코드로 기록.
    # NULL = 0011 이전 row 또는 미명시. enum 값은 app.risk.emergency_reasons.
    with op.batch_alter_table("emergency_stop_event", schema=None) as batch_op:
        batch_op.add_column(sa.Column("reason_code", sa.String(length=32), nullable=True))
        batch_op.create_index(
            op.f("ix_emergency_stop_event_reason_code"),
            ["reason_code"],
        )


def downgrade() -> None:
    with op.batch_alter_table("emergency_stop_event", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_emergency_stop_event_reason_code"))
        batch_op.drop_column("reason_code")
