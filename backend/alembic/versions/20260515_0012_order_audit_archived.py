"""add order_audit_log.archived (168)

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-15 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 168: hot/cold 분리용 flag. routes_audit hot query가 archived=False만 본다.
    # default False → 기존 row는 모두 hot으로 자동 분류 (운영자 의도적 archive
    # 호출 전까지).
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "archived", sa.Boolean(), nullable=False, server_default=sa.false(),
        ))
        batch_op.create_index(
            op.f("ix_order_audit_log_archived"),
            ["archived"],
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_order_audit_log_archived"))
        batch_op.drop_column("archived")
