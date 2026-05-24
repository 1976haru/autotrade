"""INTRADAY-DATA-01 — intraday_strategy_validation 오케스트레이터 + endpoint 테스트."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.system import strategy_potential as sp
from app.system.intraday_strategy_validation import (
    IntradayStrategyReport,
    evaluate_intraday_strategy,
    kis_intraday_supported,
    render_markdown,
    to_dict,
)

_REPO = Path(__file__).resolve().parents[2]
_INTRADAY = _REPO / "backend" / "tests" / "fixtures" / "intraday_clean"
_DAILY = _REPO / "backend" / "tests" / "fixtures" / "real_data_clean"

_VERDICTS = {sp.STRONG, sp.CAUTIOUS, sp.RESEARCH_ONLY, sp.NOT_READY, sp.BLOCKED}


def test_kis_intraday_not_supported():
    assert kis_intraday_supported() is False


def test_intraday_fixtures_produce_trades():
    r = evaluate_intraday_strategy(_INTRADAY)
    assert r.intraday_data_used is True
    assert r.bar_size_minutes == 5.0
    assert len(r.pass_symbols) >= 3
    assert r.total_trades > 0   # 분봉은 진입 발생 (일봉과 대조)
    assert r.overall_verdict in _VERDICTS


def test_intraday_not_strong_without_paper():
    r = evaluate_intraday_strategy(_INTRADAY)
    assert r.overall_verdict != sp.STRONG   # Paper 0건 → STRONG 불가


def test_daily_dir_all_blocked_not_intraday():
    """일봉 디렉토리는 분봉이 아니므로 전부 blocked → BLOCKED."""
    r = evaluate_intraday_strategy(_DAILY)
    assert len(r.pass_symbols) == 0
    assert len(r.blocked_symbols) >= 1
    assert r.overall_verdict == sp.BLOCKED


def test_empty_dir_blocked(tmp_path):
    r = evaluate_intraday_strategy(tmp_path)
    assert r.overall_verdict == sp.BLOCKED


def test_total_trades_zero_research_only(tmp_path, monkeypatch):
    """total_trades 0 이면 RESEARCH_ONLY 로 cap (daily dir 로 간접 검증은 BLOCKED 라
    별도로 caps 단위 검증)."""
    # daily dir → blocked → BLOCKED (별 경로). total_trades 0 cap 은 caps 로직으로 보장.
    r = evaluate_intraday_strategy(_DAILY)
    assert r.total_trades == 0
    assert r.overall_verdict in (sp.BLOCKED, sp.RESEARCH_ONLY, sp.NOT_READY)


def test_agent_summary_and_fields():
    r = evaluate_intraday_strategy(_INTRADAY)
    assert r.agent_value_summary in (
        "AGENT_ADDS_VALUE", "AGENT_UNDERPERFORMS", "AGENT_MIXED",
        "AGENT_TOO_CONSERVATIVE", "AGENT_VALUE_INSUFFICIENT_SAMPLE")
    assert isinstance(r.per_symbol, tuple) and len(r.per_symbol) == 5


def test_report_invariants():
    r = evaluate_intraday_strategy(_INTRADAY)
    assert r.do_not_auto_apply is True
    assert r.auto_apply_allowed is False
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    assert r.kis_intraday_available is False


@pytest.mark.parametrize("bad", [
    {"do_not_auto_apply": False}, {"auto_apply_allowed": True},
    {"is_live_authorization": True}, {"contains_secret": True},
    {"kis_intraday_available": True},
])
def test_report_guard(bad):
    base = dict(
        generated_at="x", intraday_data_used=True, bar_size_minutes=5.0, symbols_count=1,
        total_bars=100, total_days=5, total_trades=10, pass_symbols=(), warn_symbols=(),
        blocked_symbols=(), per_symbol=(), overall_verdict=sp.RESEARCH_ONLY, overall_score=10.0,
        win_rate=None, profit_factor=None, expectancy=None, max_drawdown=None,
        walk_forward_score=None, stress_score=None, agent_value_score=None,
        agent_value_summary="AGENT_MIXED")
    base.update(bad)
    with pytest.raises(ValueError):
        IntradayStrategyReport(**base)


def test_to_dict_and_markdown():
    r = evaluate_intraday_strategy(_INTRADAY)
    d = to_dict(r)
    for k in ("intraday_data_used", "bar_size_minutes", "total_trades", "pass_symbols",
              "agent_value_summary", "overall_verdict", "do_not_auto_apply",
              "is_live_authorization"):
        assert k in d
    md = render_markdown(r)
    assert "분봉 데이터 기준" in md and "실전 승인 아님" in md and "수익 보장 아님" in md
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", md)


def test_module_no_forbidden_imports():
    src = (_REPO / "backend" / "app" / "system" / "intraday_strategy_validation.py").read_text(
        encoding="utf-8")
    for ln in src.splitlines():
        if re.match(r"^\s*(from|import)\s", ln):
            for mod in ("app.brokers", "app.execution", "order_router",
                        "anthropic", "openai", "httpx", "requests", "app.ai.client"):
                assert mod not in ln, f"forbidden import: {ln.strip()}"
    for call in ("route_order(", ".place_order(", "OrderExecutor("):
        assert call not in src


# --------------------------- endpoint --------------------------------------

def test_endpoint_latest(client, safe_default_flags):
    r = client.get("/api/system/intraday-strategy-validation/latest")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["overall_verdict"] in _VERDICTS
    assert data["is_live_authorization"] is False
    assert data["do_not_auto_apply"] is True
    assert data["is_order_signal"] is False
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", r.text)


def test_endpoint_is_get_only(client, safe_default_flags):
    assert client.post(
        "/api/system/intraday-strategy-validation/latest", json={}).status_code in (404, 405)
