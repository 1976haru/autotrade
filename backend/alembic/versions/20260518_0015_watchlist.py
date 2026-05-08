"""add watchlist + watchlist_item tables (#18)

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 18: watchlist 그룹 — 운영자가 직접 등록한 universe 후보군.
    op.create_table(
        "watchlist",
        sa.Column("id",          sa.Integer(), nullable=False),
        sa.Column("created_at",  sa.DateTime(), nullable=False),
        sa.Column("updated_at",  sa.DateTime(), nullable=False),
        sa.Column("name",        sa.String(length=64),  nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_active",   sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_watchlist_created_at"), "watchlist", ["created_at"])
    op.create_index(op.f("ix_watchlist_name"),       "watchlist", ["name"])
    op.create_index(op.f("ix_watchlist_is_active"),  "watchlist", ["is_active"])

    # 18: watchlist_item — 그룹별 종목 행. (watchlist_id, symbol) UNIQUE.
    op.create_table(
        "watchlist_item",
        sa.Column("id",           sa.Integer(),  nullable=False),
        sa.Column("created_at",   sa.DateTime(), nullable=False),
        sa.Column("watchlist_id", sa.Integer(),  nullable=False),
        sa.Column("symbol",       sa.String(length=16),  nullable=False),
        sa.Column("name",         sa.String(length=64),  nullable=True),
        sa.Column("market",       sa.String(length=32),  nullable=True),
        sa.Column("sector",       sa.String(length=64),  nullable=True),
        sa.Column("note",         sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["watchlist_id"], ["watchlist.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_item_symbol"),
    )
    op.create_index(op.f("ix_watchlist_item_created_at"),   "watchlist_item", ["created_at"])
    op.create_index(op.f("ix_watchlist_item_watchlist_id"), "watchlist_item", ["watchlist_id"])
    op.create_index(op.f("ix_watchlist_item_symbol"),       "watchlist_item", ["symbol"])


def downgrade() -> None:
    op.drop_index(op.f("ix_watchlist_item_symbol"),       table_name="watchlist_item")
    op.drop_index(op.f("ix_watchlist_item_watchlist_id"), table_name="watchlist_item")
    op.drop_index(op.f("ix_watchlist_item_created_at"),   table_name="watchlist_item")
    op.drop_table("watchlist_item")

    op.drop_index(op.f("ix_watchlist_is_active"),  table_name="watchlist")
    op.drop_index(op.f("ix_watchlist_name"),       table_name="watchlist")
    op.drop_index(op.f("ix_watchlist_created_at"), table_name="watchlist")
    op.drop_table("watchlist")
