"""Alpha Decay Monitor (#77) — 전략별 알파 감쇠 추적 read-only evaluator.

한때 잘 되던 단타 전략이 *계속 통하지 않을 수* 있다. 본 모듈은 전략의 baseline
대비 최근 성과 변화를 점수화해 자동 비활성 *후보*를 표시한다 — **자동 비활성/
삭제 절대 금지**, 운영자 검토용 read-only 분석.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / 외부 HTTP / AI provider import 0건.
- DB / settings mutate 0건 (evaluator는 입력 DTO만 사용).
- *전략 비활성 / 삭제 / promotion gate 자동 토글 금지* — 본 모듈은 *제안*만.
- BUY/SELL/HOLD 주문 신호 생성 0건.

invariant (코드 단 강제):
- `AlphaDecayResult.is_order_signal=False` 항상.
- `AlphaDecayResult.auto_disable=False` 항상.
- `AlphaDecayResult.auto_apply_allowed=False` 항상.
- True 생성 시 dataclass `__post_init__` ValueError.

단기 부진과 구조적 성능저하를 구분 (`AlphaDecayKind`):
- `SHORT_TERM_DRAWDOWN`     : 최근 1~2주 부진, baseline 대비 1~2 지표만 악화
- `REGIME_MISMATCH`         : market_regime이 baseline 시점과 다름 (운영자 메타)
- `STRUCTURAL_DECAY`        : 여러 지표 동시 악화 + regime 동일 → 구조적
- `DATA_QUALITY_ISSUE`      : data_quality_score 낮음 / freshness drift
- `INSUFFICIENT_DATA`       : 최근 표본 부족 (recent_trade_count < 임계)
- `NONE`                    : HEALTHY
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / thresholds ----------


class AlphaDecayStatus(StrEnum):
    """4단계 + 표본 부족 별도 상태."""
    HEALTHY              = "HEALTHY"               # score 0~24
    WATCH                = "WATCH"                 # 25~49
    DECAY_WARNING        = "DECAY_WARNING"         # 50~74
    DISABLE_CANDIDATE    = "DISABLE_CANDIDATE"     # 75~100
    INSUFFICIENT_DATA    = "INSUFFICIENT_DATA"     # 표본 부족 → score 산정 불가


class AlphaDecayKind(StrEnum):
    """단기 부진 vs 구조적 성능저하 분류 — advisory tag (주문 신호 X)."""
    NONE                 = "NONE"
    SHORT_TERM_DRAWDOWN  = "SHORT_TERM_DRAWDOWN"
    REGIME_MISMATCH      = "REGIME_MISMATCH"
    STRUCTURAL_DECAY     = "STRUCTURAL_DECAY"
    DATA_QUALITY_ISSUE   = "DATA_QUALITY_ISSUE"
    INSUFFICIENT_DATA    = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class AlphaDecayThresholds:
    """평가 임계. 운영자 override 가능."""
    # 표본.
    min_recent_trades:             int   = 20

    # 점수 임계 (status 분류).
    score_watch:                   int   = 25
    score_decay_warning:           int   = 50
    score_disable_candidate:       int   = 75

    # 지표 임계 (status 부여 보조).
    min_profit_factor:             float = 1.2
    expectancy_flip_to_negative:   float = 0.0
    mdd_worsening_ratio:           float = 1.5     # baseline 대비 50% 이상 악화
    consec_losses_doubled_ratio:   float = 2.0

    # 점수 가중치 (감점 폭, 합이 100을 넘을 수 있음 — clamp).
    weight_expectancy_drop:        float = 25.0
    weight_expectancy_flip:        float = 25.0    # 양수→음수면 *추가*
    weight_pf_drop:                float = 20.0
    weight_pf_below_min:           float = 15.0    # PF<1.2면 추가
    weight_winrate_drop:           float = 10.0
    weight_mdd_worsen:             float = 15.0
    weight_consec_losses_increase: float = 10.0
    weight_data_quality_issue:     float = 15.0
    weight_regime_change:          float = 5.0

    # 구조적 분류 임계: 동시에 N개 이상 지표 악화면 STRUCTURAL.
    structural_min_degraded_signals: int = 3

    # data quality.
    data_quality_warn_score:       float = 75.0
    data_quality_block_score:      float = 60.0


# ---------- DTO ----------


@dataclass(frozen=True)
class StrategyMetricsSnapshot:
    """baseline 또는 recent 스냅샷 — 백테스트 결과 / 실 운용 통계 모두 입력 가능.

    음수 expectancy / 0 trade_count 도 허용 (호출자가 평가용으로 채움).
    """
    trade_count:           int   = 0
    expectancy:            float = 0.0
    profit_factor:         float | None = None
    win_rate:              float = 0.0           # 0~1
    max_drawdown:          int   = 0             # 절댓값 (음수가 아니라 양의 정수)
    max_consecutive_losses: int  = 0

    @property
    def has_data(self) -> bool:
        return self.trade_count > 0


@dataclass(frozen=True)
class AlphaDecayInput:
    """전략 알파 감쇠 평가 입력.

    필수:
    - strategy_name
    - baseline: 백테스트 / 검증 단계 통과 시점의 성과 스냅샷
    - recent  : 최근 운용 (paper / shadow / live) 통계 스냅샷

    옵션:
    - baseline_regime / recent_regime: 시장 regime (분류용)
    - recent_data_quality_score: 0~100 (data quality #21 carry)
    - operator_note: 운영자 사후 메모 (사람 관측 — score 계산엔 미사용)
    """
    strategy_name:                 str
    baseline:                      StrategyMetricsSnapshot
    recent:                        StrategyMetricsSnapshot
    baseline_regime:               str | None = None
    recent_regime:                 str | None = None
    recent_data_quality_score:     float | None = None
    operator_note:                 str | None = None


@dataclass
class AlphaDecayResult:
    """평가 결과.

    invariants (코드 단 강제):
    - `is_order_signal=False` 항상.
    - `auto_disable=False` 항상 — 자동 비활성 절대 금지.
    - `auto_apply_allowed=False` 항상 — 결과 적용은 *수동*.
    """
    strategy_name:           str
    score:                   int        # 0~100 (INSUFFICIENT_DATA면 -1)
    status:                  AlphaDecayStatus
    kind:                    AlphaDecayKind
    degraded_signals:        list[str] = field(default_factory=list)
    cautions:                list[str] = field(default_factory=list)
    recommended_action:      str       = ""
    metrics:                 dict[str, Any] = field(default_factory=dict)
    thresholds:              dict[str, Any] = field(default_factory=dict)
    is_order_signal:         bool = False
    auto_disable:            bool = False
    auto_apply_allowed:      bool = False
    generated_at:            datetime  = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "AlphaDecayResult.is_order_signal must be False — "
                "alpha decay monitor does not produce BUY/SELL/HOLD signals."
            )
        if self.auto_disable is not False:
            raise ValueError(
                "AlphaDecayResult.auto_disable must be False — "
                "alpha decay monitor does not auto-disable strategies. "
                "Operator review is required."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "AlphaDecayResult.auto_apply_allowed must be False — "
                "alpha decay analysis is advisory only."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":         self.strategy_name,
            "score":                 self.score,
            "status":                self.status.value,
            "kind":                  self.kind.value,
            "degraded_signals":      list(self.degraded_signals),
            "cautions":              list(self.cautions),
            "recommended_action":    self.recommended_action,
            "metrics":               dict(self.metrics),
            "thresholds":            dict(self.thresholds),
            "is_order_signal":       self.is_order_signal,
            "auto_disable":          self.auto_disable,
            "auto_apply_allowed":    self.auto_apply_allowed,
            "live_flag_changed":     False,
            "mode_changed":          False,
            "generated_at":          self.generated_at.isoformat(),
        }


# ---------- score ----------


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def compute_alpha_decay_score(
    baseline: StrategyMetricsSnapshot,
    recent:   StrategyMetricsSnapshot,
    thresholds: AlphaDecayThresholds | None = None,
    *,
    recent_data_quality_score: float | None = None,
    regime_changed:            bool = False,
) -> tuple[int, list[str]]:
    """0~100 score + degraded_signal 라벨 리스트 반환.

    score는 *감점 누적*. 각 신호가 가중치만큼 더해지고 100으로 clamp.
    """
    th = thresholds or AlphaDecayThresholds()
    score = 0.0
    signals: list[str] = []

    # 1) expectancy
    if baseline.expectancy > 0 and recent.expectancy < baseline.expectancy:
        # 비율 기반 — 0~1 사이 감소율을 weight에 곱.
        drop = (baseline.expectancy - recent.expectancy) / max(1.0, abs(baseline.expectancy))
        delta = th.weight_expectancy_drop * _clamp(drop, 0.0, 1.0)
        score += delta
        signals.append("expectancy_drop")
        # 양수 → 음수 flip은 *추가* 가중치.
        if recent.expectancy <= th.expectancy_flip_to_negative:
            score += th.weight_expectancy_flip
            signals.append("expectancy_flip_to_negative")

    # 2) profit factor
    base_pf   = baseline.profit_factor
    recent_pf = recent.profit_factor
    if base_pf is not None and recent_pf is not None and recent_pf < base_pf:
        ratio = (base_pf - recent_pf) / max(0.1, base_pf)
        score += th.weight_pf_drop * _clamp(ratio, 0.0, 1.0)
        signals.append("pf_drop")
    if recent_pf is not None and recent_pf < th.min_profit_factor:
        score += th.weight_pf_below_min
        signals.append("pf_below_min")

    # 3) win rate
    if baseline.win_rate > 0 and recent.win_rate < baseline.win_rate:
        ratio = (baseline.win_rate - recent.win_rate) / max(0.01, baseline.win_rate)
        score += th.weight_winrate_drop * _clamp(ratio, 0.0, 1.0)
        signals.append("winrate_drop")

    # 4) MDD 악화
    if baseline.max_drawdown > 0 and recent.max_drawdown > baseline.max_drawdown:
        ratio = recent.max_drawdown / max(1, baseline.max_drawdown)
        if ratio >= th.mdd_worsening_ratio:
            score += th.weight_mdd_worsen
            signals.append("mdd_worsen")

    # 5) 연속 손실 증가
    if (baseline.max_consecutive_losses > 0
            and recent.max_consecutive_losses
                 >= baseline.max_consecutive_losses * th.consec_losses_doubled_ratio):
        score += th.weight_consec_losses_increase
        signals.append("consec_losses_increase")

    # 6) data quality
    if (recent_data_quality_score is not None
            and recent_data_quality_score < th.data_quality_warn_score):
        score += th.weight_data_quality_issue
        signals.append("data_quality_issue")

    # 7) regime change
    if regime_changed:
        score += th.weight_regime_change
        signals.append("regime_change")

    return int(round(_clamp(score, 0.0, 100.0))), signals


# ---------- classify / status ----------


def _classify_kind(
    *,
    signals: list[str],
    regime_changed: bool,
    insufficient: bool,
    data_quality_issue: bool,
    thresholds: AlphaDecayThresholds,
) -> AlphaDecayKind:
    """단기 부진 vs 구조적 성능저하 vs ...

    우선순위:
    1. INSUFFICIENT_DATA  : 표본 부족이면 다른 분류 비활성
    2. DATA_QUALITY_ISSUE : data quality 낮으면
    3. REGIME_MISMATCH    : regime 변경이 main 사유
    4. STRUCTURAL_DECAY   : 동시에 ≥3개 지표 악화
    5. SHORT_TERM_DRAWDOWN: 1~2개 지표만 악화
    6. NONE
    """
    if insufficient:
        return AlphaDecayKind.INSUFFICIENT_DATA
    if data_quality_issue:
        return AlphaDecayKind.DATA_QUALITY_ISSUE

    # 핵심 지표만 카운트 (data_quality / regime_change 제외).
    core = [s for s in signals if s not in ("regime_change", "data_quality_issue")]
    n = len(core)

    if regime_changed and n <= 1:
        return AlphaDecayKind.REGIME_MISMATCH
    if n >= thresholds.structural_min_degraded_signals:
        return AlphaDecayKind.STRUCTURAL_DECAY
    if n >= 1:
        return AlphaDecayKind.SHORT_TERM_DRAWDOWN
    return AlphaDecayKind.NONE


def _status_for_score(score: int, thresholds: AlphaDecayThresholds) -> AlphaDecayStatus:
    if score >= thresholds.score_disable_candidate:
        return AlphaDecayStatus.DISABLE_CANDIDATE
    if score >= thresholds.score_decay_warning:
        return AlphaDecayStatus.DECAY_WARNING
    if score >= thresholds.score_watch:
        return AlphaDecayStatus.WATCH
    return AlphaDecayStatus.HEALTHY


# ---------- evaluator ----------


def evaluate_alpha_decay(
    inp: AlphaDecayInput,
    thresholds: AlphaDecayThresholds | None = None,
) -> AlphaDecayResult:
    """전략 알파 감쇠 평가 — 외부 시스템 영향 0건.

    PASS / DISABLE_CANDIDATE 라벨은 *제안*일 뿐. 실제 비활성화 / 삭제는 운영자
    수동 결정. RiskManager / PermissionGate / OrderExecutor 우회 금지.
    """
    th = thresholds or AlphaDecayThresholds()
    cautions: list[str] = []

    insufficient = (
        not inp.recent.has_data or inp.recent.trade_count < th.min_recent_trades
    )

    # data quality issue 단독 확인 (score 계산과는 별개 분류용).
    data_quality_issue_strong = (
        inp.recent_data_quality_score is not None
        and inp.recent_data_quality_score < th.data_quality_block_score
    )

    regime_changed = (
        inp.baseline_regime is not None
        and inp.recent_regime is not None
        and inp.baseline_regime != inp.recent_regime
    )

    if insufficient:
        # 표본이 부족하면 score를 산정하지 *않는다*.
        cautions.append(
            f"recent trade_count {inp.recent.trade_count} < "
            f"{th.min_recent_trades} — 표본 부족, 알파 감쇠 측정 불가."
        )
        score   = -1
        status  = AlphaDecayStatus.INSUFFICIENT_DATA
        kind    = AlphaDecayKind.INSUFFICIENT_DATA
        signals: list[str] = []
    else:
        score, signals = compute_alpha_decay_score(
            inp.baseline, inp.recent, th,
            recent_data_quality_score=inp.recent_data_quality_score,
            regime_changed=regime_changed,
        )
        status = _status_for_score(score, th)
        kind   = _classify_kind(
            signals=signals,
            regime_changed=regime_changed,
            insufficient=False,
            data_quality_issue=data_quality_issue_strong,
            thresholds=th,
        )

    if data_quality_issue_strong:
        cautions.append(
            f"recent data quality {inp.recent_data_quality_score} < "
            f"{th.data_quality_block_score} — 데이터 품질이 낮아 결과 신뢰도 저하."
        )
    if regime_changed:
        cautions.append(
            f"market regime 변경 ({inp.baseline_regime} → {inp.recent_regime}) — "
            "단기 부진을 구조적 감쇠로 단정 짓지 않도록 주의."
        )

    return AlphaDecayResult(
        strategy_name=inp.strategy_name,
        score=score,
        status=status,
        kind=kind,
        degraded_signals=signals,
        cautions=cautions,
        recommended_action=_recommendation_for(status, kind),
        metrics={
            "baseline": _snapshot_to_dict(inp.baseline),
            "recent":   _snapshot_to_dict(inp.recent),
            "baseline_regime":            inp.baseline_regime,
            "recent_regime":              inp.recent_regime,
            "regime_changed":             regime_changed,
            "recent_data_quality_score":  inp.recent_data_quality_score,
            "expectancy_delta":           round(
                inp.recent.expectancy - inp.baseline.expectancy, 4,
            ),
            "pf_delta": (
                None if (inp.baseline.profit_factor is None
                         or inp.recent.profit_factor is None)
                else round(inp.recent.profit_factor - inp.baseline.profit_factor, 4)
            ),
            "winrate_delta":              round(
                inp.recent.win_rate - inp.baseline.win_rate, 4,
            ),
            "mdd_ratio": (
                None if inp.baseline.max_drawdown <= 0
                else round(inp.recent.max_drawdown / inp.baseline.max_drawdown, 4)
            ),
            "consec_losses_baseline":     inp.baseline.max_consecutive_losses,
            "consec_losses_recent":       inp.recent.max_consecutive_losses,
            "operator_note":              inp.operator_note,
        },
        thresholds={
            "min_recent_trades":         th.min_recent_trades,
            "score_watch":               th.score_watch,
            "score_decay_warning":       th.score_decay_warning,
            "score_disable_candidate":   th.score_disable_candidate,
            "min_profit_factor":         th.min_profit_factor,
            "mdd_worsening_ratio":       th.mdd_worsening_ratio,
            "structural_min_degraded_signals": th.structural_min_degraded_signals,
        },
    )


def _snapshot_to_dict(s: StrategyMetricsSnapshot) -> dict[str, Any]:
    return {
        "trade_count":            s.trade_count,
        "expectancy":             round(s.expectancy, 4),
        "profit_factor":          s.profit_factor,
        "win_rate":               round(s.win_rate, 4),
        "max_drawdown":           s.max_drawdown,
        "max_consecutive_losses": s.max_consecutive_losses,
    }


def _recommendation_for(
    status: AlphaDecayStatus, kind: AlphaDecayKind,
) -> str:
    if status == AlphaDecayStatus.HEALTHY:
        return "전략 정상. 운용 지속 + 정기 모니터링."
    if status == AlphaDecayStatus.WATCH:
        return (
            "주의 단계. 다음 운용 구간에서 지표 회복 여부 확인. "
            "**자동 비활성/삭제 금지** — 운영자 검토만."
        )
    if status == AlphaDecayStatus.DECAY_WARNING:
        if kind == AlphaDecayKind.REGIME_MISMATCH:
            return (
                "REGIME_MISMATCH — market regime 변화로 인한 부진 가능성. "
                "regime 정상화 시 회복 여부 확인 후 결정. **자동 비활성 금지**."
            )
        if kind == AlphaDecayKind.STRUCTURAL_DECAY:
            return (
                "STRUCTURAL_DECAY — 다지표 동시 악화. "
                "Strategy Researcher Agent(#55) 분석 + 별도 PR 검토 권장. "
                "**자동 비활성 금지** — 운영자 승인 필요."
            )
        return (
            "DECAY_WARNING — 최근 부진. 단기 부진과 구조적 저하 구분 위해 "
            "추가 운용 또는 backtest 재검증 권장. **자동 비활성 금지**."
        )
    if status == AlphaDecayStatus.DISABLE_CANDIDATE:
        return (
            "DISABLE_CANDIDATE — *비활성 후보*. **자동 비활성/삭제 절대 금지**. "
            "운영자 검토 + Strategy Researcher Agent(#55) 분석 + 별도 승인 PR 필요. "
            "전략 삭제/중단은 수동 승인."
        )
    return "INSUFFICIENT_DATA — 추가 운용으로 표본 확보 후 재평가."
