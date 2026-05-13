"""add audit_event table (Audit Event facade — append-only, no delete)

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-25 00:00:00.000000

체크리스트 #68: 통합 감사 이벤트 timeline. 기존 도메인 테이블(OrderAuditLog
/ PendingApproval / AgentDecisionLog / EmergencyStopEvent / VirtualOrder /
FuturesOrderAuditLog)을 *대체하지 않고* 그 위에 cross-cutting view를 제공.

절대 원칙:
- append-only: 본 PR은 audit_event row를 *delete*하는 SQL이나 API를 추가하지
  않는다. `archived` flag만 변경 가능 (cold storage / 노이즈 분리용).
- Secret redaction: `app.audit.events.log_audit_event`가 INSERT 전 fail-closed
  검사 — API key / Anthropic / KIS app_key / chat_id / 한국 계좌번호 패턴
  발견 시 SecretLeakError로 raise. redaction 아닌 거부.
- 기존 테이블 schema 변경 0건. 본 마이그레이션은 *새 테이블 1건*만 추가.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("severity",   sa.String(length=16), nullable=False),
        sa.Column("source",     sa.String(length=16), nullable=False),
        sa.Column("actor",      sa.String(length=64), nullable=True),
        sa.Column("symbol",     sa.String(length=16), nullable=True),
        sa.Column("strategy",   sa.String(length=64), nullable=True),
        sa.Column("mode",       sa.String(length=32), nullable=True),
        sa.Column("target_kind", sa.String(length=32), nullable=True),
        sa.Column("target_id",   sa.Integer(), nullable=True),
        sa.Column("summary",    sa.String(length=255), nullable=False),
        sa.Column("reason",     sa.String(length=255), nullable=True),
        sa.Column("details",    sa.JSON(), nullable=True),
        sa.Column("archived",     sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at",  sa.DateTime(), nullable=True),
        sa.Column("archived_by",  sa.String(length=64), nullable=True),
        sa.Column("archive_note", sa.String(length=255), nullable=True),
    )

    op.create_index("ix_audit_event_created_at",  "audit_event", ["created_at"])
    op.create_index("ix_audit_event_event_type",  "audit_event", ["event_type"])
    op.create_index("ix_audit_event_severity",    "audit_event", ["severity"])
    op.create_index("ix_audit_event_source",      "audit_event", ["source"])
    op.create_index("ix_audit_event_actor",       "audit_event", ["actor"])
    op.create_index("ix_audit_event_symbol",      "audit_event", ["symbol"])
    op.create_index("ix_audit_event_strategy",    "audit_event", ["strategy"])
    op.create_index("ix_audit_event_target_kind", "audit_event", ["target_kind"])
    op.create_index("ix_audit_event_target_id",   "audit_event", ["target_id"])
    op.create_index("ix_audit_event_archived",    "audit_event", ["archived"])


def downgrade() -> None:
    # downgrade는 정의되어 있지만 본 PR 정책상 운영 환경에서 호출하지 않는다.
    # audit row 삭제 = 감사 추적 손실이므로 *upgrade* 방향만 권장.
    op.drop_index("ix_audit_event_archived",    table_name="audit_event")
    op.drop_index("ix_audit_event_target_id",   table_name="audit_event")
    op.drop_index("ix_audit_event_target_kind", table_name="audit_event")
    op.drop_index("ix_audit_event_strategy",    table_name="audit_event")
    op.drop_index("ix_audit_event_symbol",      table_name="audit_event")
    op.drop_index("ix_audit_event_actor",       table_name="audit_event")
    op.drop_index("ix_audit_event_source",      table_name="audit_event")
    op.drop_index("ix_audit_event_severity",    table_name="audit_event")
    op.drop_index("ix_audit_event_event_type",  table_name="audit_event")
    op.drop_index("ix_audit_event_created_at",  table_name="audit_event")
    op.drop_table("audit_event")
