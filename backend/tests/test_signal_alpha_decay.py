"""Signal Alpha Decay (#94) — evaluator + helpers + API + invariants + 정적 가드.

본 테스트는 #77 governance/alpha_decay (전략 단위) 와는 *별개* 모듈 검증.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.analytics.signal_alpha_decay import (
    DecaySeverity,
    FreshnessVerdict,
    SignalAlphaDecayInput,
    SignalAlphaDecayResult,
    SignalSamplePoint,
    compute_signal_age_minutes,
    evaluate_signal_alpha_decay,
    freshness_verdict_for_age,
    is_signal_actionable,
    render_markdown_report,
)


# ====================================================================
# helpers
# ====================================================================


def _fresh_samples() -> tuple[SignalSamplePoint, ...]:
    """decay 가 적은 (FRESH 수준) 표본 — 모든 bucket 이 t=0 의 80~95%."""
    return (
        SignalSamplePoint(age_minutes=0,  mean_return_bps=20.0, sample_count=100),
        SignalSamplePoint(age_minutes=1,  mean_return_bps=19.0, sample_count=100),
        SignalSamplePoint(age_minutes=3,  mean_return_bps=18.5, sample_count=100),
        SignalSamplePoint(age_minutes=5,  mean_return_bps=18.0, sample_count=100),
        SignalSamplePoint(age_minutes=10, mean_return_bps=17.0, sample_count=100),
        SignalSamplePoint(age_minutes=30, mean_return_bps=16.0, sample_count=100),
        SignalSamplePoint(age_minutes=60, mean_return_bps=15.0, sample_count=100),
    )


def _decaying_samples() -> tuple[SignalSamplePoint, ...]:
    """decay 가 중간 수준 — DECAYING."""
    return (
        SignalSamplePoint(age_minutes=0,  mean_return_bps=20.0, sample_count=100),
        SignalSamplePoint(age_minutes=5,  mean_return_bps=12.0, sample_count=100),
        SignalSamplePoint(age_minutes=30, mean_return_bps=6.0,  sample_count=100),
    )


def _expired_samples() -> tuple[SignalSamplePoint, ...]:
    """severe decay — EXPIRED."""
    return (
        SignalSamplePoint(age_minutes=0,  mean_return_bps=30.0, sample_count=100),
        SignalSamplePoint(age_minutes=5,  mean_return_bps=3.0,  sample_count=100),
        SignalSamplePoint(age_minutes=30, mean_return_bps=1.0,  sample_count=100),
        SignalSamplePoint(age_minutes=60, mean_return_bps=0.5,  sample_count=100),
    )


# ====================================================================
# DTO invariants
# ====================================================================


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        SignalAlphaDecayResult(
            strategy_name="x",
            is_order_signal=True,
        )


def test_result_rejects_auto_apply_allowed_true():
    with pytest.raises(ValueError, match="auto_apply_allowed"):
        SignalAlphaDecayResult(
            strategy_name="x",
            auto_apply_allowed=True,
        )


def test_result_rejects_is_live_authorization_true():
    with pytest.raises(ValueError, match="is_live_authorization"):
        SignalAlphaDecayResult(
            strategy_name="x",
            is_live_authorization=True,
        )


def test_sample_point_rejects_negative_age():
    with pytest.raises(ValueError, match="age_minutes"):
        SignalSamplePoint(age_minutes=-1, mean_return_bps=10.0, sample_count=5)


def test_sample_point_rejects_negative_sample_count():
    with pytest.raises(ValueError, match="sample_count"):
        SignalSamplePoint(age_minutes=1, mean_return_bps=10.0, sample_count=-1)


def test_input_rejects_empty_strategy_name():
    with pytest.raises(ValueError, match="strategy_name"):
        SignalAlphaDecayInput(strategy_name="")


def test_to_dict_carries_invariants_false():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="sma", samples=_fresh_samples(),
    ))
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["auto_apply_allowed"] is False
    assert d["is_live_authorization"] is False


# ====================================================================
# enum coverage — no BUY/SELL/HOLD
# ====================================================================


def test_freshness_verdict_no_buy_sell_hold():
    values = {v.value for v in FreshnessVerdict}
    for banned in ("BUY", "SELL", "HOLD", "PLACE_ORDER"):
        assert banned not in values


def test_freshness_verdict_5_states():
    values = {v.value for v in FreshnessVerdict}
    assert values == {"FRESH", "DECAYING", "STALE", "EXPIRED", "UNKNOWN"}


def test_decay_severity_no_buy_sell_hold():
    values = {v.value for v in DecaySeverity}
    for banned in ("BUY", "SELL", "HOLD"):
        assert banned not in values


# ====================================================================
# verdict / decay_score logic
# ====================================================================


def test_fresh_signal_returns_fresh_verdict():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="sma", samples=_fresh_samples(),
    ))
    assert r.verdict_overall is FreshnessVerdict.FRESH
    assert r.decay_score >= 70.0


def test_decaying_signal_returns_decaying_verdict():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="vwap", samples=_decaying_samples(),
    ))
    assert r.verdict_overall is FreshnessVerdict.DECAYING


def test_expired_signal_returns_expired_or_stale_verdict():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="orb", samples=_expired_samples(),
    ))
    # decay_score 가 매우 낮음 — EXPIRED 또는 STALE.
    assert r.verdict_overall in (
        FreshnessVerdict.EXPIRED, FreshnessVerdict.STALE,
    )


def test_decay_score_decreases_with_more_decay():
    fresh = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="a", samples=_fresh_samples(),
    ))
    decaying = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="b", samples=_decaying_samples(),
    ))
    expired = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="c", samples=_expired_samples(),
    ))
    assert fresh.decay_score > decaying.decay_score
    assert decaying.decay_score >= expired.decay_score


def test_buckets_include_relative_to_t0_pct():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=_fresh_samples(),
    ))
    # t=0 bucket 의 relative_to_t0_pct 는 100.
    t0_bucket = next(b for b in r.buckets if b.age_minutes == 0)
    assert abs(t0_bucket.relative_to_t0_pct - 100.0) < 0.01
    # 더 늦은 bucket 은 100 이하.
    later = next(b for b in r.buckets if b.age_minutes == 60)
    assert later.relative_to_t0_pct < t0_bucket.relative_to_t0_pct


def test_buckets_have_severity_assigned():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=_expired_samples(),
    ))
    # FAIL severity 가 적어도 1개 존재.
    assert any(b.severity is DecaySeverity.FAIL for b in r.buckets)


# ====================================================================
# insufficient data / edge cases
# ====================================================================


def test_empty_samples_returns_unknown():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=(),
    ))
    assert r.verdict_overall is FreshnessVerdict.UNKNOWN
    assert r.insufficient_data is True
    assert r.buckets == []


def test_small_t0_sample_count_returns_unknown():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=(
            SignalSamplePoint(age_minutes=0, mean_return_bps=10.0, sample_count=3),
        ),
    ))
    assert r.insufficient_data is True
    assert r.verdict_overall is FreshnessVerdict.UNKNOWN


def test_zero_t0_return_returns_unknown():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=(
            SignalSamplePoint(age_minutes=0, mean_return_bps=0.0, sample_count=100),
            SignalSamplePoint(age_minutes=5, mean_return_bps=5.0, sample_count=100),
        ),
    ))
    assert r.insufficient_data is True
    assert r.verdict_overall is FreshnessVerdict.UNKNOWN


def test_low_sample_count_bucket_marked_warn():
    samples = (
        SignalSamplePoint(age_minutes=0,  mean_return_bps=20.0, sample_count=100),
        SignalSamplePoint(age_minutes=5,  mean_return_bps=19.0, sample_count=3),  # 작은 표본
    )
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=samples,
    ))
    low_bucket = next(b for b in r.buckets if b.age_minutes == 5)
    assert "표본" in low_bucket.note
    # 본래는 PASS 였지만 표본 부족으로 WARN 격하.
    assert low_bucket.severity is DecaySeverity.WARN


# ====================================================================
# realtime helpers
# ====================================================================


def test_compute_signal_age_minutes_basic():
    now = datetime(2026, 5, 15, 10, 30, 0, tzinfo=timezone.utc)
    sig = datetime(2026, 5, 15, 10, 20, 0, tzinfo=timezone.utc)  # 10분 전
    assert compute_signal_age_minutes(sig, now) == 10


def test_compute_signal_age_minutes_clamps_negative_to_zero():
    """미래 시각이 입력되면 0 으로 clamp."""
    now = datetime(2026, 5, 15, 10, 30, 0, tzinfo=timezone.utc)
    sig = datetime(2026, 5, 15, 10, 40, 0, tzinfo=timezone.utc)  # 미래
    assert compute_signal_age_minutes(sig, now) == 0


def test_compute_signal_age_minutes_handles_naive_datetime():
    """naive datetime 입력도 처리 (UTC 가정)."""
    now = datetime(2026, 5, 15, 10, 30, 0)  # naive
    sig = datetime(2026, 5, 15, 10, 25, 0)  # naive
    assert compute_signal_age_minutes(sig, now) == 5


def test_freshness_verdict_for_age_fresh():
    assert freshness_verdict_for_age(0) is FreshnessVerdict.FRESH
    assert freshness_verdict_for_age(1) is FreshnessVerdict.FRESH


def test_freshness_verdict_for_age_decaying():
    assert freshness_verdict_for_age(5) is FreshnessVerdict.DECAYING
    assert freshness_verdict_for_age(30) is FreshnessVerdict.DECAYING


def test_freshness_verdict_for_age_stale():
    assert freshness_verdict_for_age(45) is FreshnessVerdict.STALE
    assert freshness_verdict_for_age(60) is FreshnessVerdict.STALE


def test_freshness_verdict_for_age_expired():
    assert freshness_verdict_for_age(61) is FreshnessVerdict.EXPIRED
    assert freshness_verdict_for_age(180) is FreshnessVerdict.EXPIRED


def test_is_signal_actionable_default_allows_decaying_and_stale():
    """default strict=False — STALE 까지는 actionable=True (단지 경고)."""
    assert is_signal_actionable(1) is True
    assert is_signal_actionable(15) is True
    assert is_signal_actionable(45) is True
    # EXPIRED 만 차단.
    assert is_signal_actionable(120) is False


def test_is_signal_actionable_strict_blocks_stale():
    """strict=True — STALE 도 차단."""
    assert is_signal_actionable(1, strict=True) is True
    assert is_signal_actionable(15, strict=True) is True
    assert is_signal_actionable(45, strict=True) is False  # STALE
    assert is_signal_actionable(120, strict=True) is False


# ====================================================================
# markdown
# ====================================================================


def test_markdown_contains_disclaimer_and_verdict():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="sma", samples=_fresh_samples(),
    ))
    text = render_markdown_report(r)
    assert "Signal Alpha Decay" in text
    assert "FRESH" in text
    assert "advisory" in text
    assert "broker / OrderExecutor" in text


def test_markdown_no_buy_sell_hold_or_place_order():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=_expired_samples(),
    ))
    text = render_markdown_report(r)
    for banned in ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                   "HOLD signal", "Place Order", "실거래 시작", "지금 매수",
                   "지금 매도"]:
        assert banned not in text


def test_markdown_no_secret_patterns():
    r = evaluate_signal_alpha_decay(SignalAlphaDecayInput(
        strategy_name="x", samples=_fresh_samples(),
    ))
    text = render_markdown_report(r).lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "telegram_bot_token=", "sk-", "bearer ",
    ]:
        assert needle not in text


# ====================================================================
# API
# ====================================================================


def test_api_alpha_decay_evaluate_returns_invariants(client):
    body = {
        "strategy_name": "sma_crossover",
        "samples": [
            {"age_minutes": 0,  "mean_return_bps": 20.0, "sample_count": 100},
            {"age_minutes": 1,  "mean_return_bps": 19.0, "sample_count": 100},
            {"age_minutes": 5,  "mean_return_bps": 18.0, "sample_count": 100},
            {"age_minutes": 30, "mean_return_bps": 15.0, "sample_count": 100},
        ],
    }
    res = client.post("/api/analytics/alpha-decay/evaluate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["strategy_name"] == "sma_crossover"
    assert data["is_order_signal"] is False
    assert data["auto_apply_allowed"] is False
    assert data["is_live_authorization"] is False
    assert data["verdict_overall"] in ("FRESH", "DECAYING", "STALE", "EXPIRED",
                                       "UNKNOWN")


def test_api_alpha_decay_evaluate_with_empty_samples_returns_unknown(client):
    body = {"strategy_name": "x", "samples": []}
    res = client.post("/api/analytics/alpha-decay/evaluate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["verdict_overall"] == "UNKNOWN"
    assert data["insufficient_data"] is True


def test_api_alpha_decay_freshness_endpoint(client):
    # FRESH
    res = client.get("/api/analytics/alpha-decay/freshness?age_minutes=0")
    assert res.status_code == 200, res.text
    assert res.json()["verdict"] == "FRESH"
    assert res.json()["actionable"] is True
    assert res.json()["is_order_signal"] is False
    # STALE
    res = client.get("/api/analytics/alpha-decay/freshness?age_minutes=45")
    assert res.json()["verdict"] == "STALE"
    assert res.json()["actionable"] is True
    assert res.json()["actionable_strict"] is False
    # EXPIRED
    res = client.get("/api/analytics/alpha-decay/freshness?age_minutes=120")
    assert res.json()["verdict"] == "EXPIRED"
    assert res.json()["actionable"] is False


def test_api_alpha_decay_does_not_leak_secrets(client):
    body = {
        "strategy_name": "x",
        "samples": [
            {"age_minutes": 0, "mean_return_bps": 10.0, "sample_count": 100},
        ],
    }
    res = client.post("/api/analytics/alpha-decay/evaluate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
        "sk-", "bearer ",
    ]:
        assert needle not in text


def test_api_alpha_decay_rejects_invalid_input(client):
    body = {"strategy_name": ""}  # empty 위반.
    res = client.post("/api/analytics/alpha-decay/evaluate", json=body)
    # Pydantic 422 또는 HTTPException 400.
    assert res.status_code in (400, 422)


# ====================================================================
# invariants — static grep guards
# ====================================================================


_MODULE_PATH = Path("backend/app/analytics/signal_alpha_decay.py")


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
