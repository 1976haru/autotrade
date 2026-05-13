"""체크리스트 #68: 통합 audit_event facade 단위 테스트.

검증 invariant:
  - log_audit_event는 정상 입력 시 row INSERT + 모든 필드 영구화
  - Secret 패턴 발견 시 SecretLeakError (fail-closed, redaction 아님)
  - log_audit_event는 *기존 audit 테이블을 수정하지 않는다*
  - archive_event는 row를 삭제하지 *않고* archived=True만 set, 멱등
  - 본 모듈에 broker / OrderExecutor / route_order import 0건
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.audit.events import (
    AuditEventNotFoundError,
    EventType,
    SecretLeakError,
    Severity,
    SourceKind,
    archive_event,
    build_ai_proposal_event,
    build_approval_decision_event,
    build_emergency_stop_event,
    build_risk_block_event,
    build_signal_event,
    log_audit_event,
)
from app.db.base import Base
from app.db.models import AuditEvent, OrderAuditLog


def _session():
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False,
                         expire_on_commit=False)


# ====================================================================
# log_audit_event — happy path
# ====================================================================


def test_log_audit_event_persists_row_with_all_fields():
    Session = _session()
    with Session() as db:
        row = log_audit_event(
            db,
            event_type=EventType.SIGNAL,
            severity=Severity.INFO,
            source=SourceKind.STRATEGY,
            actor="agent-1",
            symbol="005930",
            strategy="sma_crossover",
            mode="SIMULATION",
            target_kind="OrderAuditLog",
            target_id=42,
            summary="BUY signal on 005930",
            reason="SMA crossover up",
            details={"confidence": 80, "indicators": {"sma_short": 60500}},
        )
        assert row.id > 0
        assert row.event_type == "SIGNAL"
        assert row.severity   == "INFO"
        assert row.source     == "STRATEGY"
        assert row.actor      == "agent-1"
        assert row.symbol     == "005930"
        assert row.strategy   == "sma_crossover"
        assert row.target_kind == "OrderAuditLog"
        assert row.target_id   == 42
        assert row.summary    == "BUY signal on 005930"
        assert row.reason     == "SMA crossover up"
        assert row.details["confidence"] == 80
        assert row.archived is False
        assert row.archived_at is None


def test_log_audit_event_uses_default_severity_and_source():
    Session = _session()
    with Session() as db:
        row = log_audit_event(
            db, event_type=EventType.SYSTEM,
            summary="system boot",
        )
        assert row.severity == "INFO"
        assert row.source   == "SYSTEM"


def test_log_audit_event_accepts_string_event_type():
    """StrEnum 외에 그냥 str도 받음 — caller가 새 enum 값 추가 전 임시 사용."""
    Session = _session()
    with Session() as db:
        row = log_audit_event(
            db, event_type="CUSTOM_TYPE", summary="adhoc event",
        )
        assert row.event_type == "CUSTOM_TYPE"


# ====================================================================
# Secret leak — fail-closed
# ====================================================================


def test_log_audit_event_rejects_kis_app_key_in_summary():
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError, match="forbidden secret pattern"):
            log_audit_event(
                db, event_type=EventType.SYSTEM,
                summary="leak: KIS_APP_KEY=ABCDEFG1234567",
            )


def test_log_audit_event_rejects_anthropic_key_in_reason():
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError):
            log_audit_event(
                db, event_type=EventType.AI_PROPOSAL,
                summary="AI proposal",
                reason="ANTHROPIC_API_KEY=sk-xyz123abc",
            )


def test_log_audit_event_rejects_telegram_bot_token_pattern_in_details():
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError):
            log_audit_event(
                db, event_type=EventType.NOTIFICATION,
                summary="telegram test",
                details={"token": "123456789:AAH-abcDEFghiJKLmnoPQRstuVWXyz123"},
            )


def test_log_audit_event_rejects_korean_account_number_in_details_value():
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError):
            log_audit_event(
                db, event_type=EventType.ORDER_REQUEST,
                summary="order",
                details={"account": "501-86-66710"},
            )


def test_log_audit_event_rejects_secret_in_nested_details_dict():
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError):
            log_audit_event(
                db, event_type=EventType.AI_PROPOSAL,
                summary="nested",
                details={
                    "model": "claude",
                    "config": {"OPENAI_API_KEY": "sk-leak-value"},
                },
            )


def test_log_audit_event_secret_in_actor_field_rejected():
    """actor 필드도 검사 — 'KIS_APP_KEY=...' 같은 사고 차단."""
    Session = _session()
    with Session() as db:
        with pytest.raises(SecretLeakError):
            log_audit_event(
                db, event_type=EventType.OPERATOR_NOTE,
                summary="note",
                actor="KIS_APP_KEY=ABCDEFG1234567",
            )


# ====================================================================
# archive_event — append-only invariant
# ====================================================================


def test_archive_event_sets_archived_without_deleting():
    Session = _session()
    with Session() as db:
        row = log_audit_event(
            db, event_type=EventType.OPERATOR_NOTE, summary="test note",
        )
        original_id = row.id
        archive_event(db, original_id, archived_by="ops1", note="manual review done")

    # row가 *삭제되지 않고* 그대로 보존됐는지 새 session에서 재조회
    with Session() as db:
        all_rows = db.execute(select(AuditEvent)).scalars().all()
        assert len(all_rows) == 1
        archived = all_rows[0]
        assert archived.id == original_id
        assert archived.archived is True
        assert archived.archived_by == "ops1"
        assert archived.archive_note == "manual review done"
        assert archived.archived_at is not None


def test_archive_event_is_idempotent():
    """이미 archived인 row에 다시 호출해도 archived_by / note 덮어쓰지 *않음*."""
    Session = _session()
    with Session() as db:
        row = log_audit_event(
            db, event_type=EventType.OPERATOR_NOTE, summary="x",
        )
        rid = row.id
        archive_event(db, rid, archived_by="first", note="first note")
        archive_event(db, rid, archived_by="second", note="second note")
    with Session() as db:
        r = db.execute(select(AuditEvent).where(AuditEvent.id == rid)).scalar_one()
        # 멱등 — 첫 archive 정보 보존
        assert r.archived_by == "first"
        assert r.archive_note == "first note"


def test_archive_event_404_for_missing_row():
    Session = _session()
    with Session() as db:
        with pytest.raises(AuditEventNotFoundError):
            archive_event(db, 9999)


# ====================================================================
# Builders — 형식 검증
# ====================================================================


def test_build_signal_event_basic():
    e = build_signal_event(symbol="005930", action="BUY",
                            strategy="sma_crossover", confidence=80,
                            reasons=["sma cross up"])
    assert e.event_type == EventType.SIGNAL
    assert e.severity == Severity.INFO
    assert e.source == SourceKind.STRATEGY
    assert e.symbol == "005930"
    assert e.strategy == "sma_crossover"
    assert "BUY" in e.summary
    assert e.details["action"] == "BUY"
    assert e.details["confidence"] == 80


def test_build_risk_block_event_carries_audit_id_when_ai():
    e = build_risk_block_event(
        symbol="005930",
        reasons=["max_order_notional", "stale price"],
        requested_by_ai=True, audit_id=17,
    )
    assert e.event_type == EventType.RISK_BLOCK
    assert e.severity == Severity.WARN
    assert e.source == SourceKind.AI
    assert e.target_kind == "OrderAuditLog"
    assert e.target_id == 17
    assert e.details["requested_by_ai"] is True


def test_build_ai_proposal_event_is_not_order_intent():
    """AI proposal 이벤트의 details.is_order_intent는 항상 False — invariant lock."""
    e = build_ai_proposal_event(
        symbol="005930", side="BUY", quantity=1,
        model="claude", confidence=75,
        supporting_reasons=["earnings beat"],
        opposing_reasons=["high volatility"],
        risk_note="watch stop-loss",
    )
    assert e.event_type == EventType.AI_PROPOSAL
    assert e.source == SourceKind.AI
    assert e.details["is_order_intent"] is False
    assert e.details["confidence"] == 75
    assert e.details["supporting_reasons"] == ["earnings beat"]
    assert e.details["opposing_reasons"] == ["high volatility"]


def test_build_emergency_stop_on_is_critical():
    e = build_emergency_stop_event(
        enabled=True, level="LEVEL_1",
        reason_code="manual_operator", decided_by="ops1",
    )
    assert e.severity == Severity.CRITICAL
    assert e.actor == "ops1"
    assert e.event_type == EventType.EMERGENCY_STOP


def test_build_emergency_stop_off_is_info():
    e = build_emergency_stop_event(enabled=False, decided_by="ops1")
    assert e.severity == Severity.INFO


def test_build_approval_decision_severity_by_decision():
    e_ok = build_approval_decision_event(
        approval_id=7, decision="APPROVED", decided_by="ops1",
    )
    e_no = build_approval_decision_event(
        approval_id=8, decision="REJECTED", decided_by="ops1",
        note="신호 노후",
    )
    assert e_ok.severity == Severity.INFO
    assert e_no.severity == Severity.WARN
    assert e_no.reason == "신호 노후"


# ====================================================================
# Invariant — 기존 audit 테이블 수정 없음
# ====================================================================


def test_log_audit_event_does_not_touch_order_audit_log():
    """log_audit_event는 OrderAuditLog row를 수정 / 추가 / 삭제하지 *않는다*."""
    Session = _session()
    with Session() as db:
        before = db.execute(
            select(OrderAuditLog).order_by(OrderAuditLog.id)
        ).scalars().all()
        log_audit_event(
            db, event_type=EventType.SIGNAL, summary="x", symbol="005930",
        )
        after = db.execute(
            select(OrderAuditLog).order_by(OrderAuditLog.id)
        ).scalars().all()
        # OrderAuditLog는 변경 없음 (양쪽 모두 0 또는 동일)
        assert len(before) == len(after)


def test_module_does_not_import_broker_or_executor():
    """app.audit.events 모듈은 broker / OrderExecutor / route_order 어떤 것도
    import 또는 호출하지 *않는다*. 통합 facade는 단일 책임 (감사 로그)."""
    from app.audit import events as mod
    src = inspect.getsource(mod)
    forbidden = (
        "from app.brokers", "from app.execution", "from app.permission",
        "from app.risk.risk_manager", "broker.place_order(",
        "broker.cancel_order(", "route_order(",
    )
    for needle in forbidden:
        assert needle not in src, f"forbidden import / call: {needle!r}"


def test_module_has_no_delete_function():
    """audit_event row를 *delete*하는 함수가 노출되지 않는다 — archive만 가능.

    `from app.audit import events as mod` 의 public 멤버에 'delete' 단어가
    있어선 안 됨.
    """
    from app.audit import events as mod
    public = [n for n in dir(mod) if not n.startswith("_")]
    forbidden = {"delete_event", "delete", "remove_event", "drop_event"}
    intersection = forbidden & {n.lower() for n in public}
    assert intersection == set(), f"forbidden delete API: {intersection}"
