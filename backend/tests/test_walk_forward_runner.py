"""Walk-forward runner 테스트 (#25)."""

import pytest
from datetime import datetime, timedelta, timezone

from app.backtest.types import Bar, Signal
from app.backtest.walk_forward_runner import (
    WalkForwardConfig,
    WalkForwardFoldResult,
    WalkForwardResult,
    WalkForwardWindow,
    build_walk_forward_windows,
    run_walk_forward,
)
from app.strategies.base import Strategy


_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(day: int, close: int = 100) -> Bar:
    return Bar(
        symbol="TEST",
        timestamp=_BASE + timedelta(days=day),
        open=close, high=close + 5, low=max(1, close - 5), close=close, volume=1000,
    )


# ---------- WalkForwardConfig ----------


def test_config_step_days_defaults_to_validation_days():
    cfg = WalkForwardConfig(train_days=60, validation_days=20, step_days=0)
    assert cfg.step_days == 20


def test_config_rejects_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        WalkForwardConfig(mode="bogus")


def test_config_rejects_invalid_ratios():
    with pytest.raises(ValueError):
        WalkForwardConfig(min_positive_fold_ratio=1.5)
    with pytest.raises(ValueError):
        WalkForwardConfig(max_single_fold_pnl_share=-0.1)


def test_config_rejects_nonpositive_train_or_validation():
    with pytest.raises(ValueError):
        WalkForwardConfig(train_days=0)
    with pytest.raises(ValueError):
        WalkForwardConfig(validation_days=0)


# ---------- build_walk_forward_windows ----------


def test_build_windows_empty_when_range_invalid():
    cfg = WalkForwardConfig(train_days=10, validation_days=5, holdout_days=0)
    windows, holdout = build_walk_forward_windows(
        start=_BASE, end=_BASE - timedelta(days=1), config=cfg,
    )
    assert windows == []
    assert holdout is None


def test_build_windows_rolling_basic():
    """200일 데이터 / train 60 / val 20 / step 20 / holdout 30 → 5 fold."""
    cfg = WalkForwardConfig(
        mode="rolling", train_days=60, validation_days=20,
        step_days=20, holdout_days=30,
    )
    end = _BASE + timedelta(days=200)
    windows, holdout = build_walk_forward_windows(start=_BASE, end=end, config=cfg)

    assert holdout is not None
    holdout_start, holdout_end = holdout
    assert holdout_end == end
    assert (holdout_end - holdout_start).days == 30

    # walk 영역 = end - 30일(holdout) = 170일.
    # train(60) + val(20) = 80일. cursor 시작 0,20,40,...
    # cursor + 80 ≤ 170 → cursor ≤ 90 → cursor ∈ {0,20,40,60,80} → 5 fold.
    assert len(windows) == 5
    assert windows[0].fold_index == 0
    # rolling — train_start이 cursor를 따라 이동.
    assert windows[0].train_start == _BASE
    assert windows[1].train_start == _BASE + timedelta(days=20)


def test_build_windows_anchored_keeps_train_start_fixed():
    cfg = WalkForwardConfig(
        mode="anchored", train_days=60, validation_days=20,
        step_days=20, holdout_days=0,
    )
    end = _BASE + timedelta(days=200)
    windows, _ = build_walk_forward_windows(start=_BASE, end=end, config=cfg)
    # anchored — 모든 fold의 train_start이 _BASE.
    assert all(w.train_start == _BASE for w in windows)
    # train_end만 늘어남.
    train_ends = [w.train_end for w in windows]
    assert train_ends == sorted(train_ends)


def test_build_windows_holdout_separated():
    cfg = WalkForwardConfig(train_days=10, validation_days=5,
                            step_days=5, holdout_days=20)
    end = _BASE + timedelta(days=80)
    windows, holdout = build_walk_forward_windows(start=_BASE, end=end, config=cfg)
    assert holdout is not None
    # 모든 valid_end가 holdout 시작 이전.
    for w in windows:
        assert w.valid_end <= holdout[0]


def test_build_windows_holdout_too_large_returns_empty():
    """holdout이 전체 범위보다 크면 walk-forward 불가."""
    cfg = WalkForwardConfig(train_days=10, validation_days=5,
                            step_days=5, holdout_days=100)
    end = _BASE + timedelta(days=50)
    windows, holdout = build_walk_forward_windows(start=_BASE, end=end, config=cfg)
    assert windows == []
    assert holdout is None


def test_build_windows_too_short_returns_empty():
    """train+validation보다 짧은 데이터 → fold 0개."""
    cfg = WalkForwardConfig(train_days=60, validation_days=20, holdout_days=0)
    windows, _ = build_walk_forward_windows(
        start=_BASE, end=_BASE + timedelta(days=50), config=cfg,
    )
    assert windows == []


# ---------- WalkForwardResult metrics ----------


def _fold(idx: int, *, train_pnl: int, val_pnl: int) -> WalkForwardFoldResult:
    w = WalkForwardWindow(
        fold_index=idx,
        train_start=_BASE, train_end=_BASE + timedelta(days=10),
        valid_start=_BASE + timedelta(days=10),
        valid_end=_BASE + timedelta(days=15),
    )
    return WalkForwardFoldResult(
        window=w,
        train_metrics={"total_pnl": train_pnl},
        validation_metrics={"total_pnl": val_pnl},
        validation_bar_count=5,
    )


def test_positive_fold_ratio():
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=-10),
               _fold(2, train_pnl=100, val_pnl=20)],
    )
    assert abs(r.positive_fold_ratio() - (2/3)) < 1e-9


def test_single_best_fold_pnl_share():
    """500이 전체 양수 합 600 중 차지하는 비율 = 0.833."""
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=100, val_pnl=500),
               _fold(1, train_pnl=100, val_pnl=100),
               _fold(2, train_pnl=100, val_pnl=-50)],
    )
    share = r.single_best_fold_pnl_share()
    assert share is not None
    assert abs(share - (500/600)) < 1e-9


def test_single_best_fold_pnl_share_none_when_no_positives():
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=10, val_pnl=-50),
               _fold(1, train_pnl=10, val_pnl=-30)],
    )
    assert r.single_best_fold_pnl_share() is None


def test_overfit_risk_score_when_train_dominates():
    """train ≫ validation → overfit 의심."""
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=1000, val_pnl=10),
               _fold(1, train_pnl=1000, val_pnl=20)],
    )
    assert r.overfit_risk_score() > 50.0


def test_overfit_risk_score_zero_when_train_is_loss():
    """train이 손실이면 overfit 측정 무의미 → 0."""
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=-100, val_pnl=-50)],
    )
    assert r.overfit_risk_score() == 0.0


def test_stability_score_is_positive_fold_ratio_times_100():
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=20)],
    )
    assert r.stability_score() == 100.0


# ---------- promotion recommendation ----------


def test_recommendation_fail_when_too_few_folds():
    r = WalkForwardResult(
        config=WalkForwardConfig(min_fold_count=3),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=30)],
    )
    assert r.promotion_recommendation() == "FAIL"


def test_recommendation_fail_when_holdout_loss():
    r = WalkForwardResult(
        config=WalkForwardConfig(min_fold_count=2),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=30)],
        holdout_metrics={"total_pnl": -1000},
        holdout_window={"start": "X", "end": "Y", "bar_count": 10},
    )
    assert r.promotion_recommendation() == "FAIL"


def test_recommendation_caution_on_high_single_fold_share():
    """한 fold가 95% 차지 → CAUTION."""
    r = WalkForwardResult(
        config=WalkForwardConfig(
            min_fold_count=3, min_positive_fold_ratio=0.5,
            max_single_fold_pnl_share=0.7,
        ),
        folds=[_fold(0, train_pnl=100, val_pnl=950),
               _fold(1, train_pnl=100, val_pnl=30),
               _fold(2, train_pnl=100, val_pnl=20)],
    )
    rec = r.promotion_recommendation()
    assert rec == "CAUTION"


def test_recommendation_pass_when_all_criteria_met():
    r = WalkForwardResult(
        config=WalkForwardConfig(
            min_fold_count=3, min_positive_fold_ratio=0.6,
            max_single_fold_pnl_share=0.7,
        ),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=70),
               _fold(2, train_pnl=100, val_pnl=80)],
        holdout_metrics={"total_pnl": 100},
    )
    assert r.promotion_recommendation() == "PASS"


def test_warnings_when_too_few_folds():
    r = WalkForwardResult(
        config=WalkForwardConfig(min_fold_count=5),
        folds=[_fold(0, train_pnl=100, val_pnl=50),
               _fold(1, train_pnl=100, val_pnl=30)],
    )
    msgs = r.warnings()
    assert any("표본 부족" in m for m in msgs)


def test_overfit_flags_when_train_high_validation_low():
    r = WalkForwardResult(
        config=WalkForwardConfig(min_positive_fold_ratio=0.6),
        folds=[_fold(0, train_pnl=1000, val_pnl=-10),
               _fold(1, train_pnl=1000, val_pnl=-20)],
    )
    assert any("overfit" in f.lower() for f in r.overfit_flags())


def test_to_dict_is_json_serializable():
    import json
    r = WalkForwardResult(
        config=WalkForwardConfig(),
        folds=[_fold(0, train_pnl=100, val_pnl=50)],
        holdout_metrics={"total_pnl": 30},
        holdout_window={"start": "2026-01-01T00:00:00", "end": "2026-01-31T00:00:00", "bar_count": 30},
    )
    d = r.to_dict()
    json.dumps(d)
    assert d["promotion_recommendation"] in ("PASS", "CAUTION", "FAIL")
    assert "summary" in d


# ---------- run_walk_forward (engine 통합) ----------


class _AlwaysBuyOnce(Strategy):
    """첫 봉에 BUY, 두 번째 봉에 SELL — fold마다 같은 패턴 반복."""
    def __init__(self):
        self._idx = 0

    def on_bar(self, bars):
        self._idx += 1
        if self._idx == 1:
            return Signal.BUY
        if self._idx == 2:
            return Signal.SELL
        return Signal.HOLD


def test_run_walk_forward_executes_each_fold():
    bars = [_bar(d, close=100 + d) for d in range(120)]
    cfg = WalkForwardConfig(
        train_days=20, validation_days=10, step_days=10, holdout_days=20,
        min_fold_count=2, min_positive_fold_ratio=0.0,
    )
    result = run_walk_forward(
        bars=bars,
        strategy_factory=_AlwaysBuyOnce,
        walk_forward_config=cfg,
        initial_cash=1_000_000, quantity=1,
    )
    assert result.fold_count() >= 2
    # holdout이 분리됨.
    assert result.holdout_metrics is not None
    # JSON 직렬화 가능.
    import json
    json.dumps(result.to_dict())


def test_run_walk_forward_empty_bars_returns_empty():
    cfg = WalkForwardConfig()
    r = run_walk_forward(
        bars=[],
        strategy_factory=_AlwaysBuyOnce,
        walk_forward_config=cfg,
    )
    assert r.fold_count() == 0


def test_run_walk_forward_strategy_factory_creates_fresh_instance():
    """fold마다 strategy 상태가 격리돼야 함."""
    bars = [_bar(d) for d in range(100)]
    cfg = WalkForwardConfig(train_days=20, validation_days=10,
                            step_days=10, holdout_days=0,
                            min_fold_count=2, min_positive_fold_ratio=0.0)
    result = run_walk_forward(
        bars=bars,
        strategy_factory=_AlwaysBuyOnce,
        walk_forward_config=cfg,
        initial_cash=1_000_000, quantity=1,
    )
    # 상태 격리 — 모든 fold가 정확히 같은 거래 카운트(1).
    for fold in result.folds:
        assert fold.validation_metrics["trade_count"] == 1


# ---------- API endpoint smoke ----------


def _basic_bars_payload(n: int = 120):
    out = []
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        ts = (base + timedelta(days=i)).isoformat()
        out.append({
            "symbol": "TEST", "timestamp": ts,
            "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000,
        })
    return out


def test_route_walk_forward_smoke(client):
    res = client.post("/api/backtest/walk-forward", json={
        "strategy": "sma_crossover",
        "params": {"short": 5, "long": 10},
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(120),
        "walk_forward": {
            "mode": "rolling", "train_days": 20, "validation_days": 10,
            "step_days": 10, "holdout_days": 20,
            "min_fold_count": 2, "min_positive_fold_ratio": 0.0,
        },
    })
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["promotion_recommendation"] in ("PASS", "CAUTION", "FAIL")
    assert "summary" in body
    assert "warnings" in body
    assert "overfit_flags" in body
    assert isinstance(body["folds"], list)
    assert body["bars_processed"] == 120


def test_route_walk_forward_rejects_invalid_strategy(client):
    res = client.post("/api/backtest/walk-forward", json={
        "strategy": "bogus_strategy",
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(60),
    })
    assert res.status_code == 400


def test_route_walk_forward_rejects_invalid_mode(client):
    res = client.post("/api/backtest/walk-forward", json={
        "strategy": "sma_crossover",
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(60),
        "walk_forward": {"mode": "vibes"},
    })
    assert res.status_code == 400


def test_route_walk_forward_does_not_break_existing_run(client):
    """walk-forward 라우트 추가가 /run 또는 /compare를 깨지 않음."""
    res = client.post("/api/backtest/run", json={
        "strategy": "sma_crossover",
        "initial_cash": 1_000_000, "quantity": 1,
        "bars": _basic_bars_payload(20),
    })
    assert res.status_code == 200
