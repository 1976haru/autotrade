"""#47 / 6-02: Walk-forward 과최적화 방지 검증 (Paper 분석 전용).

#46 의 4전략 + Agent Council 백테스트(`strategy_council_backtest`)가 *특정 기간에만
맞는 착시(overfit)* 인지 검증한다. 과거 데이터를 시간 순서로 train / validation /
test 로 분리하고, walk-forward(rolling) 방식으로 반복 검증해 검증 구간에서도 성과가
유지되는지 본다.

**본 작업은 과최적화 방지용 *검증* 작업이다.** 실제 주문을 보내지 않으며,
walk-forward 결과만으로 실전 전환(live promotion)을 허가하지 않는다.

핵심 설계:
- 분할은 *날짜(KST date) 단위* — 한 거래일을 train/val/test 로 쪼개지 않는다
  (#46 forward return 이 당일 close 로 clamp 되므로 day 경계 분할이 안전).
- **미래 데이터를 train 에 섞지 않는다** — dates 를 시간 순서로 정렬해 분할.
- 각 segment 는 독립적으로 #46 `run_strategy_council_backtest` 로 평가.
- train 성과가 좋아도 validation/test 가 붕괴하면 `overfit_suspected`.
- 결과는 분석 리포트(JSON/Markdown) 로만 저장 — 주문 권한 아님.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건 (place·cancel·route 경로 미사용).
- 외부 HTTP / AI SDK (anthropic/openai/httpx/requests) import 0건.
- `WalkForwardReport.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Sequence

from app.backtest.strategy_council_backtest import (
    DEFAULT_HORIZON_BARS,
    BacktestInput,
    BacktestReport,
    OHLCVBar,
    run_strategy_council_backtest,
)

_KST = timezone(timedelta(hours=9))

# segment 성과 측정 지표 — council expectancy 가 primary.
SEGMENT_LABELS: tuple[str, ...] = ("train", "validation", "test")


class WalkForwardMode(StrEnum):
    THREE_WAY = "THREE_WAY"   # 단순 60/20/20 분할 (split 1개).
    ROLLING = "ROLLING"       # rolling walk-forward (날짜 window 슬라이딩).


# reason_code.
WALK_FORWARD_INSUFFICIENT_DATA = "WALK_FORWARD_INSUFFICIENT_DATA"
WALK_FORWARD_SPLIT_CREATED = "WALK_FORWARD_SPLIT_CREATED"
WALK_FORWARD_VALIDATION_DEGRADATION = "WALK_FORWARD_VALIDATION_DEGRADATION"
WALK_FORWARD_TEST_DEGRADATION = "WALK_FORWARD_TEST_DEGRADATION"
WALK_FORWARD_OVERFIT_SUSPECTED = "WALK_FORWARD_OVERFIT_SUSPECTED"
WALK_FORWARD_PERFORMANCE_COLLAPSE = "WALK_FORWARD_PERFORMANCE_COLLAPSE"
WALK_FORWARD_STABLE = "WALK_FORWARD_STABLE"

DISCLAIMER_KO = (
    "본 walk-forward 리포트는 과최적화 방지용 *검증* 자료입니다. 실제 주문/실전 "
    "전환이 아니며, 본 결과만으로 실거래 권한을 부여하지 않습니다. 과거 성과는 "
    "미래 수익을 보장하지 않습니다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 입력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WalkForwardInput:
    bars:               tuple[OHLCVBar, ...]
    mode:               str = WalkForwardMode.THREE_WAY.value
    # THREE_WAY 비율 (test = 1 - train - validation).
    train_pct:          float = 0.6
    validation_pct:     float = 0.2
    # ROLLING window (날짜 일수).
    train_window_days:      int = 3
    validation_window_days: int = 1
    test_window_days:       int = 1
    step_days:              int = 1
    # 백테스트 파라미터 (#46 그대로 전달).
    risk_profile:       str = "BALANCED"
    horizons:           tuple[int, ...] = DEFAULT_HORIZON_BARS
    quantity:           int = 1
    primary_horizon:    str = "close"
    # 판정 임계.
    degradation_threshold: float = 0.5   # 유지율 < 0.5 면 degradation.
    overfit_threshold:     float = 0.5   # 검증/테스트 유지율 < 0.5 → overfit 의심.
    collapse_threshold:    float = 0.2   # 유지율 < 0.2 또는 expectancy 음전 → 붕괴.


# ─────────────────────────────────────────────────────────────────────────────
# 출력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SegmentResult:
    """한 segment(train/validation/test)의 백테스트 요약."""

    label:            str
    start_ts:         str | None
    end_ts:           str | None
    bar_count:        int
    council_expectancy: float | None
    council_win_rate:   float | None
    best_single_strategy: str | None
    best_single_expectancy: float | None
    reason_code:      str
    # 안정성 집계용 carry (council 의 regime/phase 버킷).
    by_market_regime: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_time_phase:    dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label":                  self.label,
            "start_ts":               self.start_ts,
            "end_ts":                 self.end_ts,
            "bar_count":              int(self.bar_count),
            "council_expectancy":     self.council_expectancy,
            "council_win_rate":       self.council_win_rate,
            "best_single_strategy":   self.best_single_strategy,
            "best_single_expectancy": self.best_single_expectancy,
            "reason_code":            self.reason_code,
            "by_market_regime":       {k: dict(v) for k, v in self.by_market_regime.items()},
            "by_time_phase":          {k: dict(v) for k, v in self.by_time_phase.items()},
        }


@dataclass(frozen=True)
class WalkForwardSplit:
    split_index:             int
    train:                   SegmentResult
    validation:              SegmentResult
    test:                    SegmentResult
    validation_retention_ratio: float | None
    test_retention_ratio:    float | None
    overfit_suspected:       bool
    degradation_reasons:     tuple[str, ...]
    reason_code:             str

    def to_dict(self) -> dict[str, Any]:
        return {
            "split_index":              int(self.split_index),
            "train":                    self.train.to_dict(),
            "validation":               self.validation.to_dict(),
            "test":                     self.test.to_dict(),
            "validation_retention_ratio": self.validation_retention_ratio,
            "test_retention_ratio":     self.test_retention_ratio,
            "overfit_suspected":        bool(self.overfit_suspected),
            "degradation_reasons":      list(self.degradation_reasons),
            "reason_code":              self.reason_code,
        }


@dataclass(frozen=True)
class WalkForwardReport:
    generated_at:           str
    mode:                   str
    config:                 dict[str, Any]
    split_count:            int
    splits:                 tuple[WalkForwardSplit, ...]
    overall_stability_score: float
    overall_overfit_suspected: bool
    collapse_segments:      tuple[dict[str, Any], ...]
    market_regime_stability: dict[str, dict[str, Any]]
    time_phase_stability:   dict[str, dict[str, Any]]
    council_vs_best_single: dict[str, Any]
    reason_code:            str
    insufficient_data:      bool

    is_order_signal:        bool = False
    is_live_authorization:  bool = False
    auto_apply_allowed:     bool = False
    broker_order_sent:      bool = False
    contains_secret:        bool = False
    disclaimer:             str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization",
                     "auto_apply_allowed", "broker_order_sent", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (walk-forward 는 검증 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":     self.generated_at,
            "mode":             self.mode,
            "config":           dict(self.config),
            "split_count":      int(self.split_count),
            "splits":           [s.to_dict() for s in self.splits],
            "overall_stability_score": self.overall_stability_score,
            "overall_overfit_suspected": bool(self.overall_overfit_suspected),
            "collapse_segments": [dict(c) for c in self.collapse_segments],
            "market_regime_stability": {k: dict(v) for k, v in self.market_regime_stability.items()},
            "time_phase_stability":    {k: dict(v) for k, v in self.time_phase_stability.items()},
            "council_vs_best_single":  dict(self.council_vs_best_single),
            "reason_code":      self.reason_code,
            "insufficient_data": bool(self.insufficient_data),
            "is_order_signal":       False,
            "is_live_authorization": False,
            "auto_apply_allowed":    False,
            "broker_order_sent":     False,
            "contains_secret":       False,
            "disclaimer":            self.disclaimer,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 날짜 단위 분할
# ─────────────────────────────────────────────────────────────────────────────


def _bar_date(b: OHLCVBar):
    return b.timestamp.astimezone(_KST).date()


def ordered_dates(bars: Sequence[OHLCVBar]) -> list[Any]:
    """bars 의 KST 날짜를 시간 순서로 정렬해 unique 반환."""
    seen: set[Any] = set()
    out: list[Any] = []
    for b in sorted(bars, key=lambda x: x.timestamp):
        d = _bar_date(b)
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _bars_for_dates(bars: Sequence[OHLCVBar], dates: set[Any]) -> list[OHLCVBar]:
    return sorted([b for b in bars if _bar_date(b) in dates], key=lambda x: x.timestamp)


def make_three_way_date_splits(
    dates: Sequence[Any], *, train_pct: float, validation_pct: float,
) -> list[tuple[list[Any], list[Any], list[Any]]]:
    """단순 train/validation/test 날짜 분할 (split 1개). 시간 순서 보존."""
    n = len(dates)
    if n < 3:
        return []
    n_train = max(1, int(round(n * train_pct)))
    n_val = max(1, int(round(n * validation_pct)))
    # test 가 최소 1일 남도록 보정.
    if n_train + n_val >= n:
        n_train = max(1, n - 2)
        n_val = 1
    train = list(dates[:n_train])
    val = list(dates[n_train:n_train + n_val])
    test = list(dates[n_train + n_val:])
    if not train or not val or not test:
        return []
    return [(train, val, test)]


def make_rolling_date_splits(
    dates: Sequence[Any], *, train_window: int, validation_window: int,
    test_window: int, step: int,
) -> list[tuple[list[Any], list[Any], list[Any]]]:
    """rolling walk-forward 날짜 분할 — window 를 step 만큼 슬라이딩."""
    window = train_window + validation_window + test_window
    step = max(1, step)
    out: list[tuple[list[Any], list[Any], list[Any]]] = []
    start = 0
    while start + window <= len(dates):
        train = list(dates[start: start + train_window])
        val = list(dates[start + train_window: start + train_window + validation_window])
        test = list(dates[start + train_window + validation_window: start + window])
        if train and val and test:
            out.append((train, val, test))
        start += step
    return out


# ─────────────────────────────────────────────────────────────────────────────
# segment 평가
# ─────────────────────────────────────────────────────────────────────────────


def _council_metrics(report: BacktestReport) -> tuple[float | None, float | None,
                                                       dict, dict]:
    if report.council is None:
        return None, None, {}, {}
    perf = report.council.performance
    return (perf.expectancy, perf.win_rate,
            dict(perf.by_market_regime), dict(perf.by_time_phase))


def _evaluate_segment(label: str, bars: Sequence[OHLCVBar], inp: WalkForwardInput) -> SegmentResult:
    if not bars:
        return SegmentResult(label, None, None, 0, None, None, None, None,
                             WALK_FORWARD_INSUFFICIENT_DATA)
    report = run_strategy_council_backtest(BacktestInput(
        bars=tuple(bars), risk_profile=inp.risk_profile, horizons=inp.horizons,
        quantity=inp.quantity, primary_horizon=inp.primary_horizon,
    ))
    if report.insufficient_data:
        return SegmentResult(
            label, bars[0].timestamp.isoformat(), bars[-1].timestamp.isoformat(),
            len(bars), None, None, None, None, WALK_FORWARD_INSUFFICIENT_DATA)
    c_exp, c_wr, by_reg, by_phase = _council_metrics(report)
    comp = report.comparison
    return SegmentResult(
        label=label,
        start_ts=bars[0].timestamp.isoformat(), end_ts=bars[-1].timestamp.isoformat(),
        bar_count=len(bars),
        council_expectancy=c_exp, council_win_rate=c_wr,
        best_single_strategy=comp.get("best_single_strategy"),
        best_single_expectancy=comp.get("best_single_expectancy"),
        reason_code=report.reason_code,
        by_market_regime=by_reg, by_time_phase=by_phase,
    )


def _retention(train_exp: float | None, seg_exp: float | None) -> float | None:
    """유지율 = seg_exp / train_exp. train 이 양(+)일 때만 정의.

    - train_exp <= 0 또는 None → None (유지율 측정 불가).
    - seg_exp None(BUY 신호 없음) → 0.0 (성과 미유지로 보수적 판정).
    """
    if train_exp is None or train_exp <= 0:
        return None
    if seg_exp is None:
        return 0.0
    return round(seg_exp / train_exp, 4)


def _evaluate_split(idx: int, train_b, val_b, test_b, inp: WalkForwardInput) -> WalkForwardSplit:
    train = _evaluate_segment("train", train_b, inp)
    val = _evaluate_segment("validation", val_b, inp)
    test = _evaluate_segment("test", test_b, inp)

    val_ret = _retention(train.council_expectancy, val.council_expectancy)
    test_ret = _retention(train.council_expectancy, test.council_expectancy)

    reasons: list[str] = []
    if WALK_FORWARD_INSUFFICIENT_DATA in (train.reason_code, val.reason_code, test.reason_code):
        return WalkForwardSplit(idx, train, val, test, val_ret, test_ret, False,
                                (WALK_FORWARD_INSUFFICIENT_DATA,),
                                WALK_FORWARD_INSUFFICIENT_DATA)

    if val_ret is not None and val_ret < inp.degradation_threshold:
        reasons.append(WALK_FORWARD_VALIDATION_DEGRADATION)
    if test_ret is not None and test_ret < inp.degradation_threshold:
        reasons.append(WALK_FORWARD_TEST_DEGRADATION)

    # overfit 의심: train 이 양(+)인데 검증/테스트 유지율이 임계 미만.
    overfit = False
    if train.council_expectancy is not None and train.council_expectancy > 0:
        below = [r for r in (val_ret, test_ret) if r is not None and r < inp.overfit_threshold]
        # 검증/테스트 expectancy 가 음전이거나 유지율이 낮으면 overfit.
        neg_oos = [s for s in (val, test)
                   if s.council_expectancy is not None and s.council_expectancy <= 0]
        if below or neg_oos:
            overfit = True
            reasons.append(WALK_FORWARD_OVERFIT_SUSPECTED)

    reason_code = WALK_FORWARD_OVERFIT_SUSPECTED if overfit else (
        WALK_FORWARD_VALIDATION_DEGRADATION if WALK_FORWARD_VALIDATION_DEGRADATION in reasons
        else WALK_FORWARD_TEST_DEGRADATION if WALK_FORWARD_TEST_DEGRADATION in reasons
        else WALK_FORWARD_SPLIT_CREATED
    )
    return WalkForwardSplit(idx, train, val, test, val_ret, test_ret, overfit,
                            tuple(reasons), reason_code)


# ─────────────────────────────────────────────────────────────────────────────
# 안정성 / 붕괴 집계
# ─────────────────────────────────────────────────────────────────────────────


def _stability_score(splits: Sequence[WalkForwardSplit]) -> float:
    """0~1 안정성 점수.

    formula = 0.6 * retention_component + 0.4 * positive_fraction
    - retention_component = clamp01(mean over splits of min(val_ret, test_ret))
    - positive_fraction = (council expectancy > 0 인 OOS segment 수) / (OOS segment 수)
    OOS = validation + test.
    """
    mins: list[float] = []
    pos = 0
    total = 0
    for sp in splits:
        rets = [r for r in (sp.validation_retention_ratio, sp.test_retention_ratio) if r is not None]
        if rets:
            mins.append(max(0.0, min(min(rets), 1.0)))
        for seg in (sp.validation, sp.test):
            if seg.council_expectancy is not None:
                total += 1
                if seg.council_expectancy > 0:
                    pos += 1
    retention_component = (sum(mins) / len(mins)) if mins else 0.0
    positive_fraction = (pos / total) if total else 0.0
    return round(0.6 * retention_component + 0.4 * positive_fraction, 4)


def _collapse_segments(splits: Sequence[WalkForwardSplit], threshold: float) -> list[dict[str, Any]]:
    """성과 붕괴 segment 탐지 — 유지율 < collapse_threshold 또는 expectancy 음전."""
    out: list[dict[str, Any]] = []
    for sp in splits:
        for seg, ret in (("validation", sp.validation_retention_ratio),
                         ("test", sp.test_retention_ratio)):
            sr = getattr(sp, seg)
            collapsed = (
                (ret is not None and ret < threshold)
                or (sr.council_expectancy is not None and sr.council_expectancy <= 0)
            )
            if collapsed:
                out.append({
                    "split_index": sp.split_index, "segment": seg,
                    "retention_ratio": ret,
                    "council_expectancy": sr.council_expectancy,
                    "reason_code": WALK_FORWARD_PERFORMANCE_COLLAPSE,
                })
    return out


def _bucket_stability(splits: Sequence[WalkForwardSplit], attr: str) -> dict[str, dict[str, Any]]:
    """OOS(validation+test) segment 들의 council regime/phase 버킷 win_rate 안정성."""
    collected: dict[str, list[float]] = {}
    for sp in splits:
        for seg in (sp.validation, sp.test):
            buckets = getattr(seg, attr)
            for k, v in buckets.items():
                wr = v.get("win_rate")
                if wr is not None:
                    collected.setdefault(k, []).append(float(wr))
    out: dict[str, dict[str, Any]] = {}
    for k, wrs in sorted(collected.items()):
        mn, mx = min(wrs), max(wrs)
        spread = mx - mn
        out[k] = {
            "segments_present": len(wrs),
            "mean_win_rate":    round(sum(wrs) / len(wrs), 6),
            "min_win_rate":     round(mn, 6),
            "max_win_rate":     round(mx, 6),
            "win_rate_spread":  round(spread, 6),
            # 일관성: OOS 구간 간 win_rate 편차가 30%p 이하면 consistent.
            "consistent":       bool(spread <= 0.30),
        }
    return out


def _council_vs_single_aggregate(splits: Sequence[WalkForwardSplit]) -> dict[str, Any]:
    """OOS 구간에서 Agent Council 이 best single 보다 자주 나은지 집계."""
    council_better = 0
    comparable = 0
    for sp in splits:
        for seg in (sp.validation, sp.test):
            ce, be = seg.council_expectancy, seg.best_single_expectancy
            if ce is not None and be is not None:
                comparable += 1
                if ce >= be:
                    council_better += 1
    return {
        "metric": "expectancy",
        "scope": "out_of_sample (validation+test)",
        "comparable_segments": comparable,
        "council_better_count": council_better,
        "council_better_fraction": (round(council_better / comparable, 4) if comparable else None),
        "note": ("Agent Council 이 검증 구간에서 단일 전략보다 나은지의 *백테스트 비교* "
                 "이며, 실전 전환 근거가 아닙니다."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────────────────────────────────────


def run_walk_forward_validation(inp: WalkForwardInput) -> WalkForwardReport:
    now_iso = datetime.now(timezone.utc).isoformat()
    bars = list(inp.bars)
    dates = ordered_dates(bars)
    mode = inp.mode if inp.mode in (WalkForwardMode.THREE_WAY.value,
                                    WalkForwardMode.ROLLING.value) else WalkForwardMode.THREE_WAY.value

    config = {
        "mode": mode, "train_pct": inp.train_pct, "validation_pct": inp.validation_pct,
        "test_pct": round(1 - inp.train_pct - inp.validation_pct, 4),
        "train_window_days": inp.train_window_days,
        "validation_window_days": inp.validation_window_days,
        "test_window_days": inp.test_window_days, "step_days": inp.step_days,
        "risk_profile": inp.risk_profile, "primary_horizon": inp.primary_horizon,
        "degradation_threshold": inp.degradation_threshold,
        "overfit_threshold": inp.overfit_threshold,
        "collapse_threshold": inp.collapse_threshold,
        "total_dates": len(dates),
    }

    if mode == WalkForwardMode.ROLLING.value:
        date_splits = make_rolling_date_splits(
            dates, train_window=inp.train_window_days,
            validation_window=inp.validation_window_days,
            test_window=inp.test_window_days, step=inp.step_days)
    else:
        date_splits = make_three_way_date_splits(
            dates, train_pct=inp.train_pct, validation_pct=inp.validation_pct)

    if not date_splits:
        return _empty_report(now_iso, mode, config, WALK_FORWARD_INSUFFICIENT_DATA)

    splits: list[WalkForwardSplit] = []
    for idx, (tr, va, te) in enumerate(date_splits):
        split = _evaluate_split(
            idx,
            _bars_for_dates(bars, set(tr)),
            _bars_for_dates(bars, set(va)),
            _bars_for_dates(bars, set(te)),
            inp,
        )
        splits.append(split)

    evaluated = [s for s in splits if s.reason_code != WALK_FORWARD_INSUFFICIENT_DATA]
    if not evaluated:
        return _empty_report(now_iso, mode, config, WALK_FORWARD_INSUFFICIENT_DATA,
                             splits=tuple(splits))

    stability = _stability_score(evaluated)
    collapse = _collapse_segments(evaluated, inp.collapse_threshold)
    overall_overfit = any(s.overfit_suspected for s in evaluated) or stability < inp.overfit_threshold

    if overall_overfit:
        reason = WALK_FORWARD_OVERFIT_SUSPECTED
    elif collapse:
        reason = WALK_FORWARD_PERFORMANCE_COLLAPSE
    else:
        reason = WALK_FORWARD_STABLE

    return WalkForwardReport(
        generated_at=now_iso, mode=mode, config=config, split_count=len(splits),
        splits=tuple(splits), overall_stability_score=stability,
        overall_overfit_suspected=bool(overall_overfit),
        collapse_segments=tuple(collapse),
        market_regime_stability=_bucket_stability(evaluated, "by_market_regime"),
        time_phase_stability=_bucket_stability(evaluated, "by_time_phase"),
        council_vs_best_single=_council_vs_single_aggregate(evaluated),
        reason_code=reason, insufficient_data=False,
    )


def _empty_report(now_iso, mode, config, reason, *, splits=()) -> WalkForwardReport:
    return WalkForwardReport(
        generated_at=now_iso, mode=mode, config=config, split_count=len(splits),
        splits=tuple(splits), overall_stability_score=0.0,
        overall_overfit_suspected=False, collapse_segments=(),
        market_regime_stability={}, time_phase_stability={},
        council_vs_best_single={"comparable_segments": 0, "reason_code": reason},
        reason_code=reason, insufficient_data=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 요약 / 리포트 렌더
# ─────────────────────────────────────────────────────────────────────────────


def summarize_walk_forward_report(report: WalkForwardReport) -> dict[str, Any]:
    return report.to_dict()


def _fmt(v: Any, *, pct: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v*100:.2f}%" if pct else f"{v:.4f}"
    return str(v)


def render_markdown_report(report: WalkForwardReport) -> str:
    lines: list[str] = []
    lines.append("# Walk-forward 과최적화 방지 검증 리포트 (#47 / 6-02)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    # 1. 요약
    lines.append("## 1. 요약")
    lines.append(f"- mode: {report.mode} · split 개수: {report.split_count}")
    lines.append(f"- 안정성 점수(stability_score): {_fmt(report.overall_stability_score)}")
    lines.append(f"- 과최적화 의심(overfit_suspected): "
                 f"{'예' if report.overall_overfit_suspected else '아니오'}")
    lines.append(f"- reason_code: {report.reason_code}")
    lines.append("")
    # 2. 설정
    lines.append("## 2. 설정")
    for k, v in report.config.items():
        lines.append(f"- {k}: {v}")
    lines.append("")

    if report.insufficient_data:
        lines.append("## ⚠️ 데이터 부족")
        lines.append(f"walk-forward split 을 만들기에 데이터가 부족합니다 "
                     f"(`{report.reason_code}`).")
        return "\n".join(lines) + "\n"

    # 3~7. split별 성과 + 유지율
    lines.append("## 3. split별 성과 (council expectancy / 유지율)")
    lines.append("")
    lines.append("| split | train exp | val exp | test exp | val 유지율 | test 유지율 | overfit | reason |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for sp in report.splits:
        lines.append(
            f"| {sp.split_index} | {_fmt(sp.train.council_expectancy)} | "
            f"{_fmt(sp.validation.council_expectancy)} | {_fmt(sp.test.council_expectancy)} | "
            f"{_fmt(sp.validation_retention_ratio)} | {_fmt(sp.test_retention_ratio)} | "
            f"{'Y' if sp.overfit_suspected else 'N'} | {sp.reason_code} |"
        )
    lines.append("")
    lines.append("### train/validation/test 기간")
    for sp in report.splits:
        lines.append(f"- split {sp.split_index}: train {sp.train.start_ts}~{sp.train.end_ts} · "
                     f"val {sp.validation.start_ts}~{sp.validation.end_ts} · "
                     f"test {sp.test.start_ts}~{sp.test.end_ts}")
    lines.append("")

    # 8~10. stability / overfit / degradation
    lines.append("## 4. 안정성 / 과최적화 / 성과 붕괴")
    lines.append(f"- stability_score: {_fmt(report.overall_stability_score)} "
                 "(0.6·유지율 + 0.4·OOS 양(+)비율)")
    lines.append(f"- overfit_suspected: {'예' if report.overall_overfit_suspected else '아니오'}")
    if report.collapse_segments:
        lines.append("- 성과 붕괴 구간:")
        for c in report.collapse_segments:
            lines.append(f"  - split {c['split_index']} {c['segment']}: "
                         f"유지율 {_fmt(c['retention_ratio'])}, exp {_fmt(c['council_expectancy'])}")
    else:
        lines.append("- 성과 붕괴 구간: 없음")
    degr = sorted({r for sp in report.splits for r in sp.degradation_reasons})
    lines.append(f"- degradation reasons: {degr or '없음'}")
    lines.append("")

    # 11. Agent Council vs best single
    cvs = report.council_vs_best_single
    lines.append("## 5. Agent Council vs best single (out-of-sample)")
    lines.append(f"- 비교 가능 segment: {cvs.get('comparable_segments')}")
    lines.append(f"- Council 우위 횟수: {cvs.get('council_better_count')} "
                 f"({_fmt(cvs.get('council_better_fraction'), pct=True)})")
    lines.append(f"> {cvs.get('note', '')}")
    lines.append("")

    # 12~13. regime / time_phase 안정성
    lines.append("## 6. market_regime 안정성 (OOS)")
    lines.append("| regime | OOS segment | 평균 승률 | 최소 | 최대 | 편차 | 일관 |")
    lines.append("|---|---|---|---|---|---|---|")
    for k, v in report.market_regime_stability.items():
        lines.append(f"| {k} | {v['segments_present']} | {_fmt(v['mean_win_rate'], pct=True)} | "
                     f"{_fmt(v['min_win_rate'], pct=True)} | {_fmt(v['max_win_rate'], pct=True)} | "
                     f"{_fmt(v['win_rate_spread'], pct=True)} | {'Y' if v['consistent'] else 'N'} |")
    lines.append("")
    lines.append("## 7. time_phase 안정성 (OOS)")
    lines.append("| time_phase | OOS segment | 평균 승률 | 최소 | 최대 | 편차 | 일관 |")
    lines.append("|---|---|---|---|---|---|---|")
    for k, v in report.time_phase_stability.items():
        lines.append(f"| {k} | {v['segments_present']} | {_fmt(v['mean_win_rate'], pct=True)} | "
                     f"{_fmt(v['min_win_rate'], pct=True)} | {_fmt(v['max_win_rate'], pct=True)} | "
                     f"{_fmt(v['win_rate_spread'], pct=True)} | {'Y' if v['consistent'] else 'N'} |")
    lines.append("")

    # 14~16. 한계 / 실전 전환 아님 / 수익 보장 아님
    lines.append("## 8. 한계 및 고지")
    lines.append("- 본 검증은 fixture / 사용자 CSV 의 품질·대표성에 좌우됩니다.")
    lines.append("- forward return 은 당일 close 로 clamp 한 일중 보유 가정 — 익일 갭/"
                 "슬리피지/부분체결 미반영.")
    lines.append("- **실전 전환을 자동 승인하지 않습니다.** 별도 Paper Gate / Live Capital "
                 "Review / Manual Approval / Canary 게이트 + 운영자 옵트인이 필요합니다.")
    lines.append("- **과거 성과는 미래 수익을 보장하지 않습니다.**")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "WalkForwardMode", "SEGMENT_LABELS",
    "WALK_FORWARD_INSUFFICIENT_DATA", "WALK_FORWARD_SPLIT_CREATED",
    "WALK_FORWARD_VALIDATION_DEGRADATION", "WALK_FORWARD_TEST_DEGRADATION",
    "WALK_FORWARD_OVERFIT_SUSPECTED", "WALK_FORWARD_PERFORMANCE_COLLAPSE",
    "WALK_FORWARD_STABLE",
    "WalkForwardInput", "SegmentResult", "WalkForwardSplit", "WalkForwardReport",
    "ordered_dates", "make_three_way_date_splits", "make_rolling_date_splits",
    "run_walk_forward_validation", "summarize_walk_forward_report",
    "render_markdown_report",
]
