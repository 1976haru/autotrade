"""#2-09: Paper Auto Loop ledger 테스트.

invariant:
- `PaperLoopEvent` `is_order_signal=False` / `auto_apply_allowed=False` /
  `is_live_authorization=False` (`__post_init__` ValueError 가드).
- trade event (BUY/SELL/EXIT) 는 `loop_state="RUNNING"` 에서만 기록 허용 —
  다른 state 에서 시도 시 `LedgerStateError`.
- HOLD / NO_OP 는 모든 state 에서 기록 가능 (판단 로그).
- ledger metadata 에 secret 패턴 발견 시 `SecretInLedgerError` 거부.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건 (정적 grep).
- schema 에 API key / Secret / 계좌번호 필드 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.auto_paper.events import (
    DecisionAction,
    PaperFillStatus,
    PaperLoopEvent,
    TRADE_ACTIONS,
    now_iso,
)
from app.auto_paper.ledger import (
    DEFAULT_LEDGER_CAPACITY,
    LedgerStateError,
    PaperLoopLedger,
    SecretInLedgerError,
    get_ledger,
    record_paper_event,
    reset_ledger_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_ledger():
    """각 test 마다 singleton ledger reset."""
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


def _make_event(
    *,
    loop_state="RUNNING",
    strategy="sma_crossover",
    symbol="005930",
    decision_action=DecisionAction.HOLD,
    confidence=0.65,
    reason="test reason",
    paper_order_id=None,
    paper_fill_status=PaperFillStatus.NA,
    virtual_position_delta=0,
    pnl_estimate=0.0,
    metadata=None,
):
    return PaperLoopEvent(
        event_id="evt-test-001",
        timestamp=now_iso(),
        loop_state=loop_state,
        strategy=strategy,
        symbol=symbol,
        decision_action=decision_action,
        confidence=confidence,
        reason=reason,
        paper_order_id=paper_order_id,
        paper_fill_status=paper_fill_status,
        virtual_position_delta=virtual_position_delta,
        pnl_estimate=pnl_estimate,
        metadata=metadata or {},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. PaperLoopEvent — invariants + 13 필수 필드
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperLoopEventInvariants:
    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError):
            PaperLoopEvent(
                event_id="e", timestamp="t", loop_state="RUNNING",
                strategy="s", symbol="x",
                decision_action=DecisionAction.HOLD,
                confidence=None, reason="r",
                is_order_signal=True,
            )

    def test_auto_apply_allowed_must_be_false(self):
        with pytest.raises(ValueError):
            PaperLoopEvent(
                event_id="e", timestamp="t", loop_state="RUNNING",
                strategy="s", symbol="x",
                decision_action=DecisionAction.HOLD,
                confidence=None, reason="r",
                auto_apply_allowed=True,
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError):
            PaperLoopEvent(
                event_id="e", timestamp="t", loop_state="RUNNING",
                strategy="s", symbol="x",
                decision_action=DecisionAction.HOLD,
                confidence=None, reason="r",
                is_live_authorization=True,
            )

    def test_confidence_range_check(self):
        # 정상.
        _make_event(confidence=0.5)
        _make_event(confidence=None)
        # 범위 위반.
        with pytest.raises(ValueError):
            _make_event(confidence=1.5)
        with pytest.raises(ValueError):
            _make_event(confidence=-0.1)

    def test_decision_action_must_be_enum(self):
        with pytest.raises(ValueError):
            PaperLoopEvent(
                event_id="e", timestamp="t", loop_state="RUNNING",
                strategy="s", symbol="x",
                decision_action="HOLD",   # type: ignore[arg-type]
                confidence=None, reason="r",
            )

    def test_required_string_fields_non_empty(self):
        with pytest.raises(ValueError):
            _make_event(strategy="")
        with pytest.raises(ValueError):
            _make_event(symbol="")

    def test_to_dict_carries_invariants(self):
        ev = _make_event(decision_action=DecisionAction.BUY,
                          paper_order_id="paper-1",
                          paper_fill_status=PaperFillStatus.PAPER_FILLED,
                          virtual_position_delta=10,
                          pnl_estimate=500.0)
        d = ev.to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False
        # 13 필수 필드 + invariants 모두 carry.
        required = [
            "timestamp", "loop_state", "strategy", "symbol",
            "decision_action", "confidence", "reason", "risk_flags",
            "paper_order_id", "paper_fill_status",
            "virtual_position_delta", "pnl_estimate",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
        ]
        for f in required:
            assert f in d, f"missing required field: {f}"

    def test_trade_actions_set(self):
        assert TRADE_ACTIONS == frozenset({
            DecisionAction.BUY, DecisionAction.SELL, DecisionAction.EXIT,
        })
        # is_trade_event() helper.
        assert _make_event(decision_action=DecisionAction.BUY).is_trade_event()
        assert not _make_event(decision_action=DecisionAction.HOLD).is_trade_event()
        assert not _make_event(decision_action=DecisionAction.NO_OP).is_trade_event()


# ─────────────────────────────────────────────────────────────────────────────
# 2. State-aware 기록 — trade event 는 RUNNING 에서만
# ─────────────────────────────────────────────────────────────────────────────


class TestStateAwareRecording:
    def test_running_allows_buy(self):
        ledger = PaperLoopLedger()
        ev = _make_event(loop_state="RUNNING",
                          decision_action=DecisionAction.BUY,
                          paper_order_id="paper-1",
                          paper_fill_status=PaperFillStatus.PAPER_FILLED)
        ledger.record(ev)
        assert len(ledger) == 1

    def test_paused_blocks_buy(self):
        ledger = PaperLoopLedger()
        ev = _make_event(loop_state="PAUSED",
                          decision_action=DecisionAction.BUY)
        with pytest.raises(LedgerStateError):
            ledger.record(ev)
        assert len(ledger) == 0

    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "STOPPED", "MARKET_CLOSED", "EMERGENCY_STOP",
    ])
    @pytest.mark.parametrize("action", [
        DecisionAction.BUY, DecisionAction.SELL, DecisionAction.EXIT,
    ])
    def test_non_running_blocks_all_trade_events(self, state, action):
        """user spec: PAUSED / WAITING_MARKET / STOPPED / MARKET_CLOSED /
        EMERGENCY_STOP 에서 신규 ledger trade event 0건."""
        ledger = PaperLoopLedger()
        ev = _make_event(loop_state=state, decision_action=action)
        with pytest.raises(LedgerStateError):
            ledger.record(ev)
        assert len(ledger) == 0

    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "RUNNING", "STOPPED",
        "MARKET_CLOSED", "EMERGENCY_STOP",
    ])
    def test_hold_allowed_in_any_state(self, state):
        """user spec: AI 판단이 HOLD 여도 '판단 로그' 는 남길 수 있음."""
        ledger = PaperLoopLedger()
        ev = _make_event(loop_state=state, decision_action=DecisionAction.HOLD)
        ledger.record(ev)
        assert len(ledger) == 1

    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "RUNNING", "STOPPED",
        "MARKET_CLOSED", "EMERGENCY_STOP",
    ])
    def test_no_op_allowed_in_any_state(self, state):
        """tick heartbeat / audit event 는 state 무관."""
        ledger = PaperLoopLedger()
        ev = _make_event(loop_state=state, decision_action=DecisionAction.NO_OP)
        ledger.record(ev)
        assert len(ledger) == 1

    def test_reject_count_increments(self):
        ledger = PaperLoopLedger()
        for _ in range(3):
            with pytest.raises(LedgerStateError):
                ledger.record(_make_event(
                    loop_state="PAUSED", decision_action=DecisionAction.BUY,
                ))
        assert ledger.stats()["reject_count"] == 3


# ─────────────────────────────────────────────────────────────────────────────
# 3. Secret guard — metadata key/value 검사
# ─────────────────────────────────────────────────────────────────────────────


class TestSecretGuard:
    @pytest.mark.parametrize("bad_key", [
        "api_key", "API_KEY", "secret", "anthropic_api_key",
        "kis_app_key", "kis_app_secret", "access_token", "password",
        "account_no", "account_number", "private_key",
    ])
    def test_forbidden_metadata_key(self, bad_key):
        ledger = PaperLoopLedger()
        ev = _make_event(metadata={bad_key: "anything"})
        with pytest.raises(SecretInLedgerError):
            ledger.record(ev)

    @pytest.mark.parametrize("bad_value", [
        "sk-abcdefghijklmnopqrstuvwxyz123456",
        "sk-ant-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "Bearer abcdefghij1234567890123456",
        "PSTabcdefghij0123456789abcdef123456",
    ])
    def test_forbidden_metadata_value_pattern(self, bad_value):
        ledger = PaperLoopLedger()
        ev = _make_event(metadata={"opaque_field": bad_value})
        with pytest.raises(SecretInLedgerError):
            ledger.record(ev)

    def test_safe_metadata_allowed(self):
        ledger = PaperLoopLedger()
        ev = _make_event(metadata={
            "regime": "TREND_UP",
            "score": 0.65,
            "indicators": {"rsi": 35.0, "macd": 0.12},
        })
        ledger.record(ev)
        assert len(ledger) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 4. record_paper_event helper + 기록 / 조회
# ─────────────────────────────────────────────────────────────────────────────


class TestRecordPaperEvent:
    def test_basic_hold_record(self):
        ev = record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.HOLD,
            reason="trend not confirmed",
            confidence=0.55,
        )
        assert ev.event_id.startswith("paper-evt-")
        assert get_ledger().recent(1)[0].event_id == ev.event_id

    def test_paper_buy_with_fill(self):
        ev = record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.BUY,
            reason="MA cross + volume confirm",
            confidence=0.78,
            paper_order_id="paper-2026-05-18-001",
            paper_fill_status=PaperFillStatus.PAPER_FILLED,
            virtual_position_delta=10,
            pnl_estimate=0.0,
        )
        d = ev.to_dict()
        assert d["decision_action"] == "BUY"
        assert d["paper_order_id"] == "paper-2026-05-18-001"
        assert d["paper_fill_status"] == "PAPER_FILLED"
        assert d["virtual_position_delta"] == 10

    def test_event_id_uniqueness(self):
        ids = set()
        for _ in range(20):
            ev = record_paper_event(
                loop_state="RUNNING", strategy="s", symbol="x",
                decision_action=DecisionAction.NO_OP, reason="tick",
            )
            ids.add(ev.event_id)
        assert len(ids) == 20

    def test_filter_by(self):
        record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.HOLD, reason="x",
        )
        record_paper_event(
            loop_state="RUNNING", strategy="rsi_reversion", symbol="000660",
            decision_action=DecisionAction.HOLD, reason="x",
        )
        record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.BUY, reason="x",
            paper_order_id="p-1", paper_fill_status=PaperFillStatus.PAPER_FILLED,
        )
        ledger = get_ledger()
        # by strategy.
        out = ledger.filter_by(strategy="sma_crossover")
        assert len(out) == 2
        # by action.
        out = ledger.filter_by(decision_action=DecisionAction.BUY)
        assert len(out) == 1
        # combined.
        out = ledger.filter_by(strategy="sma_crossover",
                               decision_action=DecisionAction.HOLD)
        assert len(out) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 5. Ledger capacity + stats
# ─────────────────────────────────────────────────────────────────────────────


class TestLedgerCapacity:
    def test_ring_capacity(self):
        ledger = PaperLoopLedger(capacity=5)
        for i in range(8):
            ev = _make_event(decision_action=DecisionAction.NO_OP)
            object.__setattr__(ev, "event_id", f"e-{i:03d}")
            ledger.record(ev)
        # 가장 오래된 3 개는 drop.
        assert len(ledger) == 5
        assert ledger.stats()["dropped_count"] == 3

    def test_default_capacity(self):
        assert DEFAULT_LEDGER_CAPACITY == 1000

    def test_invalid_capacity_rejected(self):
        with pytest.raises(ValueError):
            PaperLoopLedger(capacity=0)

    def test_clear(self):
        ledger = PaperLoopLedger()
        ledger.record(_make_event(decision_action=DecisionAction.HOLD))
        ledger.record(_make_event(decision_action=DecisionAction.HOLD))
        assert ledger.clear() == 2
        assert len(ledger) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 6. API endpoint integration — /api/auto-paper/ledger + /events
# ─────────────────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


class TestLedgerAPI:
    def test_empty_ledger_response(self):
        client = _client()
        r = client.get("/api/auto-paper/ledger")
        assert r.status_code == 200
        body = r.json()
        assert body["events"] == []
        assert body["event_count"] == 0
        # invariants carry.
        assert body["is_order_signal"]       is False
        assert body["auto_apply_allowed"]    is False
        assert body["is_live_authorization"] is False
        # advisory_disclaimer.
        assert "Paper" in body["advisory_disclaimer"]

    def test_events_alias_returns_same_shape(self):
        client = _client()
        r1 = client.get("/api/auto-paper/ledger")
        r2 = client.get("/api/auto-paper/events")
        assert r1.status_code == 200 and r2.status_code == 200
        # 동일 shape.
        assert set(r1.json().keys()) == set(r2.json().keys())

    def test_recorded_events_visible_via_api(self):
        record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.HOLD,
            reason="trend not confirmed", confidence=0.55,
        )
        record_paper_event(
            loop_state="RUNNING", strategy="sma_crossover", symbol="005930",
            decision_action=DecisionAction.BUY,
            reason="confirmed", confidence=0.78,
            paper_order_id="p-1", paper_fill_status=PaperFillStatus.PAPER_FILLED,
            virtual_position_delta=10,
        )
        client = _client()
        r = client.get("/api/auto-paper/ledger")
        assert r.status_code == 200
        body = r.json()
        assert body["event_count"] == 2
        actions = {e["decision_action"] for e in body["events"]}
        assert actions == {"HOLD", "BUY"}
        # Secret 0건.
        joined = " ".join(str(v) for v in body["events"])
        assert "api_key" not in joined.lower()
        assert "secret" not in joined.lower()

    def test_filter_by_action(self):
        record_paper_event(
            loop_state="RUNNING", strategy="s", symbol="x",
            decision_action=DecisionAction.HOLD, reason="h",
        )
        record_paper_event(
            loop_state="RUNNING", strategy="s", symbol="x",
            decision_action=DecisionAction.BUY, reason="b",
            paper_order_id="p-1",
            paper_fill_status=PaperFillStatus.PAPER_FILLED,
        )
        client = _client()
        r = client.get("/api/auto-paper/ledger?action=BUY")
        assert r.status_code == 200
        events = r.json()["events"]
        assert all(e["decision_action"] == "BUY" for e in events)

    def test_invalid_action_returns_400(self):
        client = _client()
        r = client.get("/api/auto-paper/ledger?action=FOO")
        assert r.status_code == 400

    def test_response_no_secret_fields(self):
        client = _client()
        r = client.get("/api/auto-paper/ledger")
        text = r.text.lower()
        forbidden = [
            "anthropic_api_key", "openai_api_key", "kis_app_key",
            "kis_app_secret", "account_no", "account_number",
        ]
        for f in forbidden:
            assert f not in text


# ─────────────────────────────────────────────────────────────────────────────
# 7. Static guards — forbidden imports / safety flag mutation
# ─────────────────────────────────────────────────────────────────────────────


_LEDGER_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "ledger.py"
)
_EVENTS_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "events.py"
)


class TestNoForbiddenImports:
    def test_ledger_has_no_broker_imports(self):
        for path in (_LEDGER_PATH, _EVENTS_PATH):
            src = path.read_text(encoding="utf-8")
            forbidden = [
                r"from\s+app\.brokers\.kis\b",
                r"from\s+app\.brokers\.mock_broker\b",
                r"from\s+app\.execution\.executor\b",
                r"from\s+app\.execution\.order_router\b",
                r"broker\.place_order\(",
                r"route_order\s*\(",
                r"OrderExecutor\s*\(",
                r"KisClient\b",
            ]
            for pat in forbidden:
                assert not re.search(pat, src, re.MULTILINE), \
                    f"forbidden in {path.name}: {pat}"

    def test_no_external_http_or_ai_sdk(self):
        for path in (_LEDGER_PATH, _EVENTS_PATH):
            src = path.read_text(encoding="utf-8")
            forbidden = [
                r"^import\s+anthropic\b",
                r"^import\s+openai\b",
                r"^import\s+requests\b",
                r"^import\s+httpx\b",
                r"^from\s+anthropic\b",
                r"^from\s+openai\b",
                r"^from\s+httpx\b",
                r"^from\s+requests\b",
            ]
            for pat in forbidden:
                assert not re.search(pat, src, re.MULTILINE), \
                    f"forbidden http/AI in {path.name}: {pat}"

    def test_no_safety_flag_mutation(self):
        for path in (_LEDGER_PATH, _EVENTS_PATH):
            src = path.read_text(encoding="utf-8")
            bad = [
                r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
                r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
                r"settings\.enable_live_trading\s*=",
                r"settings\.enable_ai_execution\s*=",
            ]
            for pat in bad:
                assert not re.search(pat, src, re.IGNORECASE), \
                    f"safety flag mutation in {path.name}: {pat}"


class TestSchemaFieldLock:
    def test_event_has_required_13_fields_plus_invariants(self):
        required = {
            "timestamp", "loop_state", "strategy", "symbol",
            "decision_action", "confidence", "reason", "risk_flags",
            "paper_order_id", "paper_fill_status",
            "virtual_position_delta", "pnl_estimate",
            "is_order_signal", "auto_apply_allowed", "is_live_authorization",
        }
        actual = set(PaperLoopEvent.__dataclass_fields__.keys())
        missing = required - actual
        assert not missing, f"missing event fields: {missing}"

    def test_event_schema_has_no_secret_fields(self):
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
            "password", "private_key",
        ]
        actual = set(PaperLoopEvent.__dataclass_fields__.keys())
        for name in secret_names:
            assert name not in actual, f"schema has secret field: {name}"
