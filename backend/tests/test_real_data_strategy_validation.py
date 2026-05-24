"""REAL-DATA-STRATEGY-01 — real_data_strategy 오케스트레이터 + endpoint 테스트."""

from __future__ import annotations

import re
from pathlib import Path

from app.market_data.real_ohlcv_loader import load_from_csv
from app.system import strategy_potential as sp
from app.system.real_data_strategy import (
    RealDataStrategyReport,
    evaluate_real_data_strategy,
    render_markdown,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]
_DEMO = _REPO / "backend" / "tests" / "fixtures" / "real_data" / "demo_quasi_real.csv"
_BROKEN = _REPO / "backend" / "tests" / "fixtures" / "real_data" / "005930.csv"
_SAMPLE = _REPO / "backend" / "tests" / "fixtures" / "backtest" / "sample_ohlcv.csv"

_VERDICTS = {sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED}


def _eval(path):
    return evaluate_real_data_strategy(load_from_csv(path))


def test_demo_real_data_runs_full_pipeline():
    r = _eval(_DEMO)
    assert r.real_data_used is True
    assert r.sample_fixture_only is False
    assert r.overall_verdict in _VERDICTS
    assert r.quality["status"] in ("OK", "WARN")
    assert r.bars_count >= 100


def test_broken_ohlc_real_data_is_blocked():
    r = _eval(_BROKEN)
    assert r.quality["status"] == "FAIL"
    assert r.overall_verdict == sp.BLOCKED


def test_sample_fixture_capped_at_research_only():
    r = _eval(_SAMPLE)
    assert r.sample_fixture_only is True
    # sample fixture 는 STRONG/CAUTIOUS 불가 — 최대 RESEARCH_ONLY.
    assert r.overall_verdict in (sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED)
    assert r.overall_verdict not in (sp.STRONG, sp.CAUTIOUS)


def test_real_data_few_trades_not_strong():
    """실데이터지만 paper 0 + 거래 부족 → STRONG 불가."""
    r = _eval(_DEMO)
    assert r.overall_verdict != sp.STRONG
    assert r.paper_sample_class == "PAPER_NO_TRADES_YET"


def test_agent_comparison_carried():
    r = _eval(_DEMO)
    assert r.agent_value_verdict in (
        "AGENT_ADDS_VALUE", "AGENT_RISK_REDUCTION_VALUE",
        "AGENT_UNDERPERFORMS", "AGENT_VALUE_INSUFFICIENT_SAMPLE")


def test_market_regime_and_time_phase_fields_present():
    r = _eval(_DEMO)
    # 데이터에 따라 비어 있을 수 있으나 필드는 tuple 로 존재.
    assert isinstance(r.favorable_conditions, tuple)
    assert isinstance(r.dangerous_conditions, tuple)


def test_report_invariants():
    r = _eval(_DEMO)
    assert r.do_not_auto_apply is True
    assert r.auto_apply_allowed is False
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    assert r.kis_historical_available is False


def test_report_guard_rejects_unsafe():
    import pytest
    base = dict(
        generated_at="x", data_source="CSV_USER", real_data_used=True,
        sample_fixture_only=False, symbols_count=1, bars_count=100, days_count=30,
        trades_count=10, quality={}, overall_verdict=sp.RESEARCH_ONLY,
        overall_score=10.0, backtest_score=None, walk_forward_score=None,
        stress_score=None, agent_value_score=None, data_sufficiency_score=None,
        agent_value_verdict="x", paper_sample_class="PAPER_NO_TRADES_YET")
    for bad in ({"is_live_authorization": True}, {"auto_apply_allowed": True},
                {"do_not_auto_apply": False}, {"contains_secret": True},
                {"kis_historical_available": True}):
        kw = dict(base)
        kw.update(bad)
        with pytest.raises(ValueError):
            RealDataStrategyReport(**kw)


def test_to_dict_and_markdown_safe():
    r = _eval(_DEMO)
    d = to_dict(r)
    for k in ("data_source", "real_data_used", "overall_verdict", "quality",
              "agent_value_verdict", "do_not_auto_apply", "is_live_authorization"):
        assert k in d
    md = render_markdown(r)
    assert "자동 적용 아님" in md and "실전 승인 아님" in md and "수익 보장 아님" in md
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", md)


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "system" / "real_data_strategy.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src, f"forbidden call: {call}"


# --------------------------- endpoint --------------------------------------

def test_endpoint_latest(client, safe_default_flags):
    r = client.get("/api/system/real-data-strategy-validation/latest")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["overall_verdict"] in _VERDICTS
    assert "real_data_used" in data
    assert data["do_not_auto_apply"] is True
    assert data["is_live_authorization"] is False
    assert data["is_order_signal"] is False
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)
    assert not re.search(r"\b\d{8}-\d{2}\b", r.text)


def test_endpoint_is_get_only(client, safe_default_flags):
    assert client.post(
        "/api/system/real-data-strategy-validation/latest", json={}).status_code in (404, 405)
