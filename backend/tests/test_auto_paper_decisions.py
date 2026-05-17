"""#2-10: AI Paper 자동매수/매도 skeleton 테스트.

invariant:
- `PaperDecision` + `AIRecommendationInput` 둘 다 is_order_signal=False /
  auto_apply_allowed=False / is_live_authorization=False (`__post_init__`).
- 변환 규칙 lock:
  - BUY (pos=0) → BUY + PAPER_FILLED + virtual_delta=+size
  - BUY (pos>0) → HOLD (중복 매수 회피)
  - SELL (pos=0) → HOLD (보유 없음)
  - SELL (pos>0) → SELL + PAPER_FILLED + virtual_delta=-size
  - EXIT (pos>0) → EXIT + virtual_delta=-pos (전량)
  - EXIT (pos=0) → NO_OP
  - HOLD → HOLD
  - NO_OP → NO_OP
- ledger 기록 — `RUNNING` 만 trade event 허용, HOLD/NO_OP 모든 state.
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- secret / API key 필드 0건.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.auto_paper.events import DecisionAction, PaperFillStatus
from app.auto_paper.decisions import (
    DEFAULT_VIRTUAL_TRADE_SIZE,
    AIRecommendationInput,
    PaperDecision,
    convert_to_paper_decision,
    process_ai_recommendation,
)
from app.auto_paper.ledger import (
    LedgerStateError,
    get_ledger,
    reset_ledger_for_tests,
)


# ─────────────────────────────────────────────────────────────────────────────
# fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_ledger():
    reset_ledger_for_tests()
    yield
    reset_ledger_for_tests()


def _rec(direction="HOLD", current_position=0, **kw):
    return AIRecommendationInput(
        strategy=kw.get("strategy", "sma_crossover"),
        symbol=kw.get("symbol", "005930"),
        direction=direction,
        reason=kw.get("reason", "test"),
        confidence=kw.get("confidence", 0.65),
        current_position=current_position,
        risk_flags=kw.get("risk_flags", []),
        metadata=kw.get("metadata", {}),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. AIRecommendationInput invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestAIRecommendationInputInvariants:
    def test_is_order_signal_must_be_false(self):
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="HOLD", reason="r",
                is_order_signal=True,
            )

    def test_auto_apply_allowed_must_be_false(self):
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="HOLD", reason="r",
                auto_apply_allowed=True,
            )

    def test_is_live_authorization_must_be_false(self):
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="HOLD", reason="r",
                is_live_authorization=True,
            )

    def test_invalid_direction_rejected(self):
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="PURCHASE", reason="r",
            )

    @pytest.mark.parametrize("d", ["BUY", "SELL", "EXIT", "HOLD", "NO_OP"])
    def test_all_valid_directions_accepted(self, d):
        rec = AIRecommendationInput(
            strategy="s", symbol="x", direction=d, reason="r",
        )
        assert rec.direction == d

    def test_confidence_range_check(self):
        AIRecommendationInput(
            strategy="s", symbol="x", direction="HOLD", reason="r",
            confidence=0.5,
        )
        AIRecommendationInput(
            strategy="s", symbol="x", direction="HOLD", reason="r",
            confidence=None,
        )
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="HOLD", reason="r",
                confidence=1.5,
            )
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="x", direction="HOLD", reason="r",
                confidence=-0.1,
            )

    def test_empty_strategy_or_symbol_rejected(self):
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="", symbol="x", direction="HOLD", reason="r",
            )
        with pytest.raises(ValueError):
            AIRecommendationInput(
                strategy="s", symbol="", direction="HOLD", reason="r",
            )


# ─────────────────────────────────────────────────────────────────────────────
# 2. PaperDecision invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperDecisionInvariants:
    def test_invariants_immutable(self):
        for kwargs in (
            {"is_order_signal": True},
            {"auto_apply_allowed": True},
            {"is_live_authorization": True},
        ):
            with pytest.raises(ValueError):
                PaperDecision(
                    strategy="s", symbol="x",
                    action=DecisionAction.HOLD,
                    confidence=None, reason="r",
                    **kwargs,
                )

    def test_to_dict_carries_invariants(self):
        d = convert_to_paper_decision(_rec(direction="HOLD")).to_dict()
        assert d["is_order_signal"]       is False
        assert d["auto_apply_allowed"]    is False
        assert d["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. 변환 규칙 매트릭스 (8 케이스 — docstring 표와 1:1 lock)
# ─────────────────────────────────────────────────────────────────────────────


class TestConvertRules:
    def test_buy_zero_position_creates_buy_filled(self):
        d = convert_to_paper_decision(_rec(direction="BUY", current_position=0))
        assert d.action == DecisionAction.BUY
        assert d.paper_fill_status == PaperFillStatus.PAPER_FILLED
        assert d.virtual_position_delta == DEFAULT_VIRTUAL_TRADE_SIZE
        assert d.paper_order_id is not None
        assert d.source_direction == "BUY"

    def test_buy_existing_position_demotes_to_hold(self):
        """user spec: 이미 보유 중인데 BUY 시도 → 중복 매수 회피."""
        d = convert_to_paper_decision(_rec(direction="BUY", current_position=5))
        assert d.action == DecisionAction.HOLD
        assert d.paper_fill_status == PaperFillStatus.NA
        assert d.virtual_position_delta == 0
        assert "BUY suppressed" in d.reason
        assert d.source_direction == "BUY"   # 원입력 보존.

    def test_sell_zero_position_demotes_to_hold(self):
        """보유 없음 → 매도 불가 → HOLD audit."""
        d = convert_to_paper_decision(_rec(direction="SELL", current_position=0))
        assert d.action == DecisionAction.HOLD
        assert d.paper_fill_status == PaperFillStatus.NA
        assert d.virtual_position_delta == 0
        assert "SELL suppressed" in d.reason

    def test_sell_existing_position_creates_sell_filled(self):
        d = convert_to_paper_decision(_rec(direction="SELL", current_position=10))
        assert d.action == DecisionAction.SELL
        assert d.paper_fill_status == PaperFillStatus.PAPER_FILLED
        # default size=1, pos=10 → delta = -min(1, 10) = -1.
        assert d.virtual_position_delta == -DEFAULT_VIRTUAL_TRADE_SIZE
        assert d.paper_order_id is not None

    def test_sell_size_clamped_to_position(self):
        """size > pos → delta = -pos (overshoot 방지)."""
        d = convert_to_paper_decision(
            _rec(direction="SELL", current_position=3),
            virtual_trade_size=10,
        )
        assert d.virtual_position_delta == -3

    def test_exit_with_position_creates_exit_full_close(self):
        d = convert_to_paper_decision(_rec(direction="EXIT", current_position=7))
        assert d.action == DecisionAction.EXIT
        assert d.virtual_position_delta == -7   # 전량.
        assert d.paper_fill_status == PaperFillStatus.PAPER_FILLED

    def test_exit_zero_position_marks_no_op(self):
        d = convert_to_paper_decision(_rec(direction="EXIT", current_position=0))
        assert d.action == DecisionAction.NO_OP
        assert d.virtual_position_delta == 0
        assert "EXIT suppressed" in d.reason

    def test_hold_always_maps_to_hold(self):
        for pos in (0, 5):
            d = convert_to_paper_decision(_rec(direction="HOLD", current_position=pos))
            assert d.action == DecisionAction.HOLD
            assert d.virtual_position_delta == 0
            assert d.paper_order_id is None

    def test_no_op_always_maps_to_no_op(self):
        d = convert_to_paper_decision(_rec(direction="NO_OP", current_position=0))
        assert d.action == DecisionAction.NO_OP
        assert d.virtual_position_delta == 0

    def test_auto_fill_false_produces_pending(self):
        d = convert_to_paper_decision(
            _rec(direction="BUY", current_position=0),
            auto_fill=False,
        )
        assert d.paper_fill_status == PaperFillStatus.PAPER_PENDING

    def test_paper_order_id_uniqueness(self):
        ids = set()
        for _ in range(15):
            d = convert_to_paper_decision(_rec(direction="BUY", current_position=0))
            ids.add(d.paper_order_id)
        assert len(ids) == 15


# ─────────────────────────────────────────────────────────────────────────────
# 4. process_ai_recommendation — ledger 기록 통합
# ─────────────────────────────────────────────────────────────────────────────


class TestProcessAIRecommendation:
    def test_running_buy_records_event(self):
        decision, event = process_ai_recommendation(
            _rec(direction="BUY", current_position=0),
            loop_state="RUNNING",
        )
        assert decision.action == DecisionAction.BUY
        assert event is not None
        assert event.decision_action == DecisionAction.BUY
        assert event.loop_state == "RUNNING"
        # ledger 에 1건 추가.
        assert len(get_ledger()) == 1

    def test_running_hold_records_audit(self):
        """HOLD 도 판단 로그로 기록."""
        decision, event = process_ai_recommendation(
            _rec(direction="HOLD"), loop_state="RUNNING",
        )
        assert decision.action == DecisionAction.HOLD
        assert event is not None
        assert len(get_ledger()) == 1

    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "STOPPED", "MARKET_CLOSED", "EMERGENCY_STOP",
    ])
    def test_non_running_state_blocks_trade(self, state):
        """RUNNING 아닌 state 에서 BUY 시도 → LedgerStateError."""
        with pytest.raises(LedgerStateError):
            process_ai_recommendation(
                _rec(direction="BUY", current_position=0),
                loop_state=state,
            )
        # ledger 에 0건.
        assert len(get_ledger()) == 0

    @pytest.mark.parametrize("state", [
        "PAUSED", "WAITING_MARKET", "STOPPED", "MARKET_CLOSED", "EMERGENCY_STOP",
        "RUNNING",
    ])
    def test_hold_allowed_in_any_state(self, state):
        """HOLD 는 모든 state 에서 판단 로그 기록 가능."""
        decision, event = process_ai_recommendation(
            _rec(direction="HOLD"), loop_state=state,
        )
        assert decision.action == DecisionAction.HOLD
        assert event is not None
        assert len(get_ledger()) == 1

    def test_demoted_buy_in_non_running_records_as_hold(self):
        """이미 보유 중 (current_position>0) 이면 BUY 입력 → HOLD 변환 →
        non-RUNNING state 에서도 기록 허용 (HOLD 는 state 제한 없음)."""
        decision, event = process_ai_recommendation(
            _rec(direction="BUY", current_position=5),
            loop_state="PAUSED",
        )
        assert decision.action == DecisionAction.HOLD
        assert event is not None
        assert event.loop_state == "PAUSED"

    def test_record_false_does_not_touch_ledger(self):
        decision, event = process_ai_recommendation(
            _rec(direction="BUY", current_position=0),
            loop_state="RUNNING",
            record=False,
        )
        assert decision.action == DecisionAction.BUY
        assert event is None
        assert len(get_ledger()) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 5. API — POST /tick + GET /decision/latest
# ─────────────────────────────────────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def _force_market_open(monkeypatch):
    """test 격리: `current_market_phase` 를 OPEN 으로 강제 — KST 09:00-15:30 외에
    실행해도 RUNNING state 가 lazy-demote 되지 않도록.

    Returns the loop in RUNNING state.
    """
    from app.auto_paper import loop as loop_mod
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        loop_mod, "current_market_phase", lambda *a, **kw: MarketPhase.OPEN,
    )
    from app.auto_paper.loop import get_auto_paper_loop, AutoPaperState
    loop = get_auto_paper_loop()
    loop._state = AutoPaperState.RUNNING   # type: ignore[attr-defined]
    return loop


@pytest.fixture
def _force_market_paused(monkeypatch):
    """test 격리: loop._state = PAUSED + market_phase OPEN 으로 강제 — non-RUNNING
    state 에서 BUY 시도 차단을 검증."""
    from app.auto_paper import loop as loop_mod
    from app.scheduler.market_clock import MarketPhase
    monkeypatch.setattr(
        loop_mod, "current_market_phase", lambda *a, **kw: MarketPhase.OPEN,
    )
    from app.auto_paper.loop import get_auto_paper_loop, AutoPaperState
    loop = get_auto_paper_loop()
    loop._state = AutoPaperState.PAUSED   # type: ignore[attr-defined]
    return loop


class TestTickAPI:
    def test_empty_recommendations_returns_zero(self):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={"recommendations": []})
        assert r.status_code == 200
        body = r.json()
        assert body["decision_count"] == 0
        assert body["is_order_signal"]       is False
        assert body["auto_apply_allowed"]    is False
        assert body["is_live_authorization"] is False

    def test_tick_with_buy_in_running_state(self, _force_market_open):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={
            "recommendations": [
                {"strategy": "sma_crossover", "symbol": "005930",
                 "direction": "BUY", "reason": "MA cross",
                 "confidence": 0.78, "current_position": 0},
            ],
            "virtual_trade_size": 1,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["decision_count"] == 1
        assert body["decisions"][0]["action"] == "BUY"
        assert body["decisions"][0]["paper_fill_status"] == "PAPER_FILLED"

    def test_tick_buy_in_non_running_errored(self, _force_market_paused):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={
            "recommendations": [
                {"strategy": "s", "symbol": "x",
                 "direction": "BUY", "reason": "r",
                 "current_position": 0},
            ],
        })
        assert r.status_code == 200   # endpoint 자체는 200 — per-rec error 로 carry.
        body = r.json()
        assert body["decision_count"] == 0
        assert body["error_count"] == 1
        assert "LedgerStateError" in body["errors"][0]["error"]

    def test_tick_invalid_direction_returns_error_in_list(self):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={
            "recommendations": [
                {"strategy": "s", "symbol": "x",
                 "direction": "PURCHASE", "reason": "r"},
            ],
        })
        body = r.json()
        assert body["error_count"] == 1
        assert "invalid_input" in body["errors"][0]["error"]

    def test_latest_decision_empty_returns_none(self):
        client = _client()
        r = client.get("/api/auto-paper/decision/latest")
        assert r.status_code == 200
        body = r.json()
        assert body["has_decision"] is False
        assert body["decision"] is None
        assert body["is_order_signal"]       is False
        assert body["auto_apply_allowed"]    is False
        assert body["is_live_authorization"] is False

    def test_latest_decision_after_tick(self, _force_market_open):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={
            "recommendations": [
                {"strategy": "rsi_reversion", "symbol": "000660",
                 "direction": "BUY", "reason": "RSI oversold",
                 "confidence": 0.7, "current_position": 0},
            ],
        })
        assert r.status_code == 200
        r2 = client.get("/api/auto-paper/decision/latest")
        body = r2.json()
        assert body["has_decision"] is True
        assert body["decision"]["decision_action"] == "BUY"
        assert body["decision"]["strategy"] == "rsi_reversion"

    def test_tick_response_no_secret_fields(self):
        client = _client()
        r = client.post("/api/auto-paper/tick", json={"recommendations": []})
        text = r.text.lower()
        for f in [
            "anthropic_api_key", "openai_api_key", "kis_app_key",
            "kis_app_secret", "account_no",
        ]:
            assert f not in text


# ─────────────────────────────────────────────────────────────────────────────
# 6. Static guards — forbidden imports
# ─────────────────────────────────────────────────────────────────────────────


_MOD = (
    Path(__file__).resolve().parents[1] / "app" / "auto_paper" / "decisions.py"
)


class TestNoForbiddenImports:
    def test_no_broker_or_executor_imports(self):
        src = _MOD.read_text(encoding="utf-8")
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
                f"forbidden in decisions.py: {pat}"

    def test_no_external_http_or_ai_sdk(self):
        src = _MOD.read_text(encoding="utf-8")
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
                f"forbidden http/AI in decisions.py: {pat}"

    def test_no_safety_flag_mutation(self):
        src = _MOD.read_text(encoding="utf-8")
        bad = [
            r"ENABLE_LIVE_TRADING\s*=\s*['\"]?true",
            r"ENABLE_AI_EXECUTION\s*=\s*['\"]?true",
            r"settings\.enable_live_trading\s*=",
            r"settings\.enable_ai_execution\s*=",
        ]
        for pat in bad:
            assert not re.search(pat, src, re.IGNORECASE), \
                f"safety flag mutation in decisions.py: {pat}"

    def test_schema_has_no_secret_fields(self):
        in_fields = AIRecommendationInput.__dataclass_fields__.keys()
        out_fields = PaperDecision.__dataclass_fields__.keys()
        secret_names = [
            "api_key", "secret", "app_key", "app_secret", "access_token",
            "account_no", "account_number", "anthropic_api_key",
            "openai_api_key", "kis_app_key", "kis_app_secret",
        ]
        for n in secret_names:
            assert n not in in_fields,  f"input has secret field: {n}"
            assert n not in out_fields, f"output has secret field: {n}"
