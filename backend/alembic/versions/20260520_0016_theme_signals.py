"""add theme_signals table (#22)

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "theme_signals",
        sa.Column("id",              sa.Integer(),  nullable=False),
        sa.Column("created_at",      sa.DateTime(), nullable=False),
        sa.Column("theme",           sa.String(length=64),  nullable=False),
        sa.Column("keywords",        sa.JSON(),     nullable=False),
        sa.Column("related_symbols", sa.JSON(),     nullable=False),
        sa.Column("score",           sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("grade",           sa.String(length=16),  nullable=False, server_default="WEAK"),
        sa.Column("confidence",      sa.Integer(),  nullable=False, server_default="0"),
        sa.Column("source",          sa.String(length=32),  nullable=False),
        sa.Column("provider",        sa.String(length=32),  nullable=False),
        sa.Column("summary",         sa.Text(),     nullable=True),
        sa.Column("raw",             sa.JSON(),     nullable=True),
        sa.Column("used_for_order",  sa.Boolean(),  nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_theme_signals_created_at"),     "theme_signals", ["created_at"])
    op.create_index(op.f("ix_theme_signals_theme"),          "theme_signals", ["theme"])
    op.create_index(op.f("ix_theme_signals_score"),          "theme_signals", ["score"])
    op.create_index(op.f("ix_theme_signals_grade"),          "theme_signals", ["grade"])
    op.create_index(op.f("ix_theme_signals_source"),         "theme_signals", ["source"])
    op.create_index(op.f("ix_theme_signals_provider"),       "theme_signals", ["provider"])
    op.create_index(op.f("ix_theme_signals_used_for_order"), "theme_signals", ["used_for_order"])


def downgrade() -> None:
    for col in ("used_for_order", "provider", "source", "grade", "score",
                "theme", "created_at"):
        op.drop_index(op.f(f"ix_theme_signals_{col}"), table_name="theme_signals")
    op.drop_table("theme_signals")
