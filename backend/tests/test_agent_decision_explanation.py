"""#52 / 6-07 + #49 / 6-04: AI 판단 설명 + 주문 품질 집계 테스트.

핵심 invariant:
- entry_reason / counter_reason / exit_plan / risk_flags / risk_veto /
  exit_plan_validation / sell_reason 설명 생성.
- 구버전/누락 episode 에서 fallback 문구 (에러 0).
- order_failure_rate / rejected_rate / partial_fill_rate / blocked_reasons_top 계산.
- contains_secret=false / is_order_signal=false / is_live_authorization=false.
- broker/OrderExecutor/route_order import·호출 0건, DB write 0건, secret 노출 0건.
"""

from __future__ import annotations

import json

import pytest

from app.agents import decision_explanation as de
from app.agents import order_quality_metrics as oqm
from app.agents.decision_explanation import (
    COUNTER_REASON_FALLBACK,
    ENTRY_REASON_FALLBACK,
    EXIT_PLAN_FALLBACK,
    RISK_FLAGS_FALLBACK,
    RISK_VETO_FALLBACK,
    build_decision_explanation,
)
from app.agents.order_quality_metrics import aggregate_order_quality


def _buy_council():
    return {
        "final_action": "BUY", "confidence": 0.72, "quality_score": 78,
        "selected_strategies": ["MOMENTUM", "VWAP"], "market_regime": "TREND_UP",
        "reason": "BUY 채택 (conf=0.72)", "risk_flags": ["high_volatility"],
        "has_exit_plan": True,
        "exit_plan": {"stop_loss_pct": 2.0, "take_profit_pct": 3.0, "risk_reward_ratio": 1.5},
        "risk_veto_result": {}, "exit_plan_validation": {"valid": True}, "sell_reason": {},
        "votes": [
            {"strategy": "MOMENTUM", "signal": "BUY", "score": 80, "reason": "상승 모멘텀 +2%"},
            {"strategy": "VWAP", "signal": "BUY", "score": 70, "reason": "VWAP 상회"},
            {"strategy": "GAP", "signal": "SELL", "score": 40, "reason": "과열 위험"},
            {"strategy": "ORB", "signal": "HOLD", "score": 30, "reason": "range 내"},
        ],
    }


# ── 1~6. 설명 생성 ───────────────────────────────────────────────────────────


def test_entry_reason_from_supporting_votes():
    e = build_decision_explanation(_buy_council())
    assert "MOMENTUM" in e.entry_reason and "VWAP" in e.entry_reason
    assert "매수" in e.entry_reason


def test_counter_reason_from_opposing_votes():
    e = build_decision_explanation(_buy_council())
    assert "GAP" in e.counter_reason
    assert "과열" in e.counter_reason


def test_risk_flags_text():
    e = build_decision_explanation(_buy_council())
    assert "high_volatility" in e.risk_flags_text


def test_exit_plan_text():
    e = build_decision_explanation(_buy_council())
    assert "손절 2.0%" in e.exit_plan_text
    assert "익절 3.0%" in e.exit_plan_text
    assert "RR 1.5" in e.exit_plan_text


def test_sell_reason_text():
    c = {"final_action": "SELL", "votes": [{"strategy": "VWAP", "signal": "SELL", "score": 60}],
         "sell_reason": {"reason_code": "STOP_LOSS", "message": "손절가 도달"}, "held_position": True}
    e = build_decision_explanation(c)
    assert "STOP_LOSS" in e.sell_reason_text
    assert "손절가 도달" in e.sell_reason_text


def test_risk_veto_text():
    c = {"final_action": "HOLD",
         "risk_veto_result": {"veto_applied": True, "reason_code": "RISK_FLAGS_EXCEEDED"},
         "votes": []}
    e = build_decision_explanation(c)
    assert "RiskOfficer veto" in e.risk_veto_text
    assert "RISK_FLAGS_EXCEEDED" in e.risk_veto_text


# ── 7. exit_plan_validation 실패 설명 ────────────────────────────────────────


def test_exit_plan_validation_failure():
    c = {"final_action": "HOLD", "pre_exit_plan_action": "BUY",
         "exit_plan_validation": {"valid": False, "reason_code": "MISSING_STOP_LOSS"},
         "votes": [{"strategy": "MOMENTUM", "signal": "BUY", "score": 80}]}
    e = build_decision_explanation(c)
    assert "BUY 차단" in e.exit_plan_validation_text
    assert "MISSING_STOP_LOSS" in e.exit_plan_validation_text


# ── 8. fallback (구버전/누락) ────────────────────────────────────────────────


def test_fallbacks_on_empty_council():
    e = build_decision_explanation(None)
    assert e.exit_plan_text == EXIT_PLAN_FALLBACK
    assert e.risk_flags_text == RISK_FLAGS_FALLBACK
    assert e.risk_veto_text == RISK_VETO_FALLBACK
    assert e.counter_reason == COUNTER_REASON_FALLBACK


def test_entry_reason_fallback_when_buy_no_supporting():
    c = {"final_action": "BUY", "votes": [{"strategy": "GAP", "signal": "SELL", "score": 50}]}
    e = build_decision_explanation(c)
    assert e.entry_reason == ENTRY_REASON_FALLBACK


def test_legacy_episode_missing_fields_no_error():
    # 구버전 episode: council 에 votes/exit_plan 없음 → 에러 없이 fallback.
    c = {"final_action": "HOLD"}
    e = build_decision_explanation(c)
    assert e.exit_plan_text == EXIT_PLAN_FALLBACK
    d = e.to_dict()
    assert d["final_action"] == "HOLD"


# ── 9~12. 안전 invariant + secret ────────────────────────────────────────────


def test_explanation_invariants():
    e = build_decision_explanation(_buy_council())
    assert e.is_order_signal is False
    assert e.is_live_authorization is False
    assert e.contains_secret is False
    d = e.to_dict()
    assert d["is_order_signal"] is False
    assert d["is_live_authorization"] is False
    assert d["contains_secret"] is False


def test_explanation_invariant_enforced():
    from app.agents.decision_explanation import DecisionExplanation
    with pytest.raises(ValueError):
        DecisionExplanation(
            final_action="BUY", final_action_ko="매수", final_reason="r",
            entry_reason="e", counter_reason="c", exit_plan_text="x",
            risk_flags_text="f", risk_veto_text="v", exit_plan_validation_text="",
            sell_reason_text="", selected_strategies=[], quality_score=70,
            confidence=0.6, market_regime="TREND_UP", time_phase="MORNING",
            explanation_summary="s", is_live_authorization=True)


def test_no_secret_in_explanation():
    text = json.dumps(build_decision_explanation(_buy_council()).to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in text


# ── 13~14. import / 호출 가드 ────────────────────────────────────────────────


@pytest.mark.parametrize("mod", [de, oqm])
def test_no_forbidden_imports(mod):
    src = open(mod.__file__, encoding="utf-8").read()
    forbidden = (
        "from app.brokers.kis", "from app.brokers.mock_broker",
        "from app.execution.order_router", "from app.execution.executor",
        "from app.execution.order_executor", "from app.execution.paper_trader",
        "import anthropic", "import openai", "import httpx", "import requests",
        ".place_order(", ".cancel_order(", "route_order(", "db.add(", "db.commit(",
    )
    for tok in forbidden:
        assert tok not in src, f"forbidden token in {mod.__name__}: {tok}"


# ── 16~17. order quality 집계 ────────────────────────────────────────────────


def _episodes():
    return [
        {"final_action": "BUY", "order_quality_summary": {"order_status": "FILLED", "slippage_bps": 12.0}},
        {"final_action": "BUY", "order_quality_summary": {"order_status": "PARTIALLY_FILLED",
                                                          "slippage_bps": 30.0, "partial_fill": True}},
        {"final_action": "BUY", "order_quality_summary": {"order_status": "REJECTED"},
         "kis_order_result": {"order_quality": {"rejection_reason_code": "INSUFFICIENT_CASH"}}},
        {"final_action": "HOLD", "reason_code": "STALE_PRICE",
         "council": {"risk_veto_result": {"veto_applied": True, "reason_code": "RISK_FLAGS_EXCEEDED"}}},
    ]


def test_order_failure_and_rates():
    m = aggregate_order_quality(_episodes())
    assert m.order_count == 3            # FILLED + PARTIAL + REJECTED.
    assert m.filled_count == 1
    assert m.partial_fill_count == 1
    assert m.rejected_count == 1
    assert m.order_failure_rate == pytest.approx(1 / 3, abs=1e-3)
    assert m.rejected_rate == pytest.approx(1 / 3, abs=1e-3)
    assert m.partial_fill_rate == pytest.approx(1 / 3, abs=1e-3)
    assert m.avg_slippage_bps == pytest.approx(21.0)


def test_blocked_reasons_top():
    m = aggregate_order_quality(_episodes())
    reasons = {b["reason"] for b in m.blocked_reasons_top}
    assert "INSUFFICIENT_CASH" in reasons   # 거절 사유.
    assert "RISK_FLAGS_EXCEEDED" in reasons  # veto 사유.


def test_order_quality_insufficient_data():
    m = aggregate_order_quality([])
    assert m.status == "INSUFFICIENT_DATA"
    assert m.order_count == 0
    assert m.order_failure_rate is None


def test_order_quality_invariants():
    m = aggregate_order_quality(_episodes()).to_dict()
    assert m["is_order_signal"] is False
    assert m["is_live_authorization"] is False
    assert m["contains_secret"] is False


# ── API endpoints ────────────────────────────────────────────────────────────


def test_api_decision_explanation(client):
    r = client.post("/api/agents/decision-explanation", json={"council": _buy_council()})
    assert r.status_code == 200
    body = r.json()
    assert "MOMENTUM" in body["entry_reason"]
    assert body["is_live_authorization"] is False
    assert body["is_order_signal"] is False


def test_api_decision_explanation_empty(client):
    r = client.post("/api/agents/decision-explanation", json={})
    assert r.status_code == 200
    assert r.json()["exit_plan_text"] == EXIT_PLAN_FALLBACK


def test_api_order_quality_metrics(client):
    r = client.get("/api/agents/order-quality-metrics")
    assert r.status_code == 200
    body = r.json()
    for k in ("order_failure_rate", "rejected_rate", "partial_fill_rate",
              "blocked_reasons_top", "status"):
        assert k in body
    assert body["is_live_authorization"] is False


def test_api_no_broker_order(client):
    client.post("/api/agents/decision-explanation", json={"council": _buy_council()})
    client.get("/api/agents/order-quality-metrics")
    assert len(client.test_broker.orders) == 0


def test_api_no_secret_in_response(client):
    t = json.dumps(client.get("/api/agents/order-quality-metrics").json(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "access_token="):
        assert forbidden not in t
