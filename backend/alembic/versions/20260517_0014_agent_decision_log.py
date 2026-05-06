"""add agent_decision_log table (185)

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_decision_log",
        sa.Column("id",         sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("symbol",     sa.String(length=16), nullable=True),
        sa.Column("mode",       sa.String(length=32), nullable=False),
        sa.Column("decision",   sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("reasons",    sa.JSON(),  nullable=False),
        sa.Column("meta",       sa.JSON(),  nullable=True),
        sa.Column("chain_id",   sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_decision_log_created_at"), "agent_decision_log", ["created_at"])
    op.create_index(op.f("ix_agent_decision_log_agent_name"), "agent_decision_log", ["agent_name"])
    op.create_index(op.f("ix_agent_decision_log_symbol"),     "agent_decision_log", ["symbol"])
    op.create_index(op.f("ix_agent_decision_log_mode"),       "agent_decision_log", ["mode"])
    op.create_index(op.f("ix_agent_decision_log_decision"),   "agent_decision_log", ["decision"])
    op.create_index(op.f("ix_agent_decision_log_chain_id"),   "agent_decision_log", ["chain_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_decision_log_chain_id"),   table_name="agent_decision_log")
    op.drop_index(op.f("ix_agent_decision_log_decision"),   table_name="agent_decision_log")
    op.drop_index(op.f("ix_agent_decision_log_mode"),       table_name="agent_decision_log")
    op.drop_index(op.f("ix_agent_decision_log_symbol"),     table_name="agent_decision_log")
    op.drop_index(op.f("ix_agent_decision_log_agent_name"), table_name="agent_decision_log")
    op.drop_index(op.f("ix_agent_decision_log_created_at"), table_name="agent_decision_log")
    op.drop_table("agent_decision_log")
