"""에이전트 학습효과 측정 — read-only advisory (정직한 추이 기록만).

운영자 가설: "에이전트가 시간이 지나며 학습해서, 고정 백테스트가 못 넘은
비용벽(왕복 33bps)을 극복한다." 본 모듈은 *그 가설을 데이터로 시험* 한다 —
믿음을 막지도, "올라간다"를 미리 가정하지도 않는다. 시점별 성적을 그대로
기록하고 비용벽 선과 함께 보여줘, 넘는지 안 넘는지 운영자가 직접 판단하게 한다.

핵심 설계:
- 입력은 *이미 일어난* 결정/체결 기록(plain record) — caller 가 AgentDecisionEpisode
  등 DB row 를 매핑해 주입. 본 모듈은 broker / route_order / DB write 0건(순수 분석).
- 시점별(일자 버킷) 지표: 승률 / 평균손익per건 / 비용후 손익 / Council confidence /
  quality_score. 모두 timestamp 와 함께 누적.
- "초기 구간 vs 이후 구간" 비교(`compare_periods`) — 학습으로 *진짜* 나아졌는지.
- 비용벽(33bps) 기준선을 함께 carry — 비용후 손익이 0 위/아래인지 한눈에.
- ★과적합 경계: "최근 데이터에만 맞춰지는" 건 학습이 아니라 과적합. 최근 구간
  성적이 좋아도 *직전(과거) 구간* 에서 검증되지 않으면 `overfit_suspected` 표시.
- ★정직성 invariant: 본 리포트는 "학습 성공"을 주장하지 않는다 —
  `assumes_improvement=False` 영구. verdict 는 데이터가 말하는 대로.

`LearningMeasurementReport.is_order_signal` / `is_live_authorization` /
`auto_apply_allowed` 항상 False (dataclass __post_init__ ValueError 가드).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Sequence

# 비용벽 — cost_model.py 단일진실과 동일 (왕복 33bps = 0.0033).
COST_WALL_ROUNDTRIP_BPS = 33.0
COST_WALL_FRACTION = COST_WALL_ROUNDTRIP_BPS / 1e4


class LearningVerdict(StrEnum):
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"     # 표본 부족 — 판단 불가
    NO_TREND = "NO_TREND"                        # 변화 없음/노이즈
    IMPROVING_ABOVE_WALL = "IMPROVING_ABOVE_WALL"   # 나아지고 비용벽도 넘음(고무적, 단 표본·과적합 주의)
    IMPROVING_BELOW_WALL = "IMPROVING_BELOW_WALL"   # 나아지나 아직 비용벽 못 넘음
    DEGRADING = "DEGRADING"                      # 나빠짐
    OVERFIT_SUSPECTED = "OVERFIT_SUSPECTED"      # 최근만 좋음 — 과적합 의심


@dataclass(frozen=True)
class DecisionRecord:
    """이미 일어난 결정/체결 1건 (caller 가 DB row 에서 매핑).

    ts_epoch: 결정 시각 (UNIX epoch sec, 정렬·버킷용 — lookahead 없음, 과거 기록).
    realized_pnl_pct: 청산까지 실현 손익률 (소수, 예 +0.012 = +1.2%). 미청산은 None.
    confidence: Council confidence 0..1. quality_score: 0..100.
    """
    ts_epoch: float
    action: str                       # "BUY"/"SELL"/"HOLD"
    realized_pnl_pct: float | None    # None = 미청산/HOLD (성적 집계 제외)
    confidence: float | None = None
    quality_score: float | None = None


@dataclass(frozen=True)
class PeriodStats:
    label: str
    n_decisions: int
    n_closed_trades: int              # realized_pnl_pct 가 있는 건수
    win_rate: float | None            # 청산 중 +비율
    avg_pnl_pct: float | None         # 청산 평균 손익률 (비용 *후* — 입력이 실현가라 이미 비용 반영 가정)
    median_pnl_pct: float | None
    avg_confidence: float | None
    avg_quality: float | None
    above_cost_wall: bool | None      # avg_pnl_pct > 0 (실현이라 비용 반영됨) — 비용벽 위인지
    from_iso: str = ""
    to_iso: str = ""


@dataclass(frozen=True)
class LearningMeasurementReport:
    verdict: LearningVerdict
    periods: tuple[PeriodStats, ...]          # 시간순 버킷별 성적
    initial: PeriodStats | None               # 초기 N
    later: PeriodStats | None                 # 이후 N
    delta_avg_pnl_pp: float | None            # later - initial 평균손익 (%포인트)
    delta_win_rate_pp: float | None
    delta_quality: float | None
    cost_wall_bps: float
    overfit_suspected: bool
    n_total: int
    notes: tuple[str, ...] = field(default_factory=tuple)
    # 정직성 + 안전 invariant
    assumes_improvement: bool = False
    is_order_signal: bool = False
    is_live_authorization: bool = False
    auto_apply_allowed: bool = False

    def __post_init__(self) -> None:
        if self.assumes_improvement is not False:
            raise ValueError("assumes_improvement must be False — measure honestly, do not assume learning")
        for flag in ("is_order_signal", "is_live_authorization", "auto_apply_allowed"):
            if getattr(self, flag) is not False:
                raise ValueError(f"{flag} must be False — learning measurement is read-only advisory")


def _mean(xs: Sequence[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _median(xs: Sequence[float]) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _iso(ts: float) -> str:
    # tz-naive UTC ISO (입력은 epoch sec). datetime.now 미사용 — 결정성.
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _stats(label: str, recs: Sequence[DecisionRecord]) -> PeriodStats:
    closed = [r.realized_pnl_pct for r in recs if r.realized_pnl_pct is not None]
    wins = [p for p in closed if p > 0]
    avg = _mean(closed)
    confs = [r.confidence for r in recs if r.confidence is not None]
    quals = [r.quality_score for r in recs if r.quality_score is not None]
    ts = [r.ts_epoch for r in recs]
    return PeriodStats(
        label=label,
        n_decisions=len(recs),
        n_closed_trades=len(closed),
        win_rate=(len(wins) / len(closed)) if closed else None,
        avg_pnl_pct=avg,
        median_pnl_pct=_median(closed),
        avg_confidence=_mean(confs),
        avg_quality=_mean(quals),
        above_cost_wall=(avg > 0) if avg is not None else None,
        from_iso=_iso(min(ts)) if ts else "",
        to_iso=_iso(max(ts)) if ts else "",
    )


def measure_learning(
    records: Sequence[DecisionRecord],
    *,
    n_buckets: int = 5,
    min_closed_trades: int = 20,
    split_fraction: float = 0.5,
) -> LearningMeasurementReport:
    """시점별 성적 추이 + 초기 vs 이후 비교 + 과적합 경계. 정직한 측정만.

    min_closed_trades: 이만큼 청산 거래가 없으면 INSUFFICIENT_DATA (착시 방지).
    split_fraction: 초기 vs 이후 분할점 (0.5 = 전반/후반).
    """
    recs = sorted(records, key=lambda r: r.ts_epoch)
    n = len(recs)
    closed_total = sum(1 for r in recs if r.realized_pnl_pct is not None)
    notes: list[str] = []

    if closed_total < min_closed_trades:
        return LearningMeasurementReport(
            verdict=LearningVerdict.INSUFFICIENT_DATA,
            periods=tuple(), initial=None, later=None,
            delta_avg_pnl_pp=None, delta_win_rate_pp=None, delta_quality=None,
            cost_wall_bps=COST_WALL_ROUNDTRIP_BPS, overfit_suspected=False, n_total=n,
            notes=(f"청산 거래 {closed_total}건 < 최소 {min_closed_trades}건 — 학습 판단 불가(표본 부족).",),
        )

    # 시간순 n_buckets 등분 (성적 추이)
    periods: list[PeriodStats] = []
    for i in range(n_buckets):
        a = i * n // n_buckets
        b = (i + 1) * n // n_buckets
        seg = recs[a:b]
        if seg:
            periods.append(_stats(f"P{i+1}", seg))

    # 초기 vs 이후
    split = int(n * split_fraction)
    initial = _stats("initial", recs[:split])
    later = _stats("later", recs[split:])

    def _d(a, b):
        return None if (a is None or b is None) else b - a
    delta_pnl = _d(initial.avg_pnl_pct, later.avg_pnl_pct)
    delta_win = _d(initial.win_rate, later.win_rate)
    delta_q = _d(initial.avg_quality, later.avg_quality)

    # 과적합 경계: 마지막 버킷만 좋고 직전 버킷은 나쁘면 = 최근에만 맞춰진 것
    overfit = False
    if len(periods) >= 3:
        last = periods[-1].avg_pnl_pct
        prev = periods[-2].avg_pnl_pct
        early_avg = _mean([p.avg_pnl_pct for p in periods[:-1]])
        if (last is not None and early_avg is not None
                and last > 0 and early_avg <= 0
                and (prev is None or prev <= 0)):
            overfit = True
            notes.append("최근 구간만 +이고 그 이전 구간들은 ≤0 — 학습이 아니라 과적합(최근 데이터 적응) 의심.")

    # verdict — 데이터가 말하는 대로 (개선 가정 0)
    if overfit:
        verdict = LearningVerdict.OVERFIT_SUSPECTED
    elif delta_pnl is None:
        verdict = LearningVerdict.INSUFFICIENT_DATA
    elif delta_pnl <= 0 and (delta_win is None or delta_win <= 0):
        verdict = LearningVerdict.DEGRADING if delta_pnl < -1e-9 else LearningVerdict.NO_TREND
    else:
        # 나아짐 — 비용벽(0선, 실현손익이라 비용 반영) 넘었는지로 분기
        later_above = later.avg_pnl_pct is not None and later.avg_pnl_pct > 0
        verdict = (LearningVerdict.IMPROVING_ABOVE_WALL if later_above
                   else LearningVerdict.IMPROVING_BELOW_WALL)
        if not later_above:
            notes.append("성적은 나아지는 추세지만 비용후 평균손익이 아직 0 이하 — 비용벽 미돌파.")

    notes.append("본 측정은 학습 성공을 가정하지 않음 — 성적 추이를 그대로 기록. "
                 "표본이 작으면 추세는 노이즈일 수 있음. paper 며칠~몇주 누적 후 재판단 권장.")

    return LearningMeasurementReport(
        verdict=verdict, periods=tuple(periods), initial=initial, later=later,
        delta_avg_pnl_pp=(round(delta_pnl * 100, 3) if delta_pnl is not None else None),
        delta_win_rate_pp=(round(delta_win * 100, 2) if delta_win is not None else None),
        delta_quality=(round(delta_q, 2) if delta_q is not None else None),
        cost_wall_bps=COST_WALL_ROUNDTRIP_BPS, overfit_suspected=overfit, n_total=n,
        notes=tuple(notes),
    )


def report_to_dict(r: LearningMeasurementReport) -> dict:
    def _p(p: PeriodStats | None):
        if p is None:
            return None
        return {
            "label": p.label, "n_decisions": p.n_decisions, "n_closed_trades": p.n_closed_trades,
            "win_rate": p.win_rate, "avg_pnl_pct": p.avg_pnl_pct, "median_pnl_pct": p.median_pnl_pct,
            "avg_confidence": p.avg_confidence, "avg_quality": p.avg_quality,
            "above_cost_wall": p.above_cost_wall, "from": p.from_iso, "to": p.to_iso,
        }
    return {
        "verdict": r.verdict.value,
        "periods": [_p(p) for p in r.periods],
        "initial": _p(r.initial), "later": _p(r.later),
        "delta_avg_pnl_pp": r.delta_avg_pnl_pp, "delta_win_rate_pp": r.delta_win_rate_pp,
        "delta_quality": r.delta_quality,
        "cost_wall_bps": r.cost_wall_bps, "overfit_suspected": r.overfit_suspected,
        "n_total": r.n_total, "notes": list(r.notes),
        "assumes_improvement": r.assumes_improvement,
        "is_order_signal": r.is_order_signal, "is_live_authorization": r.is_live_authorization,
        "auto_apply_allowed": r.auto_apply_allowed,
    }
