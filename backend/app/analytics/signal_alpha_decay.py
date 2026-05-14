"""Signal Alpha Decay (#94) — *신호 단위* 알파 감쇠 분석.

본 모듈은 *진입 신호가 생성된 시점 t=0* 으로부터 시간이 흐를수록 (1분, 3분,
5분, 10분, 30분, 60분 등) 기대수익이 얼마나 빠르게 감소하는지 측정한다.
오래된 신호를 그대로 진입 근거로 쓰는 것을 *경고* 한다.

본 모듈은 #77 `app/governance/alpha_decay.py` 의 *전략 단위 알파 감쇠* 와는
**완전히 다른 개념**이다:

| 항목 | #77 governance/alpha_decay | #94 본 모듈 (analytics/signal_alpha_decay) |
|---|---|---|
| 단위 | 전략 (strategy-level) | 개별 신호 (signal-level) |
| 시간 척도 | 일 / 주 (baseline vs recent) | 분 / 시간 (1m ~ 60m) |
| 비교 | 검증 단계 vs 최근 운용 6 metric | 신호 t=0 vs 시간 경과 후 기대수익 |
| 목적 | 전략 자체의 알파 감쇠 추적 | 개별 신호의 *신선도* 평가 |

두 분석은 *상호 보완* — 운영자는 #77 (전략 건강) + #94 (신호 신선도) 모두 확인.

CLAUDE.md 절대 원칙 (코드 단 + 정적 grep 가드로 강제):

- 본 모듈은 broker / OrderExecutor / route_order / paper_trader /
  외부 HTTP / AI SDK / settings reader import 0건.
- 데이터베이스 쓰기 작업 0건 (모든 정적 grep 가드는 test 모듈에 정의).
- 안전 flag (실거래 / AI 자동실행 / 선물 LIVE) 변경 0건.
- `SignalAlphaDecayResult.is_order_signal=True` 생성 불가 (ValueError).
- `SignalAlphaDecayResult.auto_apply_allowed=True` 생성 불가 (ValueError).
- `SignalAlphaDecayResult.is_live_authorization=True` 생성 불가 (ValueError).

verdict 4단계 (`FreshnessVerdict`):

- `FRESH`     : 신호 생성 직후 ~ 짧은 시간 (default ≤ 1분) — 진입 근거로 유효
- `DECAYING`  : 1분 ~ 30분 — 진입 가능하나 decay 진행 중, 주의
- `STALE`     : 30분 ~ 60분 — 진입 근거로 *권장하지 않음*
- `EXPIRED`   : > 60분 또는 `max_actionable_age` 초과 — 진입 *금지*
- `UNKNOWN`   : 표본 부족 / 입력 없음

decay_score 0~100 (높을수록 신호가 t=0 대비 잘 유지됨):
- 100        : 모든 bucket 이 t=0 의 100% 이상 (decay 없음)
- 70~99      : 일부 bucket 이 70~99% (mild decay) — WARN
- 30~69      : 50% 수준 (significant decay) — WARN 또는 FAIL
- 0~29       : 큰 폭 감소 (severe decay) — FAIL

**본 모듈은 실거래 실행 기능을 추가하지 않는다** — advisory 분석만.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums ----------


class FreshnessVerdict(StrEnum):
    """단일 신호의 *현재 시점* 신선도 verdict.

    BUY/SELL/HOLD 값 0개 — 본 verdict 는 *주문 신호가 아니다*.
    """
    FRESH    = "FRESH"
    DECAYING = "DECAYING"
    STALE    = "STALE"
    EXPIRED  = "EXPIRED"
    UNKNOWN  = "UNKNOWN"


class DecaySeverity(StrEnum):
    """bucket / 전체 결과의 severity. BUY/SELL/HOLD 값 0개."""
    PASS    = "PASS"
    WARN    = "WARN"
    FAIL    = "FAIL"
    UNKNOWN = "UNKNOWN"


# ---------- thresholds ----------


@dataclass(frozen=True)
class SignalAlphaDecayThresholds:
    """신호 알파 감쇠 임계치. 운영자가 평가 시 override 가능.

    기본값은 *국내주식 단타* 의 보수적 권장값:
    - max_actionable_age_minutes=30: 30분 이내가 *가장 일반적인 진입 윈도우*
    - decay_warn_pct=70.0: t=0 대비 70% 미만이면 WARN
    - decay_fail_pct=30.0: t=0 대비 30% 미만이면 FAIL
    - min_sample_count=10: 통계 신뢰성 최소 표본
    """
    max_actionable_age_minutes: int = 30
    decay_warn_pct:             float = 70.0
    decay_fail_pct:             float = 30.0
    min_sample_count:           int = 10
    # 신선도 verdict 단계의 시간 임계치 (분).
    fresh_max_minutes:          int = 1
    decaying_max_minutes:       int = 30
    stale_max_minutes:          int = 60
    # decay_score 가 본 값 미만이면 verdict 가 EXPIRED 로 격하.
    min_decay_score_for_actionable: float = 30.0


# ---------- sample / bucket ----------


@dataclass(frozen=True)
class SignalSamplePoint:
    """단일 age bucket 의 *과거 관측치* — 호출자가 collector 단계에서 채움.

    예) age_minutes=5, mean_return_bps=12.3, sample_count=42 의 의미:
      "이 전략의 신호 직후 5분 시점에서, 과거 42개 표본의 평균 수익률은 12.3 bps."
    """
    age_minutes:     int
    mean_return_bps: float            # basis points (1 bps = 0.01%)
    sample_count:    int = 0
    pass_rate:       float | None = None   # target hit rate before stop (옵션)
    std_return_bps:  float = 0.0

    def __post_init__(self) -> None:
        if self.age_minutes < 0:
            raise ValueError(f"age_minutes must be >= 0, got {self.age_minutes}")
        if self.sample_count < 0:
            raise ValueError(f"sample_count must be >= 0, got {self.sample_count}")


@dataclass(frozen=True)
class SignalDecayBucket:
    """평가 결과의 단일 bucket — sample 을 입력 받아 *상대값 / severity* 까지 계산.

    `relative_to_t0_pct` = mean_return_bps / t=0 mean * 100.
    """
    label:               str
    age_minutes:         int
    mean_return_bps:     float
    sample_count:        int
    relative_to_t0_pct:  float
    severity:            DecaySeverity
    note:                str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "label":              self.label,
            "age_minutes":        self.age_minutes,
            "mean_return_bps":    self.mean_return_bps,
            "sample_count":       self.sample_count,
            "relative_to_t0_pct": self.relative_to_t0_pct,
            "severity":           self.severity.value,
            "note":               self.note,
        }


# ---------- input DTO ----------


@dataclass(frozen=True)
class SignalAlphaDecayInput:
    """평가 입력. 호출자가 historical sample 들을 채워서 전달.

    모든 필드는 *입력값 carry* — 본 모듈은 어떤 값도 *변경하지 않는다*.
    """
    strategy_name:    str
    samples:          tuple[SignalSamplePoint, ...] = ()
    strict:           bool = False

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name must be non-empty")


# ---------- result DTO ----------


@dataclass
class SignalAlphaDecayResult:
    """평가 결과.

    invariants (코드 단 강제):
    - `is_order_signal=False` 항상.
    - `auto_apply_allowed=False` 항상.
    - `is_live_authorization=False` 항상.
    """
    strategy_name:                 str
    buckets:                       list[SignalDecayBucket] = field(default_factory=list)
    decay_score:                   float = 0.0
    max_actionable_age_minutes:    int = 30
    verdict_overall:               FreshnessVerdict = FreshnessVerdict.UNKNOWN
    warnings:                      list[str] = field(default_factory=list)
    advice:                        list[str] = field(default_factory=list)
    insufficient_data:             bool = False
    is_order_signal:               bool = False
    auto_apply_allowed:            bool = False
    is_live_authorization:         bool = False
    generated_at:                  datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "SignalAlphaDecayResult.is_order_signal must be False — "
                "this module does not produce BUY/SELL/HOLD signals."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "SignalAlphaDecayResult.auto_apply_allowed must be False — "
                "this module never auto-applies settings or rules."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "SignalAlphaDecayResult.is_live_authorization must be False — "
                "this module is not a live trading authorization gate."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":              self.strategy_name,
            "buckets":                    [b.to_dict() for b in self.buckets],
            "decay_score":                self.decay_score,
            "max_actionable_age_minutes": self.max_actionable_age_minutes,
            "verdict_overall":            self.verdict_overall.value,
            "warnings":                   list(self.warnings),
            "advice":                     list(self.advice),
            "insufficient_data":          self.insufficient_data,
            "is_order_signal":            self.is_order_signal,
            "auto_apply_allowed":         self.auto_apply_allowed,
            "is_live_authorization":      self.is_live_authorization,
            "generated_at":               self.generated_at.isoformat(),
        }


# ---------- helpers ----------


def _label_for_age(age_minutes: int) -> str:
    if age_minutes < 60:
        return f"{age_minutes}m"
    hours, mins = divmod(age_minutes, 60)
    if mins == 0:
        return f"{hours}h"
    return f"{hours}h{mins}m"


def _severity_for_relative_pct(
    relative_pct: float, th: SignalAlphaDecayThresholds,
) -> DecaySeverity:
    """t=0 대비 % → severity 변환."""
    if relative_pct < th.decay_fail_pct:
        return DecaySeverity.FAIL
    if relative_pct < th.decay_warn_pct:
        return DecaySeverity.WARN
    return DecaySeverity.PASS


def _aggregate_decay_score(buckets: list[SignalDecayBucket]) -> float:
    """전체 decay_score 계산 — 모든 bucket 의 `relative_to_t0_pct` 평균.

    Bucket 이 비어있으면 0.0. 100 을 넘으면 100 으로 clamp (드물지만 t=0 대비
    개선되는 경우가 있을 수 있음 — UI 표시 단순화 위해 clamp).
    """
    if not buckets:
        return 0.0
    avg = sum(b.relative_to_t0_pct for b in buckets) / len(buckets)
    return max(0.0, min(100.0, avg))


def evaluate_signal_alpha_decay(
    inp: SignalAlphaDecayInput,
    thresholds: SignalAlphaDecayThresholds | None = None,
) -> SignalAlphaDecayResult:
    """signal alpha decay 평가. read-only — 외부 시스템 영향 0건.

    samples 가 충분하지 않으면 `insufficient_data=True` 로 verdict=UNKNOWN.
    """
    th = thresholds or SignalAlphaDecayThresholds()
    samples = sorted(inp.samples, key=lambda s: s.age_minutes)

    # t=0 sample 식별 — 가장 어린 age 또는 age=0.
    if not samples:
        return SignalAlphaDecayResult(
            strategy_name=inp.strategy_name,
            buckets=[],
            decay_score=0.0,
            max_actionable_age_minutes=th.max_actionable_age_minutes,
            verdict_overall=FreshnessVerdict.UNKNOWN,
            warnings=["입력 sample 0개 — 평가 불가"],
            advice=["진입 후 1분 / 3분 / 5분 / 10분 / 30분 / 60분 시점 표본 수집 필요"],
            insufficient_data=True,
        )

    base = samples[0]
    if base.sample_count < th.min_sample_count:
        return SignalAlphaDecayResult(
            strategy_name=inp.strategy_name,
            buckets=[],
            decay_score=0.0,
            max_actionable_age_minutes=th.max_actionable_age_minutes,
            verdict_overall=FreshnessVerdict.UNKNOWN,
            warnings=[
                f"t=0 sample {base.sample_count}개 < 최소 {th.min_sample_count}개"
            ],
            advice=["표본 보강 후 재평가"],
            insufficient_data=True,
        )

    base_return = base.mean_return_bps
    if abs(base_return) < 1e-9:
        return SignalAlphaDecayResult(
            strategy_name=inp.strategy_name,
            buckets=[],
            decay_score=0.0,
            max_actionable_age_minutes=th.max_actionable_age_minutes,
            verdict_overall=FreshnessVerdict.UNKNOWN,
            warnings=[
                "t=0 mean_return_bps ≈ 0 — 상대값 비교 불가 "
                "(0 으로 나누기 위험)"
            ],
            advice=[
                "신호 강도가 너무 약함 — 전략 검토 또는 표본 추가 수집 필요"
            ],
            insufficient_data=True,
        )

    # bucket 계산.
    buckets: list[SignalDecayBucket] = []
    for s in samples:
        rel_pct = (s.mean_return_bps / base_return) * 100.0
        sev = _severity_for_relative_pct(rel_pct, th)
        note = ""
        if s.sample_count < th.min_sample_count:
            note = (
                f"표본 {s.sample_count}개 < 최소 {th.min_sample_count}개 — "
                "상대값 신뢰 낮음"
            )
            sev = DecaySeverity.WARN if sev is DecaySeverity.PASS else sev
        buckets.append(SignalDecayBucket(
            label=_label_for_age(s.age_minutes),
            age_minutes=s.age_minutes,
            mean_return_bps=s.mean_return_bps,
            sample_count=s.sample_count,
            relative_to_t0_pct=rel_pct,
            severity=sev,
            note=note,
        ))

    decay_score = _aggregate_decay_score(buckets)

    # 전체 verdict — *가장 마지막 표본 시점* 기준으로 평가하지 않고, *전체 bucket
    # 의 평균 decay_score* 와 *최악 severity* 를 합쳐서 판단.
    warnings: list[str] = []
    advice: list[str] = []
    fail_buckets = [b for b in buckets if b.severity is DecaySeverity.FAIL]
    warn_buckets = [b for b in buckets if b.severity is DecaySeverity.WARN]

    if fail_buckets:
        warnings.append(
            f"{len(fail_buckets)}개 bucket 에서 FAIL — t=0 대비 "
            f"{th.decay_fail_pct:.0f}% 미만 감소"
        )
    if warn_buckets:
        warnings.append(
            f"{len(warn_buckets)}개 bucket 에서 WARN — t=0 대비 "
            f"{th.decay_warn_pct:.0f}% 미만"
        )

    # 전체 verdict 분기.
    if decay_score >= th.decay_warn_pct:
        verdict = FreshnessVerdict.FRESH
    elif decay_score >= th.decay_fail_pct:
        verdict = FreshnessVerdict.DECAYING
        advice.append(
            f"신호 평균 decay_score={decay_score:.1f} — 진입 시 보수적 사이즈 권장"
        )
    elif decay_score >= th.min_decay_score_for_actionable:
        verdict = FreshnessVerdict.STALE
        advice.append(
            f"신호 평균 decay_score={decay_score:.1f} — 진입 *권장하지 않음*"
        )
    else:
        verdict = FreshnessVerdict.EXPIRED
        advice.append(
            f"신호 평균 decay_score={decay_score:.1f} < "
            f"{th.min_decay_score_for_actionable} — *진입 금지*"
        )

    if inp.strict and (fail_buckets or warn_buckets):
        advice.append(
            "strict=true 입력 — WARN / FAIL bucket 이 있으면 보수적 처리 권장"
        )

    return SignalAlphaDecayResult(
        strategy_name=inp.strategy_name,
        buckets=buckets,
        decay_score=decay_score,
        max_actionable_age_minutes=th.max_actionable_age_minutes,
        verdict_overall=verdict,
        warnings=warnings,
        advice=advice,
        insufficient_data=False,
    )


# ---------- realtime helpers ----------


def compute_signal_age_minutes(
    signal_generated_at: datetime,
    now: datetime | None = None,
) -> int:
    """신호 생성 시점부터 현재까지 경과 분. 음수면 0 으로 clamp.

    timezone-aware datetime 필수 — naive 면 UTC 로 가정.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    sigtime = signal_generated_at
    if sigtime.tzinfo is None:
        sigtime = sigtime.replace(tzinfo=timezone.utc)
    delta = current - sigtime
    minutes = int(delta.total_seconds() // 60)
    return max(0, minutes)


def freshness_verdict_for_age(
    age_minutes: int,
    thresholds: SignalAlphaDecayThresholds | None = None,
) -> FreshnessVerdict:
    """*시간 경과* 만으로 신선도 verdict 판정 (decay_score 와 무관).

    운영 환경에서 신호 도착 직후 *빠른 판단*용 — 분석 단계는 별도.
    """
    th = thresholds or SignalAlphaDecayThresholds()
    if age_minutes < 0:
        return FreshnessVerdict.UNKNOWN
    if age_minutes <= th.fresh_max_minutes:
        return FreshnessVerdict.FRESH
    if age_minutes <= th.decaying_max_minutes:
        return FreshnessVerdict.DECAYING
    if age_minutes <= th.stale_max_minutes:
        return FreshnessVerdict.STALE
    return FreshnessVerdict.EXPIRED


def is_signal_actionable(
    age_minutes: int,
    thresholds: SignalAlphaDecayThresholds | None = None,
    *,
    strict: bool = False,
) -> bool:
    """*시간 경과* 만으로 신호 사용 가능 여부 판정 (boolean).

    `strict=False` (default): EXPIRED 만 차단 — STALE / DECAYING 은 허용
    (단지 경고). `strict=True`: STALE 도 차단.

    본 helper 는 advisory — 호출자가 RiskManager / OrderGuard 우회 0건.
    """
    th = thresholds or SignalAlphaDecayThresholds()
    verdict = freshness_verdict_for_age(age_minutes, th)
    if verdict is FreshnessVerdict.EXPIRED:
        return False
    if verdict is FreshnessVerdict.UNKNOWN:
        return False
    if strict and verdict is FreshnessVerdict.STALE:
        return False
    return True


# ---------- markdown ----------


def render_markdown_report(result: SignalAlphaDecayResult) -> str:
    """markdown 리포트 — 운영자 / Strategy Researcher / Daily Report 에 carry 가능."""
    lines: list[str] = []
    lines.append(f"# Signal Alpha Decay — {result.strategy_name}")
    lines.append("")
    lines.append(f"_생성: {result.generated_at.isoformat()}_")
    lines.append("")
    lines.append(
        "> ⚠️ 본 보고서는 *신호 신선도 advisory* 입니다. 본 모듈은 어떤 주문도 "
        "발행하지 않으며, 신호 결과를 직접 적용하지 않습니다. AI Agent / "
        "Strategy 는 본 verdict 가 *EXPIRED* 인 신호를 *신규 진입 근거로 사용하지 "
        "않아야 합니다*."
    )
    lines.append("")
    lines.append(f"## 판정: **{result.verdict_overall.value}**")
    lines.append("")
    lines.append(
        f"- decay_score: **{result.decay_score:.1f}** / 100"
    )
    lines.append(
        f"- max_actionable_age_minutes: **{result.max_actionable_age_minutes}** 분"
    )
    if result.insufficient_data:
        lines.append("- ⚠️ insufficient_data=True (표본 부족 또는 입력 누락)")

    if result.warnings:
        lines.append("")
        lines.append("## 경고")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")

    if result.advice:
        lines.append("")
        lines.append("## 권고")
        for a in result.advice:
            lines.append(f"- 📝 {a}")

    if result.buckets:
        lines.append("")
        lines.append("## bucket 상세")
        lines.append("")
        lines.append(
            "| label | age (분) | mean_return (bps) | samples | "
            "relative_to_t0 (%) | severity |"
        )
        lines.append("|---|---|---|---|---|---|")
        for b in result.buckets:
            lines.append(
                f"| `{b.label}` | {b.age_minutes} | {b.mean_return_bps:.2f} | "
                f"{b.sample_count} | {b.relative_to_t0_pct:.1f} | "
                f"{b.severity.value} |"
            )

    lines.append("")
    lines.append(
        "---\n본 보고서는 advisory — broker / OrderExecutor / route_order / "
        "안전 flag 변경 0건. 실거래 실행 기능을 추가하지 않습니다."
    )
    return "\n".join(lines)
