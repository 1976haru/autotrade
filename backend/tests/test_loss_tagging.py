"""Loss Tagging (#79) — tagger + storage + API + invariants."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analytics.loss_tagging import (
    LossEstimateInput,
    LossEstimateResult,
    LossReasonCategory,
    LossReasonTag,
    category_of,
    estimate_loss_reasons,
    summarize_for_daily_report,
    summarize_for_strategy_researcher,
    summarize_tag_counts,
)
from app.analytics.loss_tagging_storage import (
    append_loss_reason_log,
    list_recent_loss_reasons,
    review_loss_reason_log,
    summarize_loss_reasons,
)


def _inp(**kw) -> LossEstimateInput:
    defaults = dict(
        symbol="005930", side="BUY",
        entry_price=70_000.0, exit_price=68_000.0, quantity=10,
    )
    defaults.update(kw)
    return LossEstimateInput(**defaults)


# ---------- DTO invariants ----------


def test_result_rejects_is_estimated_false():
    with pytest.raises(ValueError, match="is_estimated"):
        LossEstimateResult(
            symbol="x", is_loss=True, trade_pnl=-100,
            is_estimated=False,
        )


def test_result_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        LossEstimateResult(
            symbol="x", is_loss=True, trade_pnl=-100,
            is_order_signal=True,
        )


def test_result_rejects_investment_advice_true():
    with pytest.raises(ValueError, match="is_investment_advice"):
        LossEstimateResult(
            symbol="x", is_loss=True, trade_pnl=-100,
            is_investment_advice=True,
        )


def test_to_dict_has_invariant_flags():
    r = estimate_loss_reasons(_inp())
    d = r.to_dict()
    assert d["is_estimated"] is True
    assert d["is_order_signal"] is False
    assert d["is_investment_advice"] is False
    assert d["live_flag_changed"] is False
    assert d["mode_changed"] is False


# ---------- is_loss ----------


def test_non_loss_trade_returns_empty_tags():
    r = estimate_loss_reasons(_inp(entry_price=70_000, exit_price=72_000))
    assert r.is_loss is False
    assert r.tags == []
    assert r.primary_tag is None


def test_loss_with_minimal_input_yields_unknown():
    r = estimate_loss_reasons(_inp())
    assert r.is_loss is True
    assert r.tags == [LossReasonTag.UNKNOWN]
    assert r.primary_tag is LossReasonTag.UNKNOWN


def test_sell_short_trade_pnl_inverse():
    # short: entry > exit 이면 이익. entry < exit 이면 손실.
    r = estimate_loss_reasons(
        _inp(side="SELL", entry_price=70_000, exit_price=72_000),
    )
    assert r.is_loss is True   # short인데 가격이 올랐으므로 손실
    r2 = estimate_loss_reasons(
        _inp(side="SELL", entry_price=72_000, exit_price=70_000),
    )
    assert r2.is_loss is False


# ---------- strategy / pattern tags ----------


def test_stop_loss_hit_detected():
    r = estimate_loss_reasons(_inp(
        entry_price=70_000, exit_price=68_500, stop_price=68_500,
    ))
    assert LossReasonTag.STOP_LOSS_HIT in r.tags


def test_failed_breakout_detected():
    r = estimate_loss_reasons(_inp(failed_breakout_pattern=True))
    assert LossReasonTag.FAILED_BREAKOUT in r.tags


def test_false_rebreak_detected():
    r = estimate_loss_reasons(_inp(false_rebreak_pattern=True))
    assert LossReasonTag.FALSE_REBREAK in r.tags


def test_vwap_loss_detected():
    r = estimate_loss_reasons(_inp(
        entry_price=70_000, exit_price=68_000, entry_vwap=69_500,
    ))
    assert LossReasonTag.VWAP_LOSS in r.tags


def test_target_not_reached_detected():
    r = estimate_loss_reasons(_inp(
        entry_price=70_000, exit_price=68_000, target_price=72_000,
    ))
    assert LossReasonTag.TARGET_NOT_REACHED in r.tags


def test_time_stop_detected():
    r = estimate_loss_reasons(_inp(
        hold_minutes=240, time_stop_threshold_minutes=180,
    ))
    assert LossReasonTag.TIME_STOP in r.tags


def test_reverse_signal_detected():
    r = estimate_loss_reasons(_inp(reverse_signal_at_exit=True))
    assert LossReasonTag.REVERSAL_SIGNAL in r.tags


# ---------- execution tags ----------


def test_low_liquidity_detected():
    r = estimate_loss_reasons(_inp(entry_volume=100_000, exit_volume=10_000))
    assert LossReasonTag.LOW_LIQUIDITY in r.tags


def test_high_slippage_detected():
    r = estimate_loss_reasons(_inp(slippage_bps=120.0))
    assert LossReasonTag.HIGH_SLIPPAGE in r.tags


def test_partial_fill_detected():
    r = estimate_loss_reasons(_inp(partial_fill_ratio=0.5))
    assert LossReasonTag.PARTIAL_FILL in r.tags


def test_price_gap_detected():
    r = estimate_loss_reasons(_inp(gap_ratio=-0.05))
    assert LossReasonTag.PRICE_GAP in r.tags


# ---------- market tags ----------


def test_market_selloff_detected():
    r = estimate_loss_reasons(_inp(kospi_return=-0.025))
    assert LossReasonTag.MARKET_SELLOFF in r.tags


def test_sector_drop_detected():
    r = estimate_loss_reasons(_inp(sector_return=-0.04))
    assert LossReasonTag.SECTOR_DROP in r.tags


def test_regime_change_detected():
    r = estimate_loss_reasons(_inp(
        regime_at_entry="trend_up", regime_at_exit="range_bound",
    ))
    assert LossReasonTag.REGIME_CHANGE in r.tags


def test_volatility_spike_detected():
    r = estimate_loss_reasons(_inp(volatility_pct=0.08))
    assert LossReasonTag.VOLATILITY_SPIKE in r.tags


# ---------- risk tags ----------


def test_emergency_stop_detected():
    r = estimate_loss_reasons(_inp(emergency_stop_active=True))
    assert LossReasonTag.EMERGENCY_STOP in r.tags
    # primary 우선순위: risk > 기타.
    assert r.primary_tag is LossReasonTag.EMERGENCY_STOP


def test_risk_limit_hit_detected():
    r = estimate_loss_reasons(_inp(daily_loss_limit_breached=True))
    assert LossReasonTag.RISK_LIMIT_HIT in r.tags


def test_over_exposure_detected():
    r = estimate_loss_reasons(_inp(over_exposure=True))
    assert LossReasonTag.OVER_EXPOSURE in r.tags


# ---------- data tags ----------


def test_data_stale_detected():
    r = estimate_loss_reasons(_inp(data_stale_at_entry=True))
    assert LossReasonTag.DATA_STALE in r.tags


def test_bad_quote_detected():
    r = estimate_loss_reasons(_inp(bad_quote_count=3))
    assert LossReasonTag.BAD_QUOTE in r.tags


def test_missing_bar_detected():
    r = estimate_loss_reasons(_inp(missing_bar_count=2))
    assert LossReasonTag.MISSING_BAR in r.tags


# ---------- agent tags ----------


def test_ai_overconfidence_detected():
    r = estimate_loss_reasons(_inp(ai_entry_confidence=90))
    assert LossReasonTag.AI_OVERCONFIDENCE in r.tags


def test_ai_low_confidence_detected():
    r = estimate_loss_reasons(_inp(ai_entry_confidence=30))
    assert LossReasonTag.AI_LOW_CONFIDENCE in r.tags


def test_news_theme_faded_detected():
    r = estimate_loss_reasons(_inp(
        news_theme_active_at_entry=True, news_theme_faded_at_exit=True,
    ))
    assert LossReasonTag.NEWS_THEME_FADED in r.tags


# ---------- multiple tags / priority ----------


def test_multiple_tags_collected():
    r = estimate_loss_reasons(_inp(
        stop_price=68_000, exit_price=68_000,
        kospi_return=-0.025, slippage_bps=80.0,
        ai_entry_confidence=85,
    ))
    assert LossReasonTag.STOP_LOSS_HIT in r.tags
    assert LossReasonTag.MARKET_SELLOFF in r.tags
    assert LossReasonTag.HIGH_SLIPPAGE in r.tags
    assert LossReasonTag.AI_OVERCONFIDENCE in r.tags
    # primary 는 risk (없으면 data > market > execution > strategy > agent).
    assert category_of(r.primary_tag) is LossReasonCategory.MARKET


def test_primary_tag_priority_risk_over_market():
    r = estimate_loss_reasons(_inp(
        emergency_stop_active=True,
        kospi_return=-0.025,
    ))
    assert r.primary_tag is LossReasonTag.EMERGENCY_STOP


def test_confidence_grows_with_tag_count():
    r1 = estimate_loss_reasons(_inp())  # unknown only
    r2 = estimate_loss_reasons(_inp(
        stop_price=68_000, exit_price=68_000,
        slippage_bps=80.0, kospi_return=-0.025,
    ))
    assert r2.confidence > r1.confidence


# ---------- summary helpers ----------


def test_summarize_tag_counts_orders_by_count():
    results = [
        estimate_loss_reasons(_inp(stop_price=68_000, exit_price=68_000))
        for _ in range(3)
    ] + [
        estimate_loss_reasons(_inp(slippage_bps=80.0)),
    ]
    rows = summarize_tag_counts(results)
    assert rows[0].tag == "stop_loss_hit"
    assert rows[0].count == 3


def test_summarize_for_daily_report_includes_disclaimer():
    results = [estimate_loss_reasons(_inp(slippage_bps=80.0))]
    summary = summarize_for_daily_report(results)
    assert summary["is_estimated"] is True
    assert "추정" in summary["note"]


def test_summarize_for_daily_report_empty_when_no_losses():
    summary = summarize_for_daily_report([
        estimate_loss_reasons(_inp(entry_price=70_000, exit_price=72_000)),
    ])
    assert summary["loss_count"] == 0
    assert summary["top_tags"] == []


def test_summarize_for_strategy_researcher_filters_repeated():
    results = [
        estimate_loss_reasons(_inp(stop_price=68_000, exit_price=68_000)),
        estimate_loss_reasons(_inp(stop_price=68_000, exit_price=68_000)),
        estimate_loss_reasons(_inp(slippage_bps=80.0)),
    ]
    summary = summarize_for_strategy_researcher(results)
    tags = {row["tag"] for row in summary["repeated_tags"]}
    assert "stop_loss_hit" in tags
    assert "high_slippage" not in tags  # 단일 발생은 제외


# ---------- storage ----------


def test_append_loss_reason_log_persists_row(client):
    db = client.test_db_factory()
    try:
        result = estimate_loss_reasons(_inp(slippage_bps=80.0))
        row = append_loss_reason_log(
            db, result,
            source_table="manual", source_id=1, strategy="sma_cross", mode="PAPER",
        )
        assert row is not None
        assert row.primary_tag == "high_slippage"
        assert row.is_estimated is True
        assert row.review_status is None
    finally:
        db.close()


def test_append_loss_reason_log_skips_non_loss(client):
    db = client.test_db_factory()
    try:
        result = estimate_loss_reasons(_inp(entry_price=70_000, exit_price=72_000))
        row = append_loss_reason_log(
            db, result, source_table="manual",
        )
        assert row is None
    finally:
        db.close()


def test_review_updates_only_review_fields(client):
    db = client.test_db_factory()
    try:
        result = estimate_loss_reasons(_inp(slippage_bps=80.0))
        row = append_loss_reason_log(db, result, source_table="manual")
        original_tags = list(row.tags or [])
        original_pnl  = row.trade_pnl

        updated = review_loss_reason_log(
            db, log_id=row.id,
            review_status="agreed",
            reviewed_by="operator",
            review_note="가격 갭에서 슬리피지 발생 확인",
        )
        assert updated is not None
        assert updated.review_status == "agreed"
        assert updated.reviewed_by == "operator"
        # 원본 추정 데이터는 변경되지 않는다.
        assert list(updated.tags or []) == original_tags
        assert updated.trade_pnl == original_pnl
        assert updated.is_estimated is True
    finally:
        db.close()


def test_review_returns_none_for_missing_id(client):
    db = client.test_db_factory()
    try:
        out = review_loss_reason_log(
            db, log_id=999, review_status="agreed",
        )
        assert out is None
    finally:
        db.close()


def test_list_recent_loss_reasons(client):
    db = client.test_db_factory()
    try:
        for _ in range(3):
            r = estimate_loss_reasons(_inp(slippage_bps=80.0))
            append_loss_reason_log(db, r, source_table="manual")
        rows = list_recent_loss_reasons(db, limit=10)
        assert len(rows) == 3
        # 손실 row 만.
        assert all(r.is_loss for r in rows)
    finally:
        db.close()


def test_summarize_loss_reasons_groups_by_category(client):
    db = client.test_db_factory()
    try:
        # 손익이 -20000 모두 동일.
        append_loss_reason_log(
            db, estimate_loss_reasons(_inp(slippage_bps=80.0)),
            source_table="manual", strategy="sma_cross",
        )
        append_loss_reason_log(
            db, estimate_loss_reasons(_inp(kospi_return=-0.025)),
            source_table="manual", strategy="sma_cross",
        )
        summary = summarize_loss_reasons(db, days=30)
        assert summary["loss_count"] == 2
        assert summary["is_estimated"] is True
        assert "추정" in summary["note"]
        cats = summary["by_category"]
        # market 1 + execution 1 (primary)
        assert sum(cats.values()) == 2
        # by_strategy 통계.
        assert summary["by_strategy"][0]["strategy"] == "sma_cross"
    finally:
        db.close()


# ---------- API ----------


def test_route_estimate_returns_result(client):
    body = {
        "symbol": "005930", "side": "BUY",
        "entry_price": 70000, "exit_price": 68000, "quantity": 10,
        "slippage_bps": 80.0, "kospi_return": -0.025,
    }
    res = client.post("/api/analytics/loss-tags/estimate", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["is_loss"] is True
    assert "high_slippage" in data["tags"]
    assert "market_selloff" in data["tags"]
    assert data["is_estimated"] is True
    assert data["is_order_signal"] is False
    assert data["is_investment_advice"] is False
    # persist=false → row 생성 X.
    assert data["persisted_log_id"] is None


def test_route_estimate_with_persist(client):
    body = {
        "symbol": "005930", "side": "BUY",
        "entry_price": 70000, "exit_price": 68000, "quantity": 10,
        "slippage_bps": 80.0,
        "persist": True, "source_table": "manual", "strategy": "sma_cross",
    }
    res = client.post("/api/analytics/loss-tags/estimate", json=body)
    assert res.status_code == 200
    data = res.json()
    assert data["persisted_log_id"] is not None


def test_route_summary(client):
    # 먼저 row 하나 persist.
    body = {
        "symbol": "005930", "side": "BUY",
        "entry_price": 70000, "exit_price": 68000, "quantity": 10,
        "slippage_bps": 80.0, "persist": True, "source_table": "manual",
    }
    client.post("/api/analytics/loss-tags/estimate", json=body)
    res = client.get("/api/analytics/loss-tags/summary?days=30")
    assert res.status_code == 200
    data = res.json()
    assert data["is_estimated"] is True
    assert data["loss_count"] >= 1


def test_route_recent_and_review(client):
    body = {
        "symbol": "005930", "side": "BUY",
        "entry_price": 70000, "exit_price": 68000, "quantity": 10,
        "slippage_bps": 80.0, "persist": True, "source_table": "manual",
    }
    create_res = client.post("/api/analytics/loss-tags/estimate", json=body)
    log_id = create_res.json()["persisted_log_id"]

    recent_res = client.get("/api/analytics/loss-tags/recent?limit=10")
    assert recent_res.status_code == 200
    items = recent_res.json()["items"]
    assert any(it["id"] == log_id for it in items)

    review_res = client.patch(
        f"/api/analytics/loss-tags/{log_id}/review",
        json={"review_status": "agreed", "reviewed_by": "ops",
              "review_note": "확인됨"},
    )
    assert review_res.status_code == 200
    assert review_res.json()["review_status"] == "agreed"


def test_route_review_404_for_missing_id(client):
    res = client.patch(
        "/api/analytics/loss-tags/99999/review",
        json={"review_status": "agreed"},
    )
    assert res.status_code == 404


def test_route_response_does_not_leak_secrets(client):
    body = {
        "symbol": "x", "side": "BUY",
        "entry_price": 100, "exit_price": 90, "quantity": 1,
    }
    res = client.post("/api/analytics/loss-tags/estimate", json=body)
    text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "anthropic_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in text


def test_no_delete_endpoint_exposed(client):
    """삭제 API 0건 — DELETE 시도는 405."""
    res = client.delete("/api/analytics/loss-tags/1")
    # FastAPI는 등록되지 않은 메서드는 405 또는 404를 반환.
    assert res.status_code in (404, 405)


# ---------- invariants — static grep guards ----------


_MODULE_PATHS = [
    Path("backend/app/analytics/__init__.py"),
    Path("backend/app/analytics/loss_tagging.py"),
    Path("backend/app/analytics/loss_tagging_storage.py"),
    Path("backend/app/api/routes_analytics.py"),
]


def _resolve(p: Path) -> Path:
    return p if p.exists() else Path(__file__).resolve().parents[2] / p


def test_modules_do_not_import_broker_or_executor():
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
    for rel in _MODULE_PATHS:
        src = _resolve(rel).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, f"{rel} imports forbidden: {needle!r}"


def test_modules_do_not_call_broker_or_route_order():
    forbidden = [
        "broker.place_order(",
        "broker.cancel_order(",
        "route_order(",
        ".execute_order(",
        "OrderExecutor(",
        "submit_candidate(",
        "AiClient(",
    ]
    for rel in _MODULE_PATHS:
        src = _resolve(rel).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, f"{rel} contains forbidden call: {needle!r}"


def test_modules_do_not_mutate_safety_flags():
    forbidden = [
        "settings.enable_live_trading =",
        "settings.enable_ai_execution =",
        "settings.enable_futures_live_trading =",
        "setattr(settings, \"enable_",
        "os.environ[\"ENABLE_LIVE_TRADING\"]",
        "os.environ[\"ENABLE_AI_EXECUTION\"]",
        "os.environ[\"ENABLE_FUTURES_LIVE_TRADING\"]",
    ]
    for rel in _MODULE_PATHS:
        src = _resolve(rel).read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in src, f"{rel} mutates safety flag: {needle!r}"


def test_evaluator_module_does_not_write_to_db():
    """evaluator 자체는 read-only — storage 모듈만 db.add/db.commit 호출."""
    src = _resolve(Path("backend/app/analytics/loss_tagging.py")).read_text(encoding="utf-8")
    forbidden = [
        "db.add(", "db.add_all(", "db.commit(", "db.flush(",
        "db.delete(", "db.merge(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for needle in forbidden:
        assert needle not in src, f"loss_tagging.py writes to DB: {needle!r}"


def test_storage_does_not_have_row_delete_path():
    """append + review 만 — delete 경로 0개."""
    src = _resolve(
        Path("backend/app/analytics/loss_tagging_storage.py"),
    ).read_text(encoding="utf-8")
    forbidden = [
        "db.delete(", "DELETE FROM", ".delete(",
    ]
    for needle in forbidden:
        assert needle not in src, f"storage has delete path: {needle!r}"


def test_routes_does_not_have_delete_endpoint():
    """DELETE 메서드 / @router.delete 없음."""
    src = _resolve(
        Path("backend/app/api/routes_analytics.py"),
    ).read_text(encoding="utf-8")
    assert "@router.delete" not in src
    assert ".delete(" not in src.lower()
