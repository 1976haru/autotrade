"""Enforce account-scoped idempotency keys before an order is sent.

Revision ID: 0032
Revises: 0031
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    duplicates = bind.execute(sa.text(
        "select account_id, client_order_id from order_audit_log "
        "where account_id is not null and client_order_id is not null "
        "group by account_id, client_order_id having count(*) > 1 limit 1"
    )).first()
    if duplicates is not None:
        raise RuntimeError("0032 refused: duplicate account-scoped client_order_id exists")
    with op.batch_alter_table("order_audit_log") as batch:
        batch.create_unique_constraint(
            "uq_order_audit_account_client_order", ["account_id", "client_order_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("order_audit_log") as batch:
        batch.drop_constraint("uq_order_audit_account_client_order", type_="unique")
