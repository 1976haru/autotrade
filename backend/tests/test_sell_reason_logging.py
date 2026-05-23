"""P-26: 매도(SELL) 사유 정밀 기록 테스트.

infer_sell_reason 의 12개 reason_code 산출 + SELL reason_code 필수화 +
episode / council / summary 연결 + secret 미노출 + 정적 가드.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agents.sell_reason import (
    AI_AGENT_EXIT,
    CAT_PROFIT_EXIT,
    CAT_RISK_EXIT,
    CAT_STRATEGY_EXIT,
    MANUAL_EXIT,
    MARKET_CLOSE_EXIT,
    MOMENTUM_WEAKENING,
    RISK_REDUCTION,
    STOP_LOSS,
    STRATEGY_REVERSAL,
    TAKE_PROFIT,
    TIME_STOP,
    TRAILING_STOP,
    UNKNOWN_SELL_REASON,
    VWAP_BREAKDOWN,
    REASON_CATEGORY,
    REASON_MESSAGE,
    SellReason,
    infer_sell_reason,
    sell_reason_summary,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "agents" / "sell_reason.py"


# ── 1~11. 각 reason_code 산출 ──

def test_stop_loss_reason():
    r = infer_sell_reason(position={"entry_price": 1000, "current_price": 940,
                                    "stop_loss_price": 950})
    assert r.reason_code == STOP_LOSS
    assert r.category == CAT_RISK_EXIT
    assert "손절" in r.message


def test_take_profit_reason():
    r = infer_sell_reason(position={"entry_price": 1000, "current_price": 1080,
                                    "take_profit_price": 1050})
    assert r.reason_code == TAKE_PROFIT
    assert r.category == CAT_PROFIT_EXIT


def test_trailing_stop_reason():
    r = infer_sell_reason(position={"current_price": 960, "trailing_stop_price": 970})
    assert r.reason_code == TRAILING_STOP


def test_vwap_breakdown_reason():
    r = infer_sell_reason(votes=[{"strategy": "VWAP", "signal": "SELL"}])
    assert r.reason_code == VWAP_BREAKDOWN
    assert "VWAP" in r.source_strategies


def test_momentum_weakening_reason():
    r = infer_sell_reason(votes=[{"strategy": "MOMENTUM", "signal": "SELL"}])
    assert r.reason_code == MOMENTUM_WEAKENING


def test_market_close_exit_reason():
    r = infer_sell_reason(market_closed=True)
    assert r.reason_code == MARKET_CLOSE_EXIT


def test_risk_reduction_reason():
    r = infer_sell_reason(risk_result={"risk_reduction": True})
    assert r.reason_code == RISK_REDUCTION
    assert r.category == CAT_RISK_EXIT


def test_manual_exit_reason():
    r = infer_sell_reason(triggered_by="MANUAL")
    assert r.reason_code == MANUAL_EXIT


def test_strategy_reversal_reason():
    r = infer_sell_reason(votes=[{"strategy": "ORB", "signal": "SELL"},
                                 {"strategy": "GAP", "signal": "SELL"}])
    assert r.reason_code == STRATEGY_REVERSAL


def test_time_stop_reason():
    r = infer_sell_reason(position={"holding_minutes": 120, "max_holding_minutes": 60})
    assert r.reason_code == TIME_STOP


def test_ai_agent_exit_reason():
    # SELL 컨텍스트인데 단일 비-VWAP/MOMENTUM 전략 → 복합 판단으로 정규화.
    r = infer_sell_reason(decision=type("D", (), {"side": "SELL", "exit_plan": {}})(),
                          votes=[{"strategy": "ORB", "signal": "SELL"}])
    assert r.reason_code == AI_AGENT_EXIT


# ── 12. explicit + alias 정규화 ──

def test_explicit_reason_code_used_first():
    r = infer_sell_reason(explicit_reason_code="TAKE_PROFIT",
                          position={"current_price": 940, "stop_loss_price": 950})
    assert r.reason_code == TAKE_PROFIT  # 명시값 우선 (stop_loss 무시)


@pytest.mark.parametrize("alias,expected", [
    ("SL", STOP_LOSS), ("TP", TAKE_PROFIT), ("trailing", TRAILING_STOP),
    ("vwap", VWAP_BREAKDOWN), ("manual", MANUAL_EXIT), ("eod", MARKET_CLOSE_EXIT),
])
def test_reason_code_alias_normalized(alias, expected):
    assert infer_sell_reason(explicit_reason_code=alias).reason_code == expected


# ── 13. SELL reason_code 필수 / 자동 SELL UNKNOWN 금지 ──

def test_sell_never_returns_unknown():
    """자동 SELL 은 어떤 입력에서도 UNKNOWN 으로 끝나지 않는다."""
    r = infer_sell_reason(decision=type("D", (), {"side": "SELL", "exit_plan": {}})())
    assert r.reason_code != UNKNOWN_SELL_REASON
    assert r.reason_code == AI_AGENT_EXIT


def test_sell_reason_code_always_present():
    """SELL 사유는 항상 valid reason_code 를 가진다."""
    r = infer_sell_reason(decision=type("D", (), {"side": "SELL", "exit_plan": {}})())
    assert r.reason_code in REASON_CATEGORY
    assert r.message  # 메시지 비어있지 않음


def test_non_sell_empty_input_is_unknown():
    """SELL 컨텍스트가 전혀 없으면 방어적 UNKNOWN."""
    assert infer_sell_reason().reason_code == UNKNOWN_SELL_REASON


# ── 결정적 / invariant ──

def test_deterministic():
    kw = dict(position={"entry_price": 1000, "current_price": 940, "stop_loss_price": 950})
    a = infer_sell_reason(**kw).to_dict()
    b = infer_sell_reason(**kw).to_dict()
    assert a == b


def test_invariants_locked():
    r = infer_sell_reason(market_closed=True)
    assert r.is_live_authorization is False
    assert r.is_order_signal is False
    assert r.contains_secret is False
    d = r.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["contains_secret"] is False


def test_invariant_guard_rejects_true():
    with pytest.raises(ValueError):
        SellReason(reason_code=STOP_LOSS, category=CAT_RISK_EXIT,
                   message="x", is_live_authorization=True)
    with pytest.raises(ValueError):
        SellReason(reason_code=STOP_LOSS, category=CAT_RISK_EXIT,
                   message="x", is_order_signal=True)


def test_all_codes_have_category_and_message():
    for code in REASON_CATEGORY:
        assert code in REASON_MESSAGE
        assert REASON_MESSAGE[code]


def test_summary_shape():
    s = infer_sell_reason(market_closed=True).summary()
    assert set(s) == {"reason_code", "category", "message"}
    assert sell_reason_summary(None) == {}
    assert sell_reason_summary({"reason_code": STOP_LOSS})["category"] == CAT_RISK_EXIT


# ── council 연결 ──

def test_council_sell_carries_sell_reason():
    from app.agents.agent_council import StrategyMarketInput, run_agent_council
    inp = StrategyMarketInput(
        symbol="005930", current_price=900, prev_close=1000, open_price=1000,
        vwap=1000, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 980, 960, 940, 900),
        current_volume=120, avg_volume=100,
        market_regime="TREND_DOWN", regime_decision="ALLOW",
    )
    d = run_agent_council(inp, held_position=True)
    assert d.final_action.value == "SELL"
    assert d.sell_reason and d.sell_reason.get("reason_code")
    assert d.to_dict()["sell_reason"]["reason_code"] == d.sell_reason["reason_code"]


def test_council_buy_has_empty_sell_reason():
    from app.agents.agent_council import StrategyMarketInput, run_agent_council
    inp = StrategyMarketInput(
        symbol="005930", current_price=1100, prev_close=1000, open_price=1010,
        vwap=1050, opening_range_high=1010, opening_range_low=990,
        recent_closes=(1000, 1020, 1050, 1080, 1100),
        current_volume=150, avg_volume=100,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )
    d = run_agent_council(inp, held_position=False)
    assert d.final_action.value in ("BUY", "HOLD")
    assert d.sell_reason == {}
    assert d.to_dict()["sell_reason"] == {}


# ── episode 연결 (DB) ──

@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_episode_records_and_surfaces_sell_reason(db_session):
    from app.agents.decision_episode import (
        episode_to_dict,
        new_episode_id,
        record_episode,
        summarize_episodes,
    )
    eid = new_episode_id()
    sr = infer_sell_reason(votes=[{"strategy": "VWAP", "signal": "SELL"}]).to_dict()
    row = record_episode(
        db_session, episode_id=eid, final_action="SELL", symbol="005930",
        council={"final_action": "SELL"}, kis_order_result={"submitted": True},
        sell_reason=sr,
    )
    db_session.commit()
    d = episode_to_dict(row)
    # episode.reason_code 가 SELL 사유로 연결.
    assert d["reason_code"] == VWAP_BREAKDOWN
    assert d["sell_reason"]["reason_code"] == VWAP_BREAKDOWN
    assert d["sell_reason_summary"]["reason_code"] == VWAP_BREAKDOWN
    # council JSON 에 nest.
    assert d["council"]["sell_reason"]["reason_code"] == VWAP_BREAKDOWN
    # kis_order_result 에 carry.
    assert d["kis_order_result"]["sell_reason_code"] == VWAP_BREAKDOWN
    # summary by_sell_reason 집계.
    summ = summarize_episodes(db_session, limit=200)
    assert summ["by_sell_reason"].get(VWAP_BREAKDOWN, 0) >= 1
    assert summ["by_sell_category"].get(CAT_STRATEGY_EXIT, 0) >= 1


def test_buy_episode_has_empty_sell_reason(db_session):
    from app.agents.decision_episode import (
        episode_to_dict,
        new_episode_id,
        record_episode,
    )
    eid = new_episode_id()
    row = record_episode(db_session, episode_id=eid, final_action="BUY", symbol="005930",
                         council={"final_action": "BUY"})
    db_session.commit()
    d = episode_to_dict(row)
    assert d["sell_reason"] == {}
    assert d["sell_reason_summary"] == {}


# ── API ──

def test_infer_sell_reason_api():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    r = c.post("/api/agents/sell-reason/infer",
               json={"position": {"entry_price": 1000, "current_price": 940,
                                  "stop_loss_price": 950}})
    assert r.status_code == 200
    body = r.json()
    assert body["sell_reason"]["reason_code"] == STOP_LOSS
    assert body["is_order_signal"] is False
    assert body["is_live_authorization"] is False


# ── secret 미노출 / 정적 가드 ──

def test_to_dict_has_no_secret_keys():
    d = infer_sell_reason(
        market_closed=True,
        position={"current_price": 1000, "app_secret": "x", "account_no": "12345"},
    ).to_dict()
    flat = str(d).lower()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in [k.lower() for k in d.keys()]
    # metadata 에도 secret 키를 carry 하지 않음 (position 원문 미저장).
    assert "app_secret" not in flat


def test_module_no_forbidden_imports():
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
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


def test_module_no_order_calls():
    """docstring 설명은 허용하되, *코드 식별자* 로 broker/order 심볼 사용 0건."""
    src = _MODULE.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "OrderRequest"):
        assert bad not in names, f"forbidden code symbol: {bad}"
