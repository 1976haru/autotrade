"""P-29: Agent 전략 가중치 개선 후보 추천 테스트.

성과 반영(INCREASE/DECREASE/NEEDS_MORE_DATA) + 가중치 합/min/max/delta 제약 +
risk_profile 폭 차이 + Agent vs single warning + expected_effect + 자동 적용 금지
invariant + secret 미노출 + 정적 가드(설정 변경 / apply endpoint 없음).
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agents.weight_recommendation import (
    ACTION_DECREASE,
    ACTION_INCREASE,
    ACTION_NEEDS_MORE_DATA,
    DEFAULT_CURRENT_WEIGHTS,
    MAX_WEIGHT,
    MIN_WEIGHT,
    STATUS_NEEDS_MORE_DATA,
    STATUS_RECOMMENDATION_ONLY,
    WeightRecommendation,
    recommend_strategy_weights,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "agents" / "weight_recommendation.py"
_FIXED = datetime(2026, 5, 23, tzinfo=timezone.utc)


def _block(s, **kw):
    base = dict(strategy=s, evaluated_count=10, win_rate=0.5, profit_factor=1.0,
                payoff_ratio=1.0, max_drawdown=0.1, max_consecutive_losses=1,
                average_loss=-0.4, average_return=0.1)
    base.update(kw)
    return base


def _report(strategies, *, evaluated=30, warning=None):
    return {"evaluated_episodes": evaluated, "strategies": strategies,
            "agent_vs_single": {"warning": warning}}


def _good_bad_report():
    return _report([
        _block("ORB", evaluated_count=3),  # 표본 부족
        _block("MOMENTUM", win_rate=0.75, profit_factor=2.2, payoff_ratio=2.0,
               max_drawdown=0.05, evaluated_count=12),
        _block("GAP", win_rate=0.2, profit_factor=0.4, payoff_ratio=0.5,
               max_drawdown=0.45, max_consecutive_losses=4, average_loss=-1.5,
               evaluated_count=10),
        _block("VWAP", win_rate=0.7, profit_factor=1.8, payoff_ratio=1.6,
               max_drawdown=0.08, evaluated_count=8),
    ])


def _actions(rec):
    return {a["strategy"]: a for a in rec.strategy_actions}


# ── 1~9. 성과 반영 ──

def test_momentum_increase_when_good():
    a = _actions(recommend_strategy_weights(_good_bad_report()))["MOMENTUM"]
    assert a["action"] == ACTION_INCREASE
    assert a["delta"] > 0


def test_vwap_increase_when_good():
    a = _actions(recommend_strategy_weights(_good_bad_report()))["VWAP"]
    assert a["action"] == ACTION_INCREASE


def test_gap_decrease_when_bad():
    a = _actions(recommend_strategy_weights(_good_bad_report()))["GAP"]
    assert a["action"] == ACTION_DECREASE
    assert a["delta"] < 0


def test_orb_needs_more_data_when_low_sample():
    a = _actions(recommend_strategy_weights(_good_bad_report()))["ORB"]
    assert a["action"] == ACTION_NEEDS_MORE_DATA
    assert a["delta"] == 0


def test_profit_factor_reflected():
    rep = _report([_block("MOMENTUM", profit_factor=2.5, win_rate=0.6),
                   _block("GAP", profit_factor=0.5, win_rate=0.4),
                   _block("ORB"), _block("VWAP")])
    acts = _actions(recommend_strategy_weights(rep))
    assert acts["MOMENTUM"]["score"] > acts["GAP"]["score"]


def test_win_rate_reflected():
    rep = _report([_block("MOMENTUM", win_rate=0.9), _block("GAP", win_rate=0.1),
                   _block("ORB"), _block("VWAP")])
    acts = _actions(recommend_strategy_weights(rep))
    assert acts["MOMENTUM"]["score"] > acts["GAP"]["score"]


def test_payoff_ratio_reflected():
    rep = _report([_block("MOMENTUM", payoff_ratio=3.0, win_rate=0.6),
                   _block("VWAP", payoff_ratio=0.8, win_rate=0.6),
                   _block("ORB"), _block("GAP")])
    acts = _actions(recommend_strategy_weights(rep))
    assert acts["MOMENTUM"]["score"] >= acts["VWAP"]["score"]


def test_mdd_penalty():
    rep = _report([_block("MOMENTUM", max_drawdown=0.0, win_rate=0.6),
                   _block("VWAP", max_drawdown=0.5, win_rate=0.6),
                   _block("ORB"), _block("GAP")])
    acts = _actions(recommend_strategy_weights(rep))
    assert acts["MOMENTUM"]["score"] > acts["VWAP"]["score"]


def test_consecutive_losses_penalty():
    rep = _report([_block("MOMENTUM", max_consecutive_losses=0, win_rate=0.6),
                   _block("VWAP", max_consecutive_losses=6, win_rate=0.6),
                   _block("ORB"), _block("GAP")])
    acts = _actions(recommend_strategy_weights(rep))
    assert acts["MOMENTUM"]["score"] > acts["VWAP"]["score"]


# ── 10. 표본 부족 BLOCKED ──

def test_blocked_by_insufficient_sample():
    rec = recommend_strategy_weights(_report([_block("MOMENTUM")], evaluated=3))
    assert rec.status == STATUS_NEEDS_MORE_DATA
    assert "INSUFFICIENT_SAMPLE" in (rec.warning or "")
    assert rec.recommended_weights == rec.current_weights  # 변경 없음.


# ── 11~13. 가중치 제약 ──

def test_recommended_weights_sum_100():
    rec = recommend_strategy_weights(_good_bad_report())
    assert sum(rec.recommended_weights.values()) == 100


def test_min_max_weight_bounds():
    rec = recommend_strategy_weights(_good_bad_report())
    for v in rec.recommended_weights.values():
        assert MIN_WEIGHT <= v <= MAX_WEIGHT


def test_delta_cap_respected():
    # BALANCED max_delta=8 — 정규화 잔차 포함해도 과도하지 않아야.
    rec = recommend_strategy_weights(_good_bad_report(), risk_profile="BALANCED")
    for d in rec.deltas.values():
        assert abs(d) <= 8


# ── 14. risk_profile 폭 차이 ──

def test_risk_profile_delta_width_differs():
    rep = _good_bad_report()
    cons = recommend_strategy_weights(rep, risk_profile="CONSERVATIVE")
    aggr = recommend_strategy_weights(rep, risk_profile="AGGRESSIVE")
    cons_span = max(cons.deltas.values()) - min(cons.deltas.values())
    aggr_span = max(aggr.deltas.values()) - min(aggr.deltas.values())
    # 공격적 추천 폭 >= 보수적 추천 폭.
    assert aggr_span >= cons_span


# ── 15. market_regime 라벨 ──

def test_market_regime_label_carried():
    rec = recommend_strategy_weights(_good_bad_report(), market_regime="TREND_UP")
    assert rec.market_regime == "TREND_UP"


# ── 16. Agent vs single warning ──

def test_agent_underperform_warning_carried():
    rep = _good_bad_report()
    rep["agent_vs_single"] = {"warning": "Agent Council 이 MOMENTUM 단독보다 낮습니다."}
    rec = recommend_strategy_weights(rep)
    assert rec.warning and "MOMENTUM" in rec.warning


# ── 17. expected_effect ──

def test_expected_effect_present():
    rec = recommend_strategy_weights(_good_bad_report())
    assert rec.expected_effect
    assert "MOMENTUM" in rec.expected_effect or "상향" in rec.expected_effect


# ── 18~21. invariant ──

def test_requires_approval_and_no_auto_apply():
    rec = recommend_strategy_weights(_good_bad_report())
    assert rec.requires_operator_approval is True
    assert rec.auto_apply_allowed is False
    assert rec.is_order_signal is False
    assert rec.is_live_authorization is False
    assert rec.uses_account_balance is False
    d = rec.to_dict()
    assert d["requires_operator_approval"] is True
    assert d["auto_apply_allowed"] is False
    assert d["is_order_signal"] is False
    assert d["is_live_authorization"] is False


def test_invariant_guard_rejects_unsafe():
    with pytest.raises(ValueError):
        WeightRecommendation(recommendation_id="x", created_at="x",
                             status=STATUS_RECOMMENDATION_ONLY, lookback_count=1,
                             risk_profile="BALANCED", market_regime="ALL",
                             auto_apply_allowed=True)
    with pytest.raises(ValueError):
        WeightRecommendation(recommendation_id="x", created_at="x",
                             status=STATUS_RECOMMENDATION_ONLY, lookback_count=1,
                             risk_profile="BALANCED", market_regime="ALL",
                             requires_operator_approval=False)


def test_deterministic():
    rep = _good_bad_report()
    a = recommend_strategy_weights(rep, now=_FIXED).to_dict()
    b = recommend_strategy_weights(rep, now=_FIXED).to_dict()
    assert a == b


def test_current_weights_default():
    rec = recommend_strategy_weights(_good_bad_report())
    assert rec.current_weights == {**DEFAULT_CURRENT_WEIGHTS}


# ── 22~23. API ──

def test_api_ok():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.get("/api/agents/weight-recommendation?lookback_count=100")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["auto_apply_allowed"] is False
    assert body["summary"]["requires_operator_approval"] is True
    assert body["summary"]["is_order_signal"] is False
    assert "recommendation" in body
    assert "performance_basis" in body


def test_api_no_secret_keys():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    body = c.get("/api/agents/weight-recommendation").json()
    flat = str(body).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in flat


# ── 24~27. 정적 가드 (자동 적용 / apply endpoint / 설정 변경 / 실 계좌 없음) ──

def test_to_dict_no_secret_keys():
    d = recommend_strategy_weights(_good_bad_report()).to_dict()
    for bad in ("app_secret", "account_no", "access_token", "api_key", "password"):
        assert bad not in str(d).lower()


def test_module_does_not_mutate_strategy_weights():
    src = _MODULE.read_text(encoding="utf-8")
    # STRATEGY_WEIGHTS 에 대한 할당/변경 0건.
    assert "STRATEGY_WEIGHTS[" not in src
    assert "STRATEGY_WEIGHTS =" not in src
    assert ".update(" not in src or "STRATEGY_WEIGHTS" not in src


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


def test_module_no_order_or_apply_symbols():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "apply_weights", "save_weights", "get_balance", "account_balance"):
        assert bad not in names, f"forbidden code symbol: {bad}"


def test_no_apply_endpoint_in_routes():
    routes = (Path(__file__).resolve().parents[1] / "app" / "api" / "routes_agents.py"
              ).read_text(encoding="utf-8")
    for bad in ("weight-recommendation/apply", "weights/apply", "apply-weights"):
        assert bad not in routes, f"apply endpoint must not exist: {bad}"
