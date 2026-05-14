"""Loss Root Cause Analyzer (#96) — *결정 시점 / 실행 단계* 손실 원인 태깅.

본 모듈은 손실 거래에 *근본 원인 태그*를 부여한다. 단순히 "얼마나 잃었는가"가
아니라 "왜 잃었는가"에 초점. AI Agent / Strategy 성능 개선의 1차 입력 자료.

본 모듈은 #79 `app/analytics/loss_tagging.py` 와는 *별개 분석 레이어*:

| 항목 | #79 loss_tagging | #96 본 모듈 (loss_root_cause) |
|---|---|---|
| 초점 | post-trade 결과 분류 (25 tag × 7 cat) | *결정 시점 / 실행 단계* 약점 태깅 (16 tag × 5 cat) |
| 입력 | 체결된 손실 거래의 전후 metric | *신호 → 진입 → 청산* 의 시점별 결정 데이터 |
| 태그 예 | STOP_LOSS_HIT, MARKET_SELLOFF, AI_LOW_CONFIDENCE | LATE_ENTRY, STALE_SIGNAL, AGENT_OVERRULED, HIGH_CORRELATION |
| 용도 | 손실 패턴 통계 / 추세 추적 | AI Agent prompt 개선 / 신호 품질 보강 |
| 사용 단계 | 일/주 단위 집계 | 거래별 즉시 + 누적 |

두 분석은 *상호 보완* — 운영자는 두 카드 모두 확인.

CLAUDE.md 절대 원칙 — 본 모듈은 외부 시스템과 완전 분리된 *순수 함수*:

- broker / executor / 외부 HTTP / AI SDK / settings reader 직접 사용 0건.
- 데이터베이스 쓰기 작업 0건.
- 안전 flag (실거래 / AI 자동실행 / 선물 LIVE) 변경 0건.
- 본 결과는 *추정 태그* 이며 *확정 원인이 아니다* — `is_estimated=True` 영구.
- 본 결과는 *주문 신호가 아니다* — `is_order_signal=False` 영구.
- 본 결과는 *자동 적용 안 함* — `auto_apply_allowed=False` 영구.

**16개 root cause tag** (5 카테고리):

| 카테고리 | 태그 |
|---|---|
| `decision`  | `LATE_ENTRY` / `LATE_EXIT` / `STALE_SIGNAL` / `AGENT_OVERRULED` |
| `risk`      | `HIGH_CORRELATION` / `RISK_GATE_REJECTED` |
| `market`    | `HIGH_VOLATILITY` / `BAD_REGIME` / `NEWS_RISK` |
| `execution` | `LOW_LIQUIDITY` / `SLIPPAGE` / `SPREAD_TOO_WIDE` |
| `strategy`  | `STOP_LOSS_HIT` / `TIME_STOP_HIT` / `KIMP_CONVERGENCE_FAIL` |
| `unknown`   | `UNKNOWN` |

`KIMP_CONVERGENCE_FAIL` 은 *crypto-specific* — 본 프로젝트는 국내주식 단타이므로
현 단계에서는 미적용. 향후 crypto 확장 시 (#95 모듈과 동일 패턴) 활성화 예정.

**본 모듈은 실거래 기능을 추가하지 않으며**, RiskManager / OrderGuard 를 우회하지
않는다. 본 태그는 advisory — 호출자가 학습 자료 / Daily Report carry 등에 활용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums ----------


class RootCauseCategory(StrEnum):
    """5 카테고리 + unknown."""
    DECISION  = "decision"
    RISK      = "risk"
    MARKET    = "market"
    EXECUTION = "execution"
    STRATEGY  = "strategy"
    UNKNOWN   = "unknown"


class RootCauseTag(StrEnum):
    """16개 신규 root cause tag. BUY/SELL/HOLD 값 0개."""
    # decision
    LATE_ENTRY            = "late_entry"
    LATE_EXIT             = "late_exit"
    STALE_SIGNAL          = "stale_signal"
    AGENT_OVERRULED       = "agent_overruled"
    # risk
    HIGH_CORRELATION      = "high_correlation"
    RISK_GATE_REJECTED    = "risk_gate_rejected"
    # market
    HIGH_VOLATILITY       = "high_volatility"
    BAD_REGIME            = "bad_regime"
    NEWS_RISK             = "news_risk"
    # execution
    LOW_LIQUIDITY         = "low_liquidity"
    SLIPPAGE              = "slippage"
    SPREAD_TOO_WIDE       = "spread_too_wide"
    # strategy
    STOP_LOSS_HIT         = "stop_loss_hit"
    TIME_STOP_HIT         = "time_stop_hit"
    KIMP_CONVERGENCE_FAIL = "kimp_convergence_fail"  # crypto, 미사용
    # unknown
    UNKNOWN               = "unknown"


class RootCauseSeverity(StrEnum):
    """단일 태그 severity. BUY/SELL/HOLD 값 0개."""
    LOW     = "LOW"
    MEDIUM  = "MEDIUM"
    HIGH    = "HIGH"
    UNKNOWN = "UNKNOWN"


# ---------- tag → category 매핑 ----------


_TAG_TO_CATEGORY: dict[RootCauseTag, RootCauseCategory] = {
    RootCauseTag.LATE_ENTRY:            RootCauseCategory.DECISION,
    RootCauseTag.LATE_EXIT:             RootCauseCategory.DECISION,
    RootCauseTag.STALE_SIGNAL:          RootCauseCategory.DECISION,
    RootCauseTag.AGENT_OVERRULED:       RootCauseCategory.DECISION,
    RootCauseTag.HIGH_CORRELATION:      RootCauseCategory.RISK,
    RootCauseTag.RISK_GATE_REJECTED:    RootCauseCategory.RISK,
    RootCauseTag.HIGH_VOLATILITY:       RootCauseCategory.MARKET,
    RootCauseTag.BAD_REGIME:            RootCauseCategory.MARKET,
    RootCauseTag.NEWS_RISK:             RootCauseCategory.MARKET,
    RootCauseTag.LOW_LIQUIDITY:         RootCauseCategory.EXECUTION,
    RootCauseTag.SLIPPAGE:              RootCauseCategory.EXECUTION,
    RootCauseTag.SPREAD_TOO_WIDE:       RootCauseCategory.EXECUTION,
    RootCauseTag.STOP_LOSS_HIT:         RootCauseCategory.STRATEGY,
    RootCauseTag.TIME_STOP_HIT:         RootCauseCategory.STRATEGY,
    RootCauseTag.KIMP_CONVERGENCE_FAIL: RootCauseCategory.STRATEGY,
    RootCauseTag.UNKNOWN:               RootCauseCategory.UNKNOWN,
}


# 카테고리 우선순위 — primary tag 선정 시.
_CATEGORY_PRIORITY = {
    RootCauseCategory.RISK:      6,
    RootCauseCategory.DECISION:  5,
    RootCauseCategory.MARKET:    4,
    RootCauseCategory.EXECUTION: 3,
    RootCauseCategory.STRATEGY:  2,
    RootCauseCategory.UNKNOWN:   1,
}


def category_for_tag(tag: RootCauseTag) -> RootCauseCategory:
    """단일 태그의 카테고리. lookup table."""
    return _TAG_TO_CATEGORY.get(tag, RootCauseCategory.UNKNOWN)


# ---------- thresholds ----------


@dataclass(frozen=True)
class RootCauseThresholds:
    """근본원인 분류 임계.

    각 임계는 보수적 default — 너무 민감하면 false positive, 너무 둔하면 학습
    자료 부족. 운영자가 평가 시 override 가능.
    """
    # decision
    late_entry_seconds:           int   = 30      # 신호 후 30초 초과 진입 → LATE_ENTRY
    late_exit_seconds:            int   = 60      # 청산 트리거 후 60초 초과 → LATE_EXIT
    stale_signal_age_minutes:     int   = 30      # 신호 age 30분 초과 → STALE_SIGNAL
    # risk
    high_correlation_threshold:   float = 0.85    # 포트폴리오 corr ≥ 0.85 → HIGH_CORRELATION
    # market
    high_volatility_threshold:    float = 0.04    # 일중 변동성 4% 초과 → HIGH_VOLATILITY
    # execution
    slippage_bps_threshold:       float = 50.0    # 50 bps 초과 → SLIPPAGE
    low_liquidity_volume_ratio:   float = 0.2     # 평균 대비 20% 미만 거래량 → LOW_LIQUIDITY
    spread_bps_threshold:         float = 100.0   # 100 bps 초과 → SPREAD_TOO_WIDE


# ---------- input DTO ----------


@dataclass(frozen=True)
class LossRootCauseInput:
    """근본원인 평가 입력. 호출자가 거래 전후 metric 채워서 전달.

    *현재값 carry* — 본 모듈은 어떤 값도 변경하지 않으며 broker / DB / 외부
    시스템과 무관.
    """
    symbol:                  str
    is_loss:                 bool = True
    trade_pnl:               int  = 0
    strategy:                str | None = None
    mode:                    str | None = None     # SIMULATION/PAPER/SHADOW etc.

    # ---- decision metrics ----
    # 신호 생성 → 진입까지 경과 초
    entry_lag_seconds:       int | None = None
    # 청산 트리거 → 실제 청산까지 경과 초
    exit_lag_seconds:        int | None = None
    # 진입 시점의 신호 age (분) — #94 SignalAlphaDecay 와 연동
    signal_age_minutes_at_entry: int | None = None
    # 운영자가 AI 추천 결과를 reject / override 후 진입 (혹은 회피)
    operator_overruled_ai:   bool = False

    # ---- risk metrics ----
    # 진입 시점 portfolio max |corr| (#95 PortfolioCorrelationGuard 와 연동)
    portfolio_max_correlation: float | None = None
    # RiskManager pre-trade 결과 — REJECTED 였는데 운영자가 우회한 경우
    risk_gate_was_rejected:    bool = False

    # ---- market metrics ----
    # 일중 변동성 (절대값, 예: 0.05 = 5%)
    intraday_volatility:     float | None = None
    # 시장 regime — "TREND_UP" / "TREND_DOWN" / "CHOPPY" / "HIGH_VOL" 등
    market_regime_at_entry:  str | None = None
    market_regime_unfavorable: bool = False
    # 진입 시점 또는 직후 부정적 뉴스 발생
    adverse_news_event:      bool = False

    # ---- execution metrics ----
    realized_slippage_bps:   float | None = None
    # 진입 시점 거래량을 평균 거래량으로 나눈 비율 (0~1 ~ 무한)
    volume_to_avg_ratio:     float | None = None
    # 진입 시점 bid-ask spread (bps)
    spread_bps_at_entry:     float | None = None

    # ---- strategy metrics ----
    hit_stop_loss:           bool = False
    hit_time_stop:           bool = False
    # crypto 김프 페어트레이딩 (본 프로젝트 미적용)
    kimp_convergence_failed: bool = False

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must be non-empty")


# ---------- result DTO ----------


@dataclass(frozen=True)
class RootCauseTagAssignment:
    """단일 태그 부여 결과 — 어떤 metric 이 trigger 였는지 함께 carry."""
    tag:         RootCauseTag
    category:    RootCauseCategory
    severity:    RootCauseSeverity
    rationale:   str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag":       self.tag.value,
            "category":  self.category.value,
            "severity":  self.severity.value,
            "rationale": self.rationale,
        }


@dataclass
class LossRootCauseResult:
    """근본원인 평가 결과.

    invariants (코드 단 강제):
    - `is_estimated=True` 항상 (False 시 ValueError).
    - `is_order_signal=False` 항상.
    - `auto_apply_allowed=False` 항상.
    - `is_investment_advice=False` 항상.
    """
    symbol:               str
    is_loss:              bool = True
    trade_pnl:            int  = 0
    tags:                 list[RootCauseTagAssignment] = field(default_factory=list)
    primary_tag:          RootCauseTag | None = None
    primary_category:     RootCauseCategory | None = None
    rationale:            list[str] = field(default_factory=list)
    improvement_advice:   list[str] = field(default_factory=list)
    is_estimated:         bool = True
    is_order_signal:      bool = False
    auto_apply_allowed:   bool = False
    is_investment_advice: bool = False
    generated_at:         datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_estimated is not True:
            raise ValueError(
                "LossRootCauseResult.is_estimated must be True — "
                "본 태그는 *추정값* 이며 확정 원인이 아니다."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "LossRootCauseResult.is_order_signal must be False — "
                "본 모듈은 BUY/SELL/HOLD 신호를 생성하지 않는다."
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "LossRootCauseResult.auto_apply_allowed must be False — "
                "본 결과는 자동 적용되지 않는다."
            )
        if self.is_investment_advice is not False:
            raise ValueError(
                "LossRootCauseResult.is_investment_advice must be False — "
                "본 결과는 투자 조언이 아니다."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":               self.symbol,
            "is_loss":              self.is_loss,
            "trade_pnl":            self.trade_pnl,
            "tags":                 [t.to_dict() for t in self.tags],
            "primary_tag":          self.primary_tag.value if self.primary_tag else None,
            "primary_category":     (
                self.primary_category.value if self.primary_category else None
            ),
            "rationale":            list(self.rationale),
            "improvement_advice":   list(self.improvement_advice),
            "is_estimated":         self.is_estimated,
            "is_order_signal":      self.is_order_signal,
            "auto_apply_allowed":   self.auto_apply_allowed,
            "is_investment_advice": self.is_investment_advice,
            "generated_at":         self.generated_at.isoformat(),
        }


# ---------- classifier ----------


def _maybe(condition: bool, tag: RootCauseTag,
           severity: RootCauseSeverity, rationale: str,
           out: list[RootCauseTagAssignment]) -> None:
    """condition 이 True 면 태그 추가."""
    if condition:
        out.append(RootCauseTagAssignment(
            tag=tag,
            category=category_for_tag(tag),
            severity=severity,
            rationale=rationale,
        ))


def evaluate_loss_root_cause(
    inp: LossRootCauseInput,
    thresholds: RootCauseThresholds | None = None,
) -> LossRootCauseResult:
    """단일 손실 거래의 근본원인 평가. read-only — 외부 시스템 영향 0건.

    각 metric 이 임계 초과 시 해당 태그 부여. 여러 태그가 동시에 부여 가능
    (다중 원인이 정상 — 단일 원인으로 단순화하지 않음). primary_tag 는
    카테고리 우선순위 + severity 로 선정.
    """
    th = thresholds or RootCauseThresholds()
    tags: list[RootCauseTagAssignment] = []

    # ---- DECISION ----
    if (inp.entry_lag_seconds is not None
            and inp.entry_lag_seconds > th.late_entry_seconds):
        _maybe(True, RootCauseTag.LATE_ENTRY,
               RootCauseSeverity.MEDIUM,
               f"entry_lag={inp.entry_lag_seconds}s > {th.late_entry_seconds}s",
               tags)
    if (inp.exit_lag_seconds is not None
            and inp.exit_lag_seconds > th.late_exit_seconds):
        _maybe(True, RootCauseTag.LATE_EXIT,
               RootCauseSeverity.MEDIUM,
               f"exit_lag={inp.exit_lag_seconds}s > {th.late_exit_seconds}s",
               tags)
    if (inp.signal_age_minutes_at_entry is not None
            and inp.signal_age_minutes_at_entry > th.stale_signal_age_minutes):
        _maybe(True, RootCauseTag.STALE_SIGNAL,
               RootCauseSeverity.HIGH,
               f"signal_age={inp.signal_age_minutes_at_entry}m > "
               f"{th.stale_signal_age_minutes}m (#94 연동)",
               tags)
    _maybe(inp.operator_overruled_ai, RootCauseTag.AGENT_OVERRULED,
           RootCauseSeverity.MEDIUM,
           "운영자가 AI 추천 결과를 reject / override", tags)

    # ---- RISK ----
    if (inp.portfolio_max_correlation is not None
            and abs(inp.portfolio_max_correlation) >= th.high_correlation_threshold):
        _maybe(True, RootCauseTag.HIGH_CORRELATION,
               RootCauseSeverity.HIGH,
               f"portfolio_max_|corr|={abs(inp.portfolio_max_correlation):.3f} "
               f">= {th.high_correlation_threshold:.2f} (#95 연동)",
               tags)
    _maybe(inp.risk_gate_was_rejected, RootCauseTag.RISK_GATE_REJECTED,
           RootCauseSeverity.HIGH,
           "RiskManager pre-trade REJECTED 였는데 우회 진입 의심", tags)

    # ---- MARKET ----
    if (inp.intraday_volatility is not None
            and inp.intraday_volatility > th.high_volatility_threshold):
        _maybe(True, RootCauseTag.HIGH_VOLATILITY,
               RootCauseSeverity.MEDIUM,
               f"intraday_volatility={inp.intraday_volatility:.3f} > "
               f"{th.high_volatility_threshold:.3f}",
               tags)
    if inp.market_regime_unfavorable:
        _maybe(True, RootCauseTag.BAD_REGIME,
               RootCauseSeverity.MEDIUM,
               f"market_regime={inp.market_regime_at_entry or 'unspecified'} 이 "
               "전략에 부적합", tags)
    _maybe(inp.adverse_news_event, RootCauseTag.NEWS_RISK,
           RootCauseSeverity.MEDIUM,
           "진입 시점 또는 직후 부정적 뉴스 발생", tags)

    # ---- EXECUTION ----
    if (inp.volume_to_avg_ratio is not None
            and inp.volume_to_avg_ratio < th.low_liquidity_volume_ratio):
        _maybe(True, RootCauseTag.LOW_LIQUIDITY,
               RootCauseSeverity.MEDIUM,
               f"volume_to_avg_ratio={inp.volume_to_avg_ratio:.2f} < "
               f"{th.low_liquidity_volume_ratio:.2f}",
               tags)
    if (inp.realized_slippage_bps is not None
            and abs(inp.realized_slippage_bps) > th.slippage_bps_threshold):
        _maybe(True, RootCauseTag.SLIPPAGE,
               RootCauseSeverity.MEDIUM,
               f"slippage={inp.realized_slippage_bps:.1f}bps > "
               f"{th.slippage_bps_threshold:.0f}bps",
               tags)
    if (inp.spread_bps_at_entry is not None
            and inp.spread_bps_at_entry > th.spread_bps_threshold):
        _maybe(True, RootCauseTag.SPREAD_TOO_WIDE,
               RootCauseSeverity.MEDIUM,
               f"spread={inp.spread_bps_at_entry:.1f}bps > "
               f"{th.spread_bps_threshold:.0f}bps",
               tags)

    # ---- STRATEGY ----
    _maybe(inp.hit_stop_loss, RootCauseTag.STOP_LOSS_HIT,
           RootCauseSeverity.LOW,   # 정상 stop hit 도 손실의 흔한 원인.
           "stop loss 도달", tags)
    _maybe(inp.hit_time_stop, RootCauseTag.TIME_STOP_HIT,
           RootCauseSeverity.LOW,
           "time stop 도달", tags)
    _maybe(inp.kimp_convergence_failed, RootCauseTag.KIMP_CONVERGENCE_FAIL,
           RootCauseSeverity.MEDIUM,
           "김프 페어트레이딩 수렴 실패 (crypto, 본 프로젝트 미적용)",
           tags)

    # 아무 태그도 부여되지 않았으면 UNKNOWN.
    if not tags:
        tags.append(RootCauseTagAssignment(
            tag=RootCauseTag.UNKNOWN,
            category=RootCauseCategory.UNKNOWN,
            severity=RootCauseSeverity.UNKNOWN,
            rationale="입력 metric 으로는 원인 추정 불가",
        ))

    # primary tag — 카테고리 우선순위 → severity 순.
    severity_rank = {
        RootCauseSeverity.HIGH:    3,
        RootCauseSeverity.MEDIUM:  2,
        RootCauseSeverity.LOW:     1,
        RootCauseSeverity.UNKNOWN: 0,
    }

    def _sort_key(t: RootCauseTagAssignment) -> tuple[int, int]:
        return (_CATEGORY_PRIORITY.get(t.category, 0),
                severity_rank.get(t.severity, 0))

    primary = max(tags, key=_sort_key)

    rationale = [t.rationale for t in tags if t.rationale]
    advice = _build_improvement_advice(tags, inp)

    return LossRootCauseResult(
        symbol=inp.symbol,
        is_loss=inp.is_loss,
        trade_pnl=inp.trade_pnl,
        tags=tags,
        primary_tag=primary.tag,
        primary_category=primary.category,
        rationale=rationale,
        improvement_advice=advice,
    )


def _build_improvement_advice(
    tags: list[RootCauseTagAssignment],
    inp: LossRootCauseInput,
) -> list[str]:
    """태그 별 개선 제안 — AI Agent prompt / 운영자 학습 자료에 carry."""
    tag_set = {t.tag for t in tags}
    advice: list[str] = []

    if RootCauseTag.STALE_SIGNAL in tag_set:
        advice.append(
            "STALE_SIGNAL — 신호 생성 후 진입까지 시간이 길었음. "
            "#94 SignalAlphaDecay 의 freshness 임계 재검토 권장."
        )
    if RootCauseTag.LATE_ENTRY in tag_set:
        advice.append(
            "LATE_ENTRY — 신호와 실제 진입 사이 지연. 자동화 흐름 검토 또는 "
            "운영자 승인 단계 간소화 검토."
        )
    if RootCauseTag.LATE_EXIT in tag_set:
        advice.append(
            "LATE_EXIT — 청산 트리거 후 실 청산 지연. 자동 청산 흐름 또는 "
            "fill_polling 주기 점검."
        )
    if RootCauseTag.HIGH_CORRELATION in tag_set:
        advice.append(
            "HIGH_CORRELATION — 동시 노출 종목이 매우 강한 상관관계. "
            "#95 PortfolioCorrelationGuard 의 block_threshold 또는 신규 진입 "
            "diversification 정책 재검토."
        )
    if RootCauseTag.RISK_GATE_REJECTED in tag_set:
        advice.append(
            "RISK_GATE_REJECTED — RiskManager 가 사전 차단했어야 함. "
            "차단 우회가 의심되면 사고 리포트 + audit 검토."
        )
    if RootCauseTag.LOW_LIQUIDITY in tag_set:
        advice.append(
            "LOW_LIQUIDITY — 거래량 부족 시점 진입. 종목 필터 / 진입 시간대 "
            "조건 재검토."
        )
    if RootCauseTag.SLIPPAGE in tag_set:
        advice.append(
            "SLIPPAGE — 큰 slippage 발생. 시장가 대신 limit 주문 또는 호가 "
            "공급력 점검."
        )
    if RootCauseTag.SPREAD_TOO_WIDE in tag_set:
        advice.append(
            "SPREAD_TOO_WIDE — bid-ask 스프레드가 비정상. 종목 / 시장 시점 "
            "선택 재검토."
        )
    if RootCauseTag.HIGH_VOLATILITY in tag_set:
        advice.append(
            "HIGH_VOLATILITY — 변동성 높은 구간 진입. 사이즈 감소 또는 변동성 "
            "임계 도입 검토."
        )
    if RootCauseTag.BAD_REGIME in tag_set:
        advice.append(
            "BAD_REGIME — 시장 regime 이 전략에 부적합. Strategy Selection "
            "(#85) verdict 재검토."
        )
    if RootCauseTag.NEWS_RISK in tag_set:
        advice.append(
            "NEWS_RISK — 부정적 뉴스 영향. 진입 직전 뉴스 필터 추가 검토."
        )
    if RootCauseTag.AGENT_OVERRULED in tag_set:
        advice.append(
            "AGENT_OVERRULED — 운영자가 AI 추천을 reject / override. AI prompt "
            "context 보강 또는 운영자 의도 학습 자료 추가."
        )
    if RootCauseTag.UNKNOWN in tag_set and len(tags) == 1:
        advice.append(
            "UNKNOWN — 입력 metric 부족으로 원인 추정 불가. collector 보강 "
            "(entry_lag / volatility / regime 등) 권장."
        )

    return advice


# ---------- aggregation ----------


@dataclass(frozen=True)
class TagFrequency:
    """집계 결과의 단일 행."""
    tag:           RootCauseTag
    category:      RootCauseCategory
    count:         int
    share_pct:     float
    severity_dist: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag":           self.tag.value,
            "category":      self.category.value,
            "count":         self.count,
            "share_pct":     self.share_pct,
            "severity_dist": dict(self.severity_dist),
        }


@dataclass
class LossRootCauseSummary:
    """N 개 결과의 집계 요약."""
    total_loss_count:    int = 0
    by_tag:              list[TagFrequency] = field(default_factory=list)
    by_category:         dict[str, int] = field(default_factory=dict)
    top_tags:            list[str] = field(default_factory=list)
    high_severity_tags:  list[str] = field(default_factory=list)
    by_strategy:         dict[str, dict[str, int]] = field(default_factory=dict)
    is_estimated:        bool = True
    is_order_signal:     bool = False
    auto_apply_allowed:  bool = False

    def __post_init__(self) -> None:
        if self.is_estimated is not True:
            raise ValueError(
                "LossRootCauseSummary.is_estimated must be True"
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "LossRootCauseSummary.is_order_signal must be False"
            )
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "LossRootCauseSummary.auto_apply_allowed must be False"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_loss_count":     self.total_loss_count,
            "by_tag":               [r.to_dict() for r in self.by_tag],
            "by_category":          dict(self.by_category),
            "top_tags":              list(self.top_tags),
            "high_severity_tags":   list(self.high_severity_tags),
            "by_strategy":          {
                k: dict(v) for k, v in self.by_strategy.items()
            },
            "is_estimated":         self.is_estimated,
            "is_order_signal":      self.is_order_signal,
            "auto_apply_allowed":   self.auto_apply_allowed,
        }


def summarize_root_causes(
    results: list[LossRootCauseResult],
    strategy_by_result: list[str | None] | None = None,
    *,
    top_n: int = 5,
) -> LossRootCauseSummary:
    """N 개 결과를 집계해 빈도 / 카테고리 / 전략별 분포 통계 생성.

    `strategy_by_result` 는 results 와 동일 길이의 strategy 이름 list (None 가능).
    """
    if not results:
        return LossRootCauseSummary()

    strategies = (
        strategy_by_result if strategy_by_result is not None
        else [None] * len(results)
    )
    if len(strategies) != len(results):
        raise ValueError(
            f"strategy_by_result length {len(strategies)} != "
            f"results length {len(results)}"
        )

    total = len(results)
    tag_count: dict[RootCauseTag, int] = {}
    tag_severity: dict[RootCauseTag, dict[str, int]] = {}
    cat_count: dict[str, int] = {}
    high_severity_tags: set[RootCauseTag] = set()
    by_strategy: dict[str, dict[str, int]] = {}

    for r, strat in zip(results, strategies):
        for t in r.tags:
            tag_count[t.tag] = tag_count.get(t.tag, 0) + 1
            sev_dist = tag_severity.setdefault(t.tag, {})
            sev_dist[t.severity.value] = sev_dist.get(t.severity.value, 0) + 1
            if t.severity is RootCauseSeverity.HIGH:
                high_severity_tags.add(t.tag)
            cat_key = t.category.value
            cat_count[cat_key] = cat_count.get(cat_key, 0) + 1
            if strat:
                strat_cat = by_strategy.setdefault(strat, {})
                strat_cat[cat_key] = strat_cat.get(cat_key, 0) + 1

    by_tag = [
        TagFrequency(
            tag=t,
            category=category_for_tag(t),
            count=c,
            share_pct=round(100.0 * c / total, 1),
            severity_dist=dict(tag_severity.get(t, {})),
        )
        for t, c in sorted(tag_count.items(),
                            key=lambda x: x[1], reverse=True)
    ]
    top_tags = [r.tag.value for r in by_tag[:top_n]]

    return LossRootCauseSummary(
        total_loss_count=total,
        by_tag=by_tag,
        by_category=cat_count,
        top_tags=top_tags,
        high_severity_tags=sorted(t.value for t in high_severity_tags),
        by_strategy=by_strategy,
    )
