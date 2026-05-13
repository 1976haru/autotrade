"""CorrelationGuard (#78) — Rule + helpers + API + invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.risk.correlation_guard import (
    CandidateOrder,
    CorrelationGuardInput,
    CorrelationGuardPolicy,
    CorrelationGuardResult,
    CorrelationGuardRule,
    CorrelationGuardVerdict,
    HeldPosition,
    SymbolMeta,
    compute_return_correlation,
    returns_from_closes,
)


def _meta(symbol: str, sector: str = "", themes=()) -> SymbolMeta:
    return SymbolMeta(symbol=symbol, sector=sector, themes=tuple(themes))


def _candidate(
    symbol="A", side="BUY", notional=100_000, sector="", themes=(),
) -> CandidateOrder:
    return CandidateOrder(
        symbol=symbol, side=side, notional=notional,
        meta=_meta(symbol, sector, themes),
    )


def _held(symbol, notional, sector="", themes=()) -> HeldPosition:
    return HeldPosition(meta=_meta(symbol, sector, themes), notional=notional)


# ---------- DTO invariants ----------


def test_result_rejects_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        CorrelationGuardResult(
            verdict=CorrelationGuardVerdict.PASS, is_order_signal=True,
        )


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        CorrelationGuardResult(
            verdict=CorrelationGuardVerdict.PASS, auto_apply_allowed=True,
        )


def test_to_dict_has_invariant_flags():
    rule = CorrelationGuardRule(policy=CorrelationGuardPolicy())
    r = rule.evaluate(CorrelationGuardInput(candidate=_candidate()))
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- SELL / EXIT pass-through ----------


def test_sell_orders_pass_through():
    """SELL은 *리스크 축소* — 가드가 항상 SKIP_NON_BUY."""
    pol = CorrelationGuardPolicy(max_symbols_per_sector=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, sector="SEMI"),
        _held("B", 100_000, sector="SEMI"),
    )
    cand = _candidate(symbol="C", side="SELL", sector="SEMI")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.SKIP_NON_BUY
    assert r.blocked_reasons == []


def test_sell_for_held_symbol_pass_through_even_when_sector_full():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=1)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 500_000, sector="SEMI"),)
    cand = _candidate(symbol="A", side="SELL", sector="SEMI")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.SKIP_NON_BUY


# ---------- sector / symbol count ----------


def test_pass_when_sector_count_under_limit():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=3)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 100_000, sector="SEMI"),)
    cand = _candidate(symbol="B", sector="SEMI")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.PASS


def test_reject_when_sector_count_exceeds():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, sector="SEMI"),
        _held("B", 100_000, sector="SEMI"),
    )
    cand = _candidate(symbol="C", sector="SEMI")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.REJECT
    assert any("sector" in c for c in r.blocked_reasons)


def test_warn_when_sector_count_near_limit():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=4, warn_ratio=0.75)
    rule = CorrelationGuardRule(policy=pol)
    # current=2 sector SEMI, candidate adds 1 → projected=3 = 3/4 = 0.75 ratio → WARN.
    held = (
        _held("A", 100_000, sector="SEMI"),
        _held("B", 100_000, sector="SEMI"),
    )
    cand = _candidate(symbol="C", sector="SEMI")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.WARN


def test_same_symbol_repeat_buy_does_not_increase_count():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, sector="SEMI"),
        _held("B", 100_000, sector="SEMI"),
    )
    cand = _candidate(symbol="A", sector="SEMI")  # 같은 심볼 추가 매수
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    # 종목 수는 그대로 2 — sector 종목 수 한도 통과.
    assert r.verdict is CorrelationGuardVerdict.PASS
    assert r.projected_sector_symbol_count == 2


# ---------- sector absolute notional ----------


def test_reject_when_sector_exposure_exceeds():
    pol = CorrelationGuardPolicy(max_sector_exposure=300_000)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 200_000, sector="BIO"),)
    cand = _candidate(symbol="B", notional=200_000, sector="BIO")
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.REJECT
    assert any("노출" in c for c in r.blocked_reasons)


def test_warn_when_sector_exposure_near_limit():
    pol = CorrelationGuardPolicy(max_sector_exposure=500_000, warn_ratio=0.8)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 200_000, sector="BIO"),)
    cand = _candidate(symbol="B", notional=300_000, sector="BIO")
    # projected = 500_000 == limit → warn (≥ 400_000)
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    # 정확히 limit이면 REJECT가 아니라 PASS but WARN — strict > 비교.
    assert r.verdict is CorrelationGuardVerdict.WARN


# ---------- sector % of equity ----------


def test_reject_when_sector_pct_exceeds():
    pol = CorrelationGuardPolicy(max_sector_exposure_pct=0.30)  # 30%
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 1_500_000, sector="BIO"),)
    cand = _candidate(symbol="B", notional=2_000_000, sector="BIO")
    r = rule.evaluate(CorrelationGuardInput(
        candidate=cand, held_positions=held, equity_krw=10_000_000,
    ))
    assert r.verdict is CorrelationGuardVerdict.REJECT


def test_pct_check_skipped_when_equity_zero():
    pol = CorrelationGuardPolicy(max_sector_exposure_pct=0.30)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 5_000_000, sector="BIO"),)
    cand = _candidate(symbol="B", notional=5_000_000, sector="BIO")
    r = rule.evaluate(CorrelationGuardInput(
        candidate=cand, held_positions=held, equity_krw=0,
    ))
    # equity=0이면 pct 검사 skip → PASS.
    assert r.verdict is CorrelationGuardVerdict.PASS


# ---------- theme limits ----------


def test_reject_when_theme_count_exceeds():
    pol = CorrelationGuardPolicy(max_symbols_per_theme=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, themes=("AI",)),
        _held("B", 100_000, themes=("AI",)),
    )
    cand = _candidate(symbol="C", themes=("AI",))
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.REJECT
    assert any("theme 'AI'" in c for c in r.blocked_reasons)


def test_reject_when_theme_exposure_exceeds():
    pol = CorrelationGuardPolicy(max_theme_exposure=400_000)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 250_000, themes=("AI",)),)
    cand = _candidate(symbol="B", notional=200_000, themes=("AI",))
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.REJECT


def test_reject_when_theme_pct_exceeds():
    pol = CorrelationGuardPolicy(max_theme_exposure_pct=0.20)
    rule = CorrelationGuardRule(policy=pol)
    held = (_held("A", 1_500_000, themes=("Battery",)),)
    cand = _candidate(symbol="B", notional=1_000_000, themes=("Battery",))
    r = rule.evaluate(CorrelationGuardInput(
        candidate=cand, held_positions=held, equity_krw=10_000_000,
    ))
    assert r.verdict is CorrelationGuardVerdict.REJECT


def test_multiple_theme_each_checked_independently():
    pol = CorrelationGuardPolicy(max_symbols_per_theme=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, themes=("AI", "Robotics")),
        _held("B", 100_000, themes=("AI",)),
    )
    cand = _candidate(symbol="C", themes=("AI", "Robotics"))
    # AI projected = 3 (over limit), Robotics projected = 2 (=limit, WARN).
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.REJECT
    assert any("'AI'" in c for c in r.blocked_reasons)


# ---------- empty / disabled ----------


def test_pass_when_no_policy_set():
    """모든 한도 0이면 PASS."""
    rule = CorrelationGuardRule(policy=CorrelationGuardPolicy())
    held = (
        _held("A", 100_000, sector="SEMI", themes=("AI",)),
        _held("B", 100_000, sector="SEMI", themes=("AI",)),
        _held("C", 100_000, sector="SEMI", themes=("AI",)),
    )
    cand = _candidate(symbol="D", sector="SEMI", themes=("AI",))
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.PASS


def test_pass_when_no_sector_or_theme_on_candidate():
    pol = CorrelationGuardPolicy(max_symbols_per_sector=2)
    rule = CorrelationGuardRule(policy=pol)
    held = (
        _held("A", 100_000, sector="SEMI"),
        _held("B", 100_000, sector="SEMI"),
    )
    cand = _candidate(symbol="C")  # sector / themes empty
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.verdict is CorrelationGuardVerdict.PASS


# ---------- exposure dict carry ----------


def test_sector_and_theme_exposure_carry():
    rule = CorrelationGuardRule(policy=CorrelationGuardPolicy())
    held = (
        _held("A", 100_000, sector="SEMI", themes=("AI",)),
        _held("B", 200_000, sector="BIO", themes=("Health",)),
        _held("C", 50_000,  sector="SEMI", themes=("AI", "GPU")),
    )
    cand = _candidate(symbol="D", sector="SEMI", themes=("AI",))
    r = rule.evaluate(CorrelationGuardInput(candidate=cand, held_positions=held))
    assert r.sector_exposure["SEMI"] == 150_000
    assert r.sector_exposure["BIO"]  == 200_000
    assert r.theme_exposure["AI"]    == 150_000
    assert r.theme_exposure["GPU"]   == 50_000


# ---------- correlation helpers ----------


def test_returns_from_closes_basic():
    out = returns_from_closes([100.0, 110.0, 99.0])
    assert len(out) == 2
    assert out[0] == pytest.approx(0.10)
    assert out[1] == pytest.approx(-0.10)


def test_returns_from_closes_skips_invalid():
    out = returns_from_closes([100.0, 0, 110.0, -1, 120.0])
    # 0 / -1 skip — 110/100, 120/110.
    assert len(out) == 2


def test_correlation_perfect_positive():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 5
    b = [1.0, 2.0, 3.0, 4.0, 5.0] * 5
    c = compute_return_correlation(a, b, min_bars=10)
    assert c == pytest.approx(1.0)


def test_correlation_perfect_negative():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 5
    b = [5.0, 4.0, 3.0, 2.0, 1.0] * 5
    c = compute_return_correlation(a, b, min_bars=10)
    assert c == pytest.approx(-1.0)


def test_correlation_returns_none_when_insufficient_bars():
    a = [1.0, 2.0, 3.0]
    b = [3.0, 2.0, 1.0]
    assert compute_return_correlation(a, b, min_bars=20) is None


def test_correlation_returns_none_when_zero_variance():
    a = [1.0] * 30
    b = [1.0, 2.0] * 15
    assert compute_return_correlation(a, b, min_bars=10) is None


# ---------- API ----------


def test_route_preview_pass(client):
    body = {
        "candidate": {
            "symbol": "C", "side": "BUY", "notional": 100000,
            "meta": {"symbol": "C", "sector": "SEMI", "themes": []},
        },
        "held_positions": [{
            "meta": {"symbol": "A", "sector": "SEMI", "themes": []},
            "notional": 100000,
        }],
        "policy": {"max_symbols_per_sector": 3},
    }
    res = client.post("/api/risk/correlation-guard/preview", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "PASS"
    assert data["is_order_signal"] is False
    assert data["auto_apply_allowed"] is False


def test_route_preview_reject(client):
    body = {
        "candidate": {
            "symbol": "C", "side": "BUY", "notional": 100000,
            "meta": {"symbol": "C", "sector": "SEMI", "themes": ["AI"]},
        },
        "held_positions": [
            {"meta": {"symbol": "A", "sector": "SEMI", "themes": ["AI"]},
             "notional": 200000},
            {"meta": {"symbol": "B", "sector": "SEMI", "themes": ["AI"]},
             "notional": 200000},
        ],
        "policy": {"max_symbols_per_sector": 2, "max_symbols_per_theme": 2},
    }
    res = client.post("/api/risk/correlation-guard/preview", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "REJECT"


def test_route_preview_sell_pass_through(client):
    body = {
        "candidate": {
            "symbol": "A", "side": "SELL", "notional": 100000,
            "meta": {"symbol": "A", "sector": "SEMI", "themes": []},
        },
        "held_positions": [
            {"meta": {"symbol": "A", "sector": "SEMI", "themes": []},
             "notional": 500000},
            {"meta": {"symbol": "B", "sector": "SEMI", "themes": []},
             "notional": 500000},
        ],
        "policy": {"max_symbols_per_sector": 1},
    }
    res = client.post("/api/risk/correlation-guard/preview", json=body)
    assert res.status_code == 200
    assert res.json()["verdict"] == "SKIP_NON_BUY"


def test_route_response_does_not_leak_secrets(client):
    body = {
        "candidate": {"symbol": "x", "side": "BUY", "notional": 0,
                      "meta": {"symbol": "x"}},
    }
    res = client.post("/api/risk/correlation-guard/preview", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


# ---------- invariants — static grep guards ----------


_MODULE_PATH = Path("backend/app/risk/correlation_guard.py")


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_module_does_not_import_broker_or_executor():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_write_to_db():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    write_patterns = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in write_patterns:
        assert needle not in src, f"writes to DB: {needle!r}"


def test_module_does_not_read_settings():
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    for needle in forbidden:
        assert needle not in src, f"reads settings directly: {needle!r}"
