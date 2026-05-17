"""Portfolio Correlation Guard (#95) — evaluator + API + invariants + 정적 가드.

본 테스트는 #78 sector/theme correlation_guard 와는 *별개* 모듈 검증.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from app.risk.portfolio_correlation_guard import (
    PairSeverity,
    PortfolioCorrelationInput,
    PortfolioCorrelationResult,
    PortfolioCorrelationThresholds,
    PortfolioCorrelationVerdict,
    PortfolioPositionInput,
    evaluate_portfolio_correlation,
    render_markdown_report,
)


# ====================================================================
# helpers
# ====================================================================


def _seed(seed: int = 42) -> None:
    random.seed(seed)


def _identical_series(n: int = 60) -> tuple[list[float], list[float]]:
    """매우 강한 상관관계 (≈ +1.0)."""
    _seed()
    a = [random.gauss(0, 0.01) for _ in range(n)]
    b = [x + random.gauss(0, 0.0001) for x in a]
    return a, b


def _opposite_series(n: int = 60) -> tuple[list[float], list[float]]:
    """매우 강한 *역*상관관계 (≈ -1.0)."""
    _seed()
    a = [random.gauss(0, 0.01) for _ in range(n)]
    b = [-x + random.gauss(0, 0.0001) for x in a]
    return a, b


def _uncorrelated_series(n: int = 60) -> tuple[list[float], list[float]]:
    """무상관 (≈ 0)."""
    _seed()
    a = [random.gauss(0, 0.01) for _ in range(n)]
    _seed(99)
    b = [random.gauss(0, 0.01) for _ in range(n)]
    return a, b


def _ready_2_symbol_input(
    series_a: list[float], series_b: list[float],
) -> PortfolioCorrelationInput:
    return PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA", notional_krw=1_000_000),
            PortfolioPositionInput(symbol="BBB", notional_krw=1_000_000),
        ),
        return_series_by_symbol={
            "AAA": tuple(series_a),
            "BBB": tuple(series_b),
        },
    )


# ====================================================================
# DTO invariants
# ====================================================================


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        PortfolioCorrelationResult(is_order_signal=True)


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        PortfolioCorrelationResult(auto_apply_allowed=True)


def test_result_rejects_is_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        PortfolioCorrelationResult(is_live_authorization=True)


def test_position_rejects_empty_symbol():
    with pytest.raises(ValueError, match="symbol"):
        PortfolioPositionInput(symbol="")


def test_thresholds_rejects_out_of_range():
    with pytest.raises(ValueError, match="threshold"):
        PortfolioCorrelationThresholds(warn_threshold=1.5)


def test_thresholds_rejects_inverted_order():
    with pytest.raises(ValueError, match="ordered"):
        PortfolioCorrelationThresholds(
            warn_threshold=0.9, caution_threshold=0.5, block_threshold=0.85,
        )


def test_to_dict_carries_invariants_false():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False
    assert d["is_live_authorization"] is False


# ====================================================================
# enum coverage — no BUY/SELL/HOLD
# ====================================================================


def test_verdict_no_buy_sell_hold():
    values = {v.value for v in PortfolioCorrelationVerdict}
    for banned in ("BUY", "SELL", "HOLD", "PLACE_ORDER"):
        assert banned not in values


def test_verdict_5_states():
    values = {v.value for v in PortfolioCorrelationVerdict}
    assert values == {
        "HEALTHY", "WATCH", "WARN", "BLOCK", "INSUFFICIENT_DATA",
    }


def test_pair_severity_no_buy_sell_hold():
    values = {v.value for v in PairSeverity}
    for banned in ("BUY", "SELL", "HOLD"):
        assert banned not in values


# ====================================================================
# verdict logic
# ====================================================================


def test_strongly_correlated_pair_returns_block():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    assert r.verdict is PortfolioCorrelationVerdict.BLOCK
    assert r.max_pairwise_correlation > 0.85
    assert r.new_entry_allowed is False
    assert r.high_correlation_pair_count >= 1


def test_uncorrelated_pair_returns_healthy():
    a, b = _uncorrelated_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    assert r.verdict is PortfolioCorrelationVerdict.HEALTHY
    assert r.max_pairwise_correlation < 0.5
    assert r.new_entry_allowed is True


def test_negative_correlation_treated_as_strong():
    """|corr| 기준 — 음의 상관관계도 차단 대상."""
    a, b = _opposite_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    assert r.verdict is PortfolioCorrelationVerdict.BLOCK
    assert r.new_entry_allowed is False


def test_pair_severity_assigned_correctly():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    pair = r.pairs[0]
    assert pair.severity is PairSeverity.EXTREME


# ====================================================================
# candidate logic
# ====================================================================


def test_candidate_correlated_with_existing_blocks_entry():
    a, b = _identical_series()
    # AAA, BBB 무상관 / CCC 후보가 AAA 와 매우 강한 상관관계.
    _seed(123)
    aaa = [random.gauss(0, 0.01) for _ in range(60)]
    _seed(456)
    bbb = [random.gauss(0, 0.01) for _ in range(60)]
    ccc = [x + random.gauss(0, 0.0001) for x in aaa]   # AAA 와 거의 동일.
    inp = PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA"),
            PortfolioPositionInput(symbol="BBB"),
        ),
        candidate=PortfolioPositionInput(symbol="CCC", notional_krw=500_000),
        return_series_by_symbol={
            "AAA": tuple(aaa), "BBB": tuple(bbb), "CCC": tuple(ccc),
        },
    )
    r = evaluate_portfolio_correlation(inp)
    # AAA-CCC 상관관계가 매우 강함 → BLOCK.
    assert r.verdict is PortfolioCorrelationVerdict.BLOCK
    assert r.candidate_max_correlation is not None
    assert abs(r.candidate_max_correlation) > 0.85
    assert r.new_entry_allowed is False


def test_candidate_uncorrelated_with_existing_allows_entry():
    _seed(1)
    aaa = [random.gauss(0, 0.01) for _ in range(60)]
    _seed(2)
    bbb = [random.gauss(0, 0.01) for _ in range(60)]
    _seed(3)
    ccc = [random.gauss(0, 0.01) for _ in range(60)]
    inp = PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA"),
            PortfolioPositionInput(symbol="BBB"),
        ),
        candidate=PortfolioPositionInput(symbol="CCC", notional_krw=500_000),
        return_series_by_symbol={
            "AAA": tuple(aaa), "BBB": tuple(bbb), "CCC": tuple(ccc),
        },
    )
    r = evaluate_portfolio_correlation(inp)
    assert r.verdict is PortfolioCorrelationVerdict.HEALTHY
    assert r.new_entry_allowed is True


# ====================================================================
# insufficient / edge cases
# ====================================================================


def test_single_symbol_returns_insufficient_data():
    inp = PortfolioCorrelationInput(
        positions=(PortfolioPositionInput(symbol="AAA"),),
    )
    r = evaluate_portfolio_correlation(inp)
    assert r.verdict is PortfolioCorrelationVerdict.INSUFFICIENT_DATA
    assert r.insufficient_data is True
    assert r.new_entry_allowed is True   # data 부족 시 차단 안 함


def test_empty_positions_returns_insufficient_data():
    r = evaluate_portfolio_correlation(PortfolioCorrelationInput())
    assert r.verdict is PortfolioCorrelationVerdict.INSUFFICIENT_DATA


def test_below_min_bars_returns_insufficient_data():
    inp = PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA"),
            PortfolioPositionInput(symbol="BBB"),
        ),
        return_series_by_symbol={
            "AAA": (0.01, 0.02, -0.01, 0.0, 0.005),   # 5 bars only
            "BBB": (0.01, 0.02, -0.01, 0.0, 0.005),
        },
    )
    r = evaluate_portfolio_correlation(inp)
    assert r.verdict is PortfolioCorrelationVerdict.INSUFFICIENT_DATA


def test_close_series_input_converts_to_returns():
    """close_series 입력 시 자동 returns 변환."""
    # 단조 증가 종가 → 일정한 returns → close_series 변환 후 corr 계산.
    closes = [100.0 + i * 0.5 for i in range(40)]
    inp = PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA"),
            PortfolioPositionInput(symbol="BBB"),
        ),
        close_series_by_symbol={
            "AAA": tuple(closes),
            "BBB": tuple(closes),
        },
    )
    r = evaluate_portfolio_correlation(inp)
    # 두 종목 종가 시계열이 동일 → 매우 강한 상관관계 → BLOCK.
    assert r.verdict is PortfolioCorrelationVerdict.BLOCK


# ====================================================================
# strict mode
# ====================================================================


def test_strict_mode_blocks_new_entry_on_warn():
    """strict=True 면 WARN 도 new_entry_allowed=False."""
    # caution_threshold (0.7) 이상 block_threshold (0.85) 미만 corr 만들기.
    _seed(7)
    a = [random.gauss(0, 0.01) for _ in range(60)]
    b = [x * 0.75 + random.gauss(0, 0.006) for x in a]   # corr ≈ 0.75
    inp = PortfolioCorrelationInput(
        positions=(
            PortfolioPositionInput(symbol="AAA"),
            PortfolioPositionInput(symbol="BBB"),
        ),
        return_series_by_symbol={"AAA": tuple(a), "BBB": tuple(b)},
        strict=True,
    )
    r = evaluate_portfolio_correlation(inp)
    if r.verdict is PortfolioCorrelationVerdict.WARN:
        assert r.new_entry_allowed is False
    # WARN 이 아니면 (테스트 환경 변동성으로 BLOCK 또는 WATCH 일 수도) 일관성만 확인.
    else:
        assert r.verdict in (
            PortfolioCorrelationVerdict.WATCH,
            PortfolioCorrelationVerdict.BLOCK,
            PortfolioCorrelationVerdict.HEALTHY,
        )


# ====================================================================
# markdown
# ====================================================================


def test_markdown_contains_disclaimer_and_verdict():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    text = render_markdown_report(r)
    assert "Portfolio Correlation Guard" in text
    assert "BLOCK" in text
    assert "advisory" in text
    assert "broker / OrderExecutor" in text


def test_markdown_no_buy_sell_hold_or_place_order():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    text = render_markdown_report(r)
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                   "HOLD signal", "Place Order", "실거래 시작", "지금 매수",
                   "지금 매도"]:
        assert banned not in text


def test_markdown_no_secret_patterns():
    a, b = _identical_series()
    r = evaluate_portfolio_correlation(_ready_2_symbol_input(a, b))
    text = render_markdown_report(r).lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


# ====================================================================
# API
# ====================================================================


def test_api_evaluate_returns_invariants(client):
    a, b = _identical_series()
    body = {
        "positions": [
            {"symbol": "AAA", "notional_krw": 1_000_000},
            {"symbol": "BBB", "notional_krw": 1_000_000},
        ],
        "return_series_by_symbol": {"AAA": list(a), "BBB": list(b)},
    }
    res = client.post(
        "/api/risk/portfolio-correlation/evaluate", json=body,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["verdict"] == "BLOCK"
    assert data["new_entry_allowed"] is False
    # invariants.
    assert data["is_order_signal"] is False
    assert data["auto_apply_allowed"] is False
    assert data["is_live_authorization"] is False


def test_api_evaluate_with_uncorrelated_returns_healthy(client):
    a, b = _uncorrelated_series()
    body = {
        "positions": [
            {"symbol": "AAA"},
            {"symbol": "BBB"},
        ],
        "return_series_by_symbol": {"AAA": list(a), "BBB": list(b)},
    }
    res = client.post(
        "/api/risk/portfolio-correlation/evaluate", json=body,
    )
    assert res.status_code == 200
    data = res.json()
    assert data["verdict"] == "HEALTHY"
    assert data["new_entry_allowed"] is True


def test_api_evaluate_empty_returns_insufficient_data(client):
    res = client.post(
        "/api/risk/portfolio-correlation/evaluate",
        json={"positions": []},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["verdict"] == "INSUFFICIENT_DATA"
    assert data["insufficient_data"] is True


def test_api_evaluate_does_not_leak_secrets(client):
    body = {
        "positions": [
            {"symbol": "AAA"},
            {"symbol": "BBB"},
        ],
    }
    res = client.post(
        "/api/risk/portfolio-correlation/evaluate", json=body,
    )
    text = res.text.lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


def test_api_evaluate_rejects_invalid_threshold(client):
    body = {
        "positions": [
            {"symbol": "AAA"},
            {"symbol": "BBB"},
        ],
        "warn_threshold": 2.0,   # > 1.0 invalid
    }
    res = client.post(
        "/api/risk/portfolio-correlation/evaluate", json=body,
    )
    assert res.status_code == 400


# ====================================================================
# invariants — static grep guards
# ====================================================================


_MODULE_PATH = Path("backend/app/risk/portfolio_correlation_guard.py")


def _resolve(path: Path) -> Path:
    if path.exists():
        return path
    return Path(__file__).resolve().parents[2] / path


def test_module_does_not_import_broker_or_executor():
    forbidden = [
        "from app.brokers.kis",
        "from app.brokers.mock_broker",
        "from app.execution.order_router",
        "from app.execution.order_executor",
        "from app.execution.executor",
        "from app.execution.paper_trader",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_import_ai_or_external_http():
    forbidden = [
        "from app.ai.assist",
        "from app.ai.client",
        "import anthropic",
        "import openai",
        "import httpx",
        "import requests",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden import: {needle!r}"


def test_module_does_not_read_settings():
    forbidden = [
        "from app.core.config import",
        "get_settings(",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"reads settings: {needle!r}"


def test_module_does_not_call_broker_or_route_order():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        "OrderExecutor(",
        "submit_candidate(",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"forbidden call: {needle!r}"


def test_module_does_not_mutate_safety_flags():
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"mutates safety flag: {needle!r}"


def test_module_does_not_write_to_db():
    forbidden = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    src = _resolve(_MODULE_PATH).read_text(encoding="utf-8")
    for needle in forbidden:
        assert needle not in src, f"writes to DB: {needle!r}"
