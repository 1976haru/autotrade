"""Operating Loop unit + integration tests (223, MUST).

deterministic stubs라 외부 LLM/시장 데이터 없이도 안정적으로 동작 — AI Key
미설정 환경에서도 mock output이 그대로 떨어지는지 회귀 잠금.
"""

from __future__ import annotations

from datetime import datetime

from app.agents.operating_loop import (
    OPERATING_STAGES,
    build_intraday_summary,
    build_post_market_review,
    build_pre_market_brief,
    current_stage,
    review_positions,
    watch_market_open,
)


# ---------- pre-market brief ----------

def test_pre_market_brief_blocked_when_emergency_stop_on() -> None:
    brief = build_pre_market_brief(
        daily_loss_cap=1_000_000,
        emergency_stop=True,
        enable_live_trading=False,
    )
    assert brief.readiness_label == "BLOCKED"
    assert brief.readiness_score == 0
    assert brief.trading_allowed is False
    assert any("긴급" in s for s in brief.operator_summary)


def test_pre_market_brief_caution_under_high_risk() -> None:
    brief = build_pre_market_brief(
        daily_loss_cap=500_000,
        emergency_stop=False,
        enable_live_trading=False,
        market_risk_level="HIGH",
    )
    assert brief.readiness_label == "CAUTION"
    assert brief.market_risk_level == "HIGH"
    assert brief.trading_allowed is True


def test_pre_market_brief_ready_under_low_risk() -> None:
    brief = build_pre_market_brief(
        daily_loss_cap=2_000_000,
        emergency_stop=False,
        enable_live_trading=True,
        market_risk_level="LOW",
    )
    assert brief.readiness_label == "READY"
    assert brief.readiness_score >= 80
    # enable_live_trading=True 일 때는 가산점 미차감.
    assert brief.readiness_score == 90


def test_pre_market_brief_default_themes_and_strategies() -> None:
    brief = build_pre_market_brief(
        daily_loss_cap=1_000_000,
        emergency_stop=False,
        enable_live_trading=False,
    )
    assert brief.interesting_themes
    assert "sma_crossover" in brief.available_strategies


# ---------- market open watch ----------

def test_market_open_watch_pause_on_high_volatility() -> None:
    obs = watch_market_open(volatility_pct=6.0, gap_up_symbols=["005930"])
    assert obs.market_action == "PAUSE"
    assert "005930" in obs.gap_up_symbols
    assert any("volatility" in r for r in obs.reasons)


def test_market_open_watch_normal_when_quiet() -> None:
    obs = watch_market_open()
    assert obs.market_action == "NORMAL"
    assert obs.gap_up_symbols == []
    assert obs.gap_down_symbols == []


def test_market_open_watch_watch_on_moderate_volatility() -> None:
    obs = watch_market_open(volatility_pct=3.0)
    assert obs.market_action == "WATCH"


# ---------- intraday summary ----------

def test_intraday_summary_empty_state_returns_safe_defaults() -> None:
    s = build_intraday_summary()
    assert s.candidates_evaluated == 0
    assert s.virtual_orders_made == 0
    assert s.last_chief_decision is None
    assert s.operator_summary  # non-empty


def test_intraday_summary_active_state_summarizes_counts() -> None:
    s = build_intraday_summary(
        candidates=12, virtual_orders=4, rejected=8,
        last_decision="BUY", last_reasons=["chief:entry_buy"],
    )
    assert s.candidates_evaluated == 12
    assert s.virtual_orders_made == 4
    assert s.rejected_signals == 8
    assert s.last_chief_decision == "BUY"
    assert any("12" in line or "4" in line for line in s.operator_summary)


# ---------- position monitor ----------

def test_position_monitor_take_profit_trigger() -> None:
    rows = review_positions([{"symbol": "005930", "unrealized_pct": 3.5}])
    assert rows[0].advice == "TAKE_PROFIT"


def test_position_monitor_stop_loss_trigger() -> None:
    rows = review_positions([{"symbol": "035420", "unrealized_pct": -2.5}])
    assert rows[0].advice == "STOP_LOSS"


def test_position_monitor_time_exit_after_4h() -> None:
    rows = review_positions([
        {"symbol": "000660", "unrealized_pct": 0.5, "holding_minutes": 250},
    ])
    assert rows[0].advice == "TIME_EXIT"


def test_position_monitor_hold_in_band() -> None:
    rows = review_positions([
        {"symbol": "001234", "unrealized_pct": 1.0, "holding_minutes": 60},
    ])
    assert rows[0].advice == "HOLD"


def test_position_monitor_empty_input_returns_empty() -> None:
    assert review_positions(None) == []
    assert review_positions([]) == []


# ---------- post-market review ----------

def test_post_market_review_no_data() -> None:
    r = build_post_market_review()
    assert r.total_decisions == 0
    assert r.agent_score_delta == 0
    assert r.operator_summary  # not empty


def test_post_market_review_score_delta_positive_on_wins() -> None:
    r = build_post_market_review(
        total_decisions=10, successes=8, failures=1, misclassified=1,
        pnl_estimate=50_000,
    )
    assert r.agent_score_delta > 0
    assert r.pnl_estimate == 50_000


def test_post_market_review_score_delta_negative_on_losses() -> None:
    r = build_post_market_review(total_decisions=10, successes=2, failures=8)
    assert r.agent_score_delta < 0


# ---------- stage clock ----------

def test_current_stage_at_premarket() -> None:
    s = current_stage(datetime(2026, 5, 7, 8, 30))
    assert s == "pre_market"


def test_current_stage_at_open_watch() -> None:
    assert current_stage(datetime(2026, 5, 7, 9, 15)) == "market_open_watch"


def test_current_stage_at_intraday() -> None:
    assert current_stage(datetime(2026, 5, 7, 12, 0)) == "intraday"


def test_current_stage_at_position_monitor() -> None:
    assert current_stage(datetime(2026, 5, 7, 15, 10)) == "position_monitor"


def test_current_stage_at_post_market() -> None:
    assert current_stage(datetime(2026, 5, 7, 16, 0)) == "post_market"


def test_operating_stages_constant_order() -> None:
    assert OPERATING_STAGES == [
        "pre_market", "market_open_watch", "intraday",
        "position_monitor", "post_market",
    ]


# ---------- API integration ----------

def test_api_operating_loop_status_returns_stage_and_stages(client) -> None:
    res = client.get("/api/agents/operating-loop/status")
    assert res.status_code == 200
    body = res.json()
    assert body["stage"] in OPERATING_STAGES
    assert body["stages"] == OPERATING_STAGES


def test_api_pre_market_brief_blocked_when_high_risk(client) -> None:
    res = client.post("/api/agents/pre-market-brief", json={
        "daily_loss_cap": 1_000_000,
        "market_risk_level": "HIGH",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["readiness_label"] == "CAUTION"
    assert body["market_risk_level"] == "HIGH"


def test_api_market_open_watch_pause(client) -> None:
    res = client.post("/api/agents/market-open-watch", json={
        "volatility_pct": 7.0,
        "gap_up_symbols": ["005930", "000660"],
    })
    assert res.status_code == 200
    body = res.json()
    assert body["market_action"] == "PAUSE"
    assert "005930" in body["gap_up_symbols"]


def test_api_intraday_summary_round_trip(client) -> None:
    res = client.post("/api/agents/intraday-summary", json={
        "candidates": 5, "virtual_orders": 2, "rejected": 3,
        "last_decision": "HOLD",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["candidates_evaluated"] == 5
    assert body["virtual_orders_made"] == 2
    assert body["last_chief_decision"] == "HOLD"


def test_api_position_monitor_round_trip(client) -> None:
    res = client.post("/api/agents/position-monitor", json={
        "positions": [
            {"symbol": "005930", "unrealized_pct": 3.5, "holding_minutes": 30},
            {"symbol": "035420", "unrealized_pct": -2.5, "holding_minutes": 30},
        ],
    })
    assert res.status_code == 200
    rows = res.json()
    assert rows[0]["advice"] == "TAKE_PROFIT"
    assert rows[1]["advice"] == "STOP_LOSS"


def test_api_post_market_review_round_trip(client) -> None:
    res = client.post("/api/agents/post-market-review", json={
        "total_decisions": 20, "successes": 14, "failures": 5, "misclassified": 1,
        "pnl_estimate": 80_000,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["total_decisions"] == 20
    assert body["agent_score_delta"] > 0
    assert body["pnl_estimate"] == 80_000
