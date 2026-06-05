"""P-28: 전략별 성과 대시보드 analytics 테스트.

ORB/MOMENTUM/GAP/VWAP/Agent Council 집계 + 승률/평균/payoff/PF/MDD/연속손실 +
risk_profile/market_regime/time_phase 버킷 + Agent vs 단일 비교 + 데이터 부족 +
실 계좌 미사용 + secret 미노출 + 정적 가드.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.analytics.strategy_performance import (
    AGENT_COUNCIL,
    DEFAULT_STRATEGY_ORDER,
    StrategyPerformanceReport,
    calculate_strategy_performance,
    episode_return,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "analytics" / "strategy_performance.py"


def _ep(action, strat_signals, ret, *, sel=None, order=None, filled=False,
        rp="BALANCED", mr="TREND_UP", at="2026-05-20T01:00:00+00:00"):
    votes = [{"strategy": s, "signal": sig, "score": 70} for s, sig in strat_signals.items()]
    outcome = None
    if ret is not None:
        outcome = {"status": "COMPLETE",
                   "label": "PROFITABLE" if ret > 0 else "LOSS" if ret < 0 else "NEUTRAL",
                   "return_close": ret}
    oq = {"order_status": "FILLED"} if filled else {"order_status": None}
    return dict(
        final_action=action, votes=votes, selected_strategies=sel or [],
        outcome=outcome, broker_order_no=order, created_at=at,
        council={"risk_profile": rp, "market_regime": mr},
        kis_order_result={"submitted": bool(order), "order_quality": oq},
    )


def _sample():
    return [
        _ep("BUY", {"MOMENTUM": "BUY", "VWAP": "BUY", "ORB": "HOLD", "GAP": "SELL"},
            1.2, sel=["MOMENTUM", "VWAP"], order="P1", filled=True),
        _ep("BUY", {"MOMENTUM": "BUY", "VWAP": "SELL", "ORB": "BUY", "GAP": "HOLD"},
            -0.8, sel=["MOMENTUM", "ORB"], order="P2", filled=True),
        _ep("SELL", {"MOMENTUM": "SELL", "VWAP": "SELL", "ORB": "HOLD", "GAP": "SELL"},
            0.5, sel=["VWAP"], order="P3", filled=True),
        _ep("HOLD", {"MOMENTUM": "HOLD", "VWAP": "HOLD", "ORB": "HOLD", "GAP": "HOLD"},
            None),
    ]


def _by_name(report):
    return {b["strategy"]: b for b in report.strategies}


# ── 1~5. 전략별 집계 대상 ──

def test_all_strategies_present():
    r = calculate_strategy_performance(_sample())
    names = [b["strategy"] for b in r.strategies]
    assert names == list(DEFAULT_STRATEGY_ORDER)
    for s in ("ORB", "MOMENTUM", "GAP", "VWAP", AGENT_COUNCIL):
        assert s in names


def test_orb_aggregated():
    b = _by_name(calculate_strategy_performance(_sample()))["ORB"]
    assert b["decision_count"] == 4
    assert b["buy_vote_count"] == 1
    assert b["selected_count"] == 1


def test_momentum_aggregated():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    assert b["buy_vote_count"] == 2
    assert b["selected_count"] == 2
    assert b["evaluated_count"] == 2


def test_gap_aggregated():
    b = _by_name(calculate_strategy_performance(_sample()))["GAP"]
    assert b["sell_vote_count"] == 2
    assert b["selected_count"] == 0  # GAP 은 selected 에 한 번도 없음.


def test_vwap_aggregated():
    b = _by_name(calculate_strategy_performance(_sample()))["VWAP"]
    assert b["selected_count"] == 2
    assert b["win_rate"] == 1.0  # VWAP selected 2건 모두 양수.


def test_agent_council_aggregated():
    b = _by_name(calculate_strategy_performance(_sample()))[AGENT_COUNCIL]
    # BUY/SELL 3건이 council 거래로 평가.
    assert b["selected_count"] == 3
    assert b["evaluated_count"] == 3


# ── 6~10. count 지표 ──

def test_decision_buy_sell_hold_counts():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    assert b["decision_count"] == 4
    assert b["buy_vote_count"] == 2
    assert b["sell_vote_count"] == 1
    assert b["hold_vote_count"] == 1


def test_order_and_filled_counts():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    assert b["order_count"] == 2
    assert b["filled_count"] == 2


# ── 11~19. 성과 지표 ──

def test_win_rate_average_returns():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    # MOMENTUM selected: +1.2, -0.8 → win_rate 0.5.
    assert b["win_rate"] == 0.5
    assert b["average_win"] == 1.2
    assert b["average_loss"] == -0.8


def test_payoff_and_profit_factor():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    assert b["payoff_ratio"] == pytest.approx(1.5, abs=1e-3)
    assert b["profit_factor"] == pytest.approx(1.5, abs=1e-3)


def test_max_loss_and_drawdown_and_expectancy():
    b = _by_name(calculate_strategy_performance(_sample()))["MOMENTUM"]
    assert b["max_loss"] == -0.8
    assert b["max_drawdown"] >= 0.0
    assert "expectancy" in b


def test_consecutive_losses():
    eps = [
        _ep("BUY", {"MOMENTUM": "BUY"}, -0.5, sel=["MOMENTUM"], order="A", filled=True),
        _ep("BUY", {"MOMENTUM": "BUY"}, -0.3, sel=["MOMENTUM"], order="B", filled=True),
        _ep("BUY", {"MOMENTUM": "BUY"}, 0.4, sel=["MOMENTUM"], order="C", filled=True),
    ]
    b = _by_name(calculate_strategy_performance(eps))["MOMENTUM"]
    assert b["max_consecutive_losses"] == 2


# ── 20~22. 버킷 ──

def test_by_risk_profile():
    r = calculate_strategy_performance(_sample())
    assert "BALANCED" in r.by_risk_profile
    assert r.by_risk_profile["BALANCED"]["decision_count"] == 3  # BUY/SELL 3건.


def test_by_market_regime():
    r = calculate_strategy_performance(_sample())
    assert "TREND_UP" in r.by_market_regime


def test_by_time_phase():
    # 01:00 UTC = 10:00 KST = MORNING.
    r = calculate_strategy_performance(_sample())
    assert "MORNING" in r.by_time_phase


def test_time_phase_buckets_distinct():
    eps = [
        _ep("BUY", {"ORB": "BUY"}, 1.0, sel=["ORB"], order="A", filled=True,
            at="2026-05-20T00:10:00+00:00"),  # 09:10 KST OPENING_RANGE
        _ep("BUY", {"ORB": "BUY"}, 1.0, sel=["ORB"], order="B", filled=True,
            at="2026-05-20T05:00:00+00:00"),  # 14:00 KST CLOSING
    ]
    r = calculate_strategy_performance(eps)
    assert "OPENING_RANGE" in r.by_time_phase
    assert "CLOSING" in r.by_time_phase


# ── 23~24. Agent vs 단일 비교 ──

def test_agent_vs_single_comparison():
    r = calculate_strategy_performance(_sample())
    c = r.agent_vs_single
    assert c["best_single_strategy"] in ("MOMENTUM", "ORB", "GAP", "VWAP")
    assert "agent_profit_factor" in c
    assert "agent_outperforms_best_single" in c


def test_agent_underperform_warning():
    # 단일 ORB 가 강하게 이기고, council 최종판단은 손실이 섞이도록 구성.
    eps = [
        _ep("BUY", {"ORB": "BUY"}, 3.0, sel=["ORB"], order="A", filled=True),
        _ep("SELL", {"ORB": "HOLD"}, -2.0, sel=["VWAP"], order="B", filled=True),
    ]
    c = calculate_strategy_performance(eps).agent_vs_single
    if c["agent_outperforms_best_single"] is False:
        assert c["warning"]


# ── 25~26. 데이터 처리 ──

def test_outcome_missing_excluded_from_evaluated():
    eps = [_ep("BUY", {"MOMENTUM": "BUY"}, None, sel=["MOMENTUM"], order="A")]
    b = _by_name(calculate_strategy_performance(eps))["MOMENTUM"]
    assert b["selected_count"] == 1
    assert b["evaluated_count"] == 0  # outcome 없음 → 성과 평가 제외.


def test_insufficient_data():
    r = calculate_strategy_performance([])
    assert r.status == "INSUFFICIENT_DATA"
    r2 = calculate_strategy_performance([_ep("HOLD", {"ORB": "HOLD"}, None)])
    assert r2.status == "INSUFFICIENT_DATA"


def test_episode_return_basis():
    assert episode_return({"outcome": {"status": "COMPLETE", "return_close": 1.5}}) == 1.5
    assert episode_return({"outcome": {"status": "COMPLETE", "realized_pnl": 99,
                                       "return_close": 1.5}}) == 99.0
    assert episode_return({"outcome": {"status": "PENDING"}}) is None
    assert episode_return({}) is None


# ── invariant / 결정성 ──

def test_invariants_locked():
    r = calculate_strategy_performance(_sample())
    assert r.uses_account_balance is False
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    d = r.to_dict()
    assert d["uses_account_balance"] is False
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False


def test_invariant_guard_rejects_true():
    with pytest.raises(ValueError):
        StrategyPerformanceReport(status="OK", total_episodes=0, evaluated_episodes=0,
                                  uses_account_balance=True)
    with pytest.raises(ValueError):
        StrategyPerformanceReport(status="OK", total_episodes=0, evaluated_episodes=0,
                                  is_live_authorization=True)


def test_deterministic():
    eps = _sample()
    assert calculate_strategy_performance(eps).to_dict() == \
        calculate_strategy_performance(eps).to_dict()


# ── API ──

def test_api(db_session=None):
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.get("/api/agents/strategy-performance")
    assert r.status_code == 200
    body = r.json()
    assert body["contains_secret"] is False
    assert body["is_live_authorization"] is False
    assert body["uses_account_balance"] is False
    assert "strategies" in body


# ── secret 미노출 / 실 계좌 미사용 / 정적 가드 ──

def test_to_dict_no_secret_keys():
    d = calculate_strategy_performance(_sample()).to_dict()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in str(d).lower()


def test_module_no_forbidden_imports():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("app.brokers", "app.execution", "broker", "httpx", "requests",
                 "anthropic", "openai", "app.ai.assist", "app.ai.client")
    for imp in imported:
        for bad in forbidden:
            assert not imp.startswith(bad), f"forbidden import: {imp}"


def test_module_no_order_or_account_symbols():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "get_balance", "fetch_balance", "account_balance"):
        assert bad not in names, f"forbidden code symbol: {bad}"


def test_strategy_performance_endpoint_carries_episodes_analyzed():
    """D3: 응답에 episodes_analyzed 가 있어 프론트가 '집계 전'과 '실제 0'을
    구분할 수 있다. 빈 DB → episodes_analyzed == 0 (집계 전)."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db.base import Base
    from app.db.session import get_db
    from app.main import app

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)

    def _ov():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.get("/api/agents/strategy-performance")
        assert r.status_code == 200
        body = r.json()
        assert "episodes_analyzed" in body
        assert body["episodes_analyzed"] == 0   # 빈 DB → 집계 전(데이터 없음)
    finally:
        app.dependency_overrides.pop(get_db, None)
