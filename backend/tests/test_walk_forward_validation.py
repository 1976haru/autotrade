"""#47 / 6-02: Walk-forward 과최적화 방지 검증 테스트.

핵심 invariant:
- train/validation/test 시간 순서 분할 (미래 데이터가 train 에 섞이지 않음).
- rolling split 생성.
- split별 4전략 + Agent Council 백테스트.
- train/validation/test 성과 + 유지율(retention) + overfit_suspected + stability_score.
- 성과 붕괴 구간 탐지 + market_regime / time_phase 안정성.
- 데이터 부족 → WALK_FORWARD_INSUFFICIENT_DATA.
- broker / OrderExecutor / route_order / KIS import·호출 0건, secret 0건,
  is_live_authorization / is_order_signal / broker_order_sent / contains_secret = False.
"""

from __future__ import annotations

import json

import pytest

from app.backtest import walk_forward_validation as wf
from app.backtest.strategy_council_backtest import load_ohlcv_from_records
from app.backtest.walk_forward_validation import (
    WALK_FORWARD_INSUFFICIENT_DATA,
    WALK_FORWARD_OVERFIT_SUSPECTED,
    WALK_FORWARD_STABLE,
    WALK_FORWARD_VALIDATION_DEGRADATION,
    SegmentResult,
    WalkForwardInput,
    WalkForwardMode,
    WalkForwardReport,
    WalkForwardSplit,
    make_rolling_date_splits,
    make_three_way_date_splits,
    render_markdown_report,
    run_walk_forward_validation,
    summarize_walk_forward_report,
)

from tests.fixtures.backtest_ohlcv import (
    walk_forward_insufficient_records,
    walk_forward_overfit_records,
    walk_forward_stable_records,
)


def _bars(records):
    return tuple(load_ohlcv_from_records(records))


@pytest.fixture(scope="module")
def stable_report() -> WalkForwardReport:
    return run_walk_forward_validation(WalkForwardInput(bars=_bars(walk_forward_stable_records("005930"))))


@pytest.fixture(scope="module")
def overfit_report() -> WalkForwardReport:
    return run_walk_forward_validation(WalkForwardInput(bars=_bars(walk_forward_overfit_records("005930"))))


@pytest.fixture(scope="module")
def rolling_report() -> WalkForwardReport:
    return run_walk_forward_validation(WalkForwardInput(
        bars=_bars(walk_forward_stable_records("005930")), mode=WalkForwardMode.ROLLING.value))


# ── 1~3. split 생성 / 시간 순서 / 미래 누출 방지 ─────────────────────────────


def test_three_way_split_created(stable_report):
    assert stable_report.split_count == 1
    sp = stable_report.splits[0]
    assert sp.train.bar_count > 0
    assert sp.validation.bar_count > 0
    assert sp.test.bar_count > 0


def test_split_chronological_order():
    dates = list(range(10))
    splits = make_three_way_date_splits(dates, train_pct=0.6, validation_pct=0.2)
    train, val, test = splits[0]
    # 시간 순서: train < val < test, 겹침 없음.
    assert max(train) < min(val) < max(val) < min(test)
    assert set(train) | set(val) | set(test) == set(dates)
    assert not (set(train) & set(val)) and not (set(val) & set(test))


def test_no_future_data_in_train(stable_report):
    sp = stable_report.splits[0]
    # train 끝 시각 <= validation 시작 <= test 시작.
    assert sp.train.end_ts <= sp.validation.start_ts
    assert sp.validation.end_ts <= sp.test.start_ts


def test_rolling_split_created(rolling_report):
    assert rolling_report.mode == WalkForwardMode.ROLLING.value
    assert rolling_report.split_count >= 2   # rolling 은 2개 이상.


def test_rolling_windows_slide():
    dates = list(range(8))
    splits = make_rolling_date_splits(dates, train_window=3, validation_window=1,
                                      test_window=1, step=1)
    assert len(splits) == 4   # window 5, step 1 → 4 windows.
    # 각 window 내부 순서 보장.
    for train, val, test in splits:
        assert max(train) < min(val) <= max(val) < min(test)


# ── 5. 데이터 부족 ───────────────────────────────────────────────────────────


def test_insufficient_data():
    rep = run_walk_forward_validation(WalkForwardInput(bars=_bars(walk_forward_insufficient_records())))
    assert rep.reason_code == WALK_FORWARD_INSUFFICIENT_DATA
    assert rep.insufficient_data is True


def test_three_way_too_few_dates_returns_empty():
    assert make_three_way_date_splits([1, 2], train_pct=0.6, validation_pct=0.2) == []


# ── 6~10. split별 백테스트 + 성과 산출 ───────────────────────────────────────


def test_segment_metrics_present(stable_report):
    sp = stable_report.splits[0]
    for seg in (sp.train, sp.validation, sp.test):
        assert seg.council_expectancy is not None        # council 성과 산출.
        assert seg.council_win_rate is not None
        assert seg.best_single_strategy in (
            "ORB", "MOMENTUM", "GAP", "VWAP")             # split별 best single.


def test_four_strategies_and_council_evaluated_per_segment():
    # segment 평가는 #46 council 백테스트를 호출 — best_single 이 4 전략 중 하나.
    bars = _bars(walk_forward_stable_records("005930"))
    rep = run_walk_forward_validation(WalkForwardInput(bars=bars))
    sp = rep.splits[0]
    assert sp.train.best_single_strategy in {"ORB", "MOMENTUM", "GAP", "VWAP"}
    assert sp.train.council_expectancy is not None


# ── 11~12. 유지율 ────────────────────────────────────────────────────────────


def test_retention_ratios_computed(stable_report):
    sp = stable_report.splits[0]
    assert sp.validation_retention_ratio is not None
    assert sp.test_retention_ratio is not None
    # 안정 fixture → 유지율 ~1.0.
    assert sp.validation_retention_ratio > 0.8
    assert sp.test_retention_ratio > 0.8


def test_retention_formula():
    assert wf._retention(100.0, 50.0) == 0.5
    assert wf._retention(100.0, 100.0) == 1.0
    # train <= 0 또는 None → 측정 불가.
    assert wf._retention(0.0, 50.0) is None
    assert wf._retention(None, 50.0) is None
    # seg None(BUY 없음) → 0.0 (보수적).
    assert wf._retention(100.0, None) == 0.0


# ── 13~14. degradation / collapse 탐지 ───────────────────────────────────────


def test_validation_degradation_detected(overfit_report):
    sp = overfit_report.splits[0]
    # 과최적화 fixture → 검증/테스트 유지율 붕괴.
    assert sp.validation_retention_ratio is not None
    assert sp.validation_retention_ratio < 0.5
    assert (WALK_FORWARD_VALIDATION_DEGRADATION in sp.degradation_reasons
            or WALK_FORWARD_OVERFIT_SUSPECTED in sp.degradation_reasons)


def test_performance_collapse_segments(overfit_report):
    # rolling/3way 어디서든 붕괴 구간이 잡혀야 한다.
    rep = run_walk_forward_validation(WalkForwardInput(
        bars=_bars(walk_forward_overfit_records("005930")), mode=WalkForwardMode.ROLLING.value))
    assert len(rep.collapse_segments) > 0
    for c in rep.collapse_segments:
        assert c["reason_code"] == wf.WALK_FORWARD_PERFORMANCE_COLLAPSE


# ── 15. overfit_suspected ────────────────────────────────────────────────────


def test_overfit_suspected_flag(overfit_report, stable_report):
    assert overfit_report.overall_overfit_suspected is True
    assert overfit_report.reason_code == WALK_FORWARD_OVERFIT_SUSPECTED
    # 안정 fixture 는 overfit 아님.
    assert stable_report.overall_overfit_suspected is False
    assert stable_report.reason_code == WALK_FORWARD_STABLE


# ── 16. stability_score ──────────────────────────────────────────────────────


def test_stability_score_range(stable_report, overfit_report):
    assert 0.0 <= stable_report.overall_stability_score <= 1.0
    assert 0.0 <= overfit_report.overall_stability_score <= 1.0
    # 안정 > 과최적화.
    assert stable_report.overall_stability_score > overfit_report.overall_stability_score


def test_stability_score_formula_unit():
    # 두 split, 모두 OOS 양(+), 유지율 1.0 → stability 1.0.
    splits = [_fake_split(0, 100, 100, 100), _fake_split(1, 100, 100, 100)]
    assert wf._stability_score(splits) == pytest.approx(1.0)
    # OOS 음전 + 유지율 0 → 낮은 점수.
    bad = [_fake_split(0, 100, -50, -50)]
    assert wf._stability_score(bad) < 0.3


# ── 17~19. council vs single / regime / phase 안정성 ─────────────────────────


def test_council_vs_best_single_aggregate(stable_report):
    cvs = stable_report.council_vs_best_single
    assert cvs["metric"] == "expectancy"
    assert "council_better_fraction" in cvs
    assert "실전 전환" in cvs["note"]


def test_regime_and_phase_stability(stable_report):
    # 안정 fixture(상승) → 적어도 하나의 regime / time_phase 버킷이 존재.
    assert isinstance(stable_report.market_regime_stability, dict)
    assert isinstance(stable_report.time_phase_stability, dict)
    if stable_report.time_phase_stability:
        any_phase = next(iter(stable_report.time_phase_stability.values()))
        for k in ("segments_present", "mean_win_rate", "win_rate_spread", "consistent"):
            assert k in any_phase


# ── 20. reason_codes ─────────────────────────────────────────────────────────


def test_report_reason_codes(stable_report, overfit_report):
    assert stable_report.reason_code == WALK_FORWARD_STABLE
    assert overfit_report.reason_code == WALK_FORWARD_OVERFIT_SUSPECTED


# ── 21~24. 안전 invariant ────────────────────────────────────────────────────


def test_report_invariants(stable_report):
    assert stable_report.is_order_signal is False
    assert stable_report.is_live_authorization is False
    assert stable_report.broker_order_sent is False
    assert stable_report.contains_secret is False
    d = stable_report.to_dict()
    assert d["is_live_authorization"] is False
    assert d["is_order_signal"] is False
    assert d["broker_order_sent"] is False
    assert d["contains_secret"] is False


def test_report_invariant_enforced():
    with pytest.raises(ValueError):
        WalkForwardReport(
            generated_at="x", mode="THREE_WAY", config={}, split_count=0, splits=(),
            overall_stability_score=0.0, overall_overfit_suspected=False,
            collapse_segments=(), market_regime_stability={}, time_phase_stability={},
            council_vs_best_single={}, reason_code=WALK_FORWARD_STABLE,
            insufficient_data=False, is_live_authorization=True)


# ── 25~26. import / 호출 가드 ────────────────────────────────────────────────


def test_no_forbidden_imports_or_calls():
    src = open(wf.__file__, encoding="utf-8").read()
    forbidden = (
        "from app.brokers", "from app.execution.order_router",
        "from app.execution.executor", "from app.execution.order_executor",
        "from app.execution.paper_trader", "from app.brokers.kis",
        "from app.brokers.mock_broker", "import anthropic", "import openai",
        "import httpx", "import requests",
        ".place_order(", ".cancel_order(", "route_order(",
    )
    for tok in forbidden:
        assert tok not in src, f"forbidden token in module: {tok}"


# ── secret / 문구 가드 ───────────────────────────────────────────────────────


def test_no_secret_or_forbidden_language(stable_report):
    text = json.dumps(summarize_walk_forward_report(stable_report), ensure_ascii=False)
    text += render_markdown_report(stable_report)
    for forbidden in ("수익 보장", "실전 전환 승인", "원금 보장", "guaranteed",
                      "kis_app_secret", "access_token", "Bearer ", "sk-ant-",
                      "is_live_authorization\": true"):
        assert forbidden not in text


def test_markdown_required_sections(stable_report):
    md = render_markdown_report(stable_report)
    for section in ("요약", "설정", "split별 성과", "안정성", "Agent Council vs best single",
                    "market_regime 안정성", "time_phase 안정성", "한계",
                    "실전 전환을 자동 승인하지 않습니다", "미래 수익을 보장하지 않습니다"):
        assert section in md


# ── helpers ──────────────────────────────────────────────────────────────────


def _seg(label, exp):
    return SegmentResult(label, "2026-05-11T00:00:00+00:00", "2026-05-11T01:00:00+00:00",
                         100, exp, 0.6 if exp is not None else None, "ORB",
                         exp, wf.WALK_FORWARD_SPLIT_CREATED)


def _fake_split(idx, train_exp, val_exp, test_exp):
    train = _seg("train", train_exp)
    val = _seg("validation", val_exp)
    test = _seg("test", test_exp)
    return WalkForwardSplit(
        idx, train, val, test,
        wf._retention(train_exp, val_exp), wf._retention(train_exp, test_exp),
        False, (), wf.WALK_FORWARD_SPLIT_CREATED)
