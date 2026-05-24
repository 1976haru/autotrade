"""REAL-INTRADAY-TEST-01 — real_intraday_final_result 판정 로직 + endpoint 테스트."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system.real_intraday_final_result import (
    BLOCKED_BY_DATA,
    NOT_PROMISING_ON_CURRENT_DATA,
    PROMISING_FOR_PAPER_TEST,
    STRATEGY_NEEDS_TUNING,
    TOO_EARLY_TO_JUDGE,
    WORTH_MORE_RESEARCH,
    RealIntradayFinalResult,
    build_final_result,
    render_markdown,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]


def _idict(**over):
    base = {
        "symbols_count": 5, "pass_symbols": ["a", "b", "c", "d", "e"],
        "blocked_symbols": [], "total_bars": 20000, "total_trades": 500,
        "win_rate": 0.55, "profit_factor": 1.5, "expectancy": 200.0,
        "max_drawdown": -1000.0, "walk_forward_score": 60.0,
        "agent_value_summary": "AGENT_ADDS_VALUE", "agent_value_score": 70.0,
        "agent_helped_symbols": ["a", "b"], "agent_hurt_symbols": [],
        "agent_no_trade_symbols": [], "overall_verdict": "CAUTIOUS_CANDIDATE",
        "bar_size_minutes": 5.0,
        "per_symbol": [{"symbol": "a", "real_data_used": True, "sample_fixture_only": False}],
    }
    base.update(over)
    return base


def test_no_pass_symbols_blocked_by_data():
    r = build_final_result(_idict(pass_symbols=[], total_trades=0), actual_data_used=False)
    assert r.user_final_judgement == BLOCKED_BY_DATA


def test_synthetic_only_too_early():
    r = build_final_result(_idict(), actual_data_used=False)
    assert r.user_final_judgement == TOO_EARLY_TO_JUDGE


def test_zero_trades_needs_tuning():
    r = build_final_result(_idict(total_trades=0), actual_data_used=True)
    assert r.user_final_judgement == STRATEGY_NEEDS_TUNING


def test_few_trades_too_early():
    r = build_final_result(_idict(total_trades=10), actual_data_used=True)
    assert r.user_final_judgement == TOO_EARLY_TO_JUDGE


def test_under_100_trades_worth_more_research():
    r = build_final_result(_idict(total_trades=50), actual_data_used=True)
    assert r.user_final_judgement == WORTH_MORE_RESEARCH


def test_good_metrics_promising():
    r = build_final_result(_idict(
        total_trades=300, profit_factor=1.5, expectancy=200.0, walk_forward_score=60.0,
        agent_value_summary="AGENT_ADDS_VALUE"), actual_data_used=True)
    assert r.user_final_judgement == PROMISING_FOR_PAPER_TEST
    assert r.paper_rehearsal_recommended is True


def test_low_walk_forward_not_promising_demoted():
    """WF 낮으면 PROMISING 금지 → WORTH_MORE_RESEARCH."""
    r = build_final_result(_idict(
        total_trades=300, profit_factor=1.5, expectancy=200.0, walk_forward_score=10.0),
        actual_data_used=True)
    assert r.user_final_judgement != PROMISING_FOR_PAPER_TEST
    assert r.paper_rehearsal_recommended is False


def test_pf_below_threshold_not_promising():
    r = build_final_result(_idict(
        total_trades=300, profit_factor=1.05, expectancy=50.0, walk_forward_score=60.0),
        actual_data_used=True)
    assert r.user_final_judgement != PROMISING_FOR_PAPER_TEST


def test_negative_performance_not_promising_on_current_data():
    r = build_final_result(_idict(
        total_trades=300, profit_factor=0.8, expectancy=-50.0, walk_forward_score=5.0),
        actual_data_used=True)
    assert r.user_final_judgement == NOT_PROMISING_ON_CURRENT_DATA


def test_few_symbols_capped_too_early():
    r = build_final_result(_idict(
        symbols_count=2, pass_symbols=["a", "b"], total_trades=300,
        profit_factor=1.5, expectancy=200.0, walk_forward_score=60.0),
        actual_data_used=True)
    # 종목 < 3 → 최대 TOO_EARLY_TO_JUDGE.
    assert r.user_final_judgement == TOO_EARLY_TO_JUDGE


def test_invariants_and_one_liner():
    r = build_final_result(_idict(), actual_data_used=True)
    assert r.do_not_auto_apply is True
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    assert r.no_profit_guarantee is True
    assert r.one_line_conclusion  # 비어있지 않음


def test_guard_rejects_unsafe():
    base = dict(
        generated_at="x", actual_data_used=True, data_source="s", bar_size_minutes=5.0,
        symbols_count=1, pass_symbols=(), blocked_symbols=(), total_bars=1, total_trades=1,
        median_win_rate=None, median_profit_factor=None, median_expectancy=None,
        median_mdd=None, median_walk_forward_score=None, agent_value_summary="x",
        agent_value_score=None, agent_helped_symbols=(), agent_hurt_symbols=(),
        agent_no_trade_symbols=(), developer_verdict="RESEARCH_ONLY",
        user_final_judgement=WORTH_MORE_RESEARCH, one_line_conclusion="x",
        paper_rehearsal_recommended=False)
    for bad in ({"is_live_authorization": True}, {"do_not_auto_apply": False},
                {"contains_secret": True}, {"no_profit_guarantee": False},
                {"user_final_judgement": "MOON"}):
        kw = dict(base)
        kw.update(bad)
        with pytest.raises(ValueError):
            RealIntradayFinalResult(**kw)


def test_markdown_safe():
    md = render_markdown(build_final_result(_idict(), actual_data_used=True))
    assert "한 줄 결론" in md
    assert "실전 승인 아님" in md and "수익 보장 아님" in md
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", md)


def test_to_dict_keys():
    d = to_dict(build_final_result(_idict(), actual_data_used=True))
    for k in ("user_final_judgement", "developer_verdict", "actual_data_used",
              "one_line_conclusion", "paper_rehearsal_recommended", "do_not_auto_apply",
              "is_live_authorization", "no_profit_guarantee"):
        assert k in d


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "system" / "real_intraday_final_result.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src


# --------------------------- endpoint --------------------------------------

def test_final_result_endpoint(client, safe_default_flags):
    r = client.get("/api/system/real-intraday-final-result/latest")
    assert r.status_code == 200, r.text
    d = r.json()
    assert "user_final_judgement" in d
    assert d["is_live_authorization"] is False
    assert d["do_not_auto_apply"] is True
    assert d["no_profit_guarantee"] is True
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_final_result_endpoint_get_only(client, safe_default_flags):
    assert client.post(
        "/api/system/real-intraday-final-result/latest", json={}).status_code in (404, 405)
