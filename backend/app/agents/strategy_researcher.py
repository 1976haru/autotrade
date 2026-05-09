"""#55: Strategy Researcher Agent.

백테스트 결과(`BacktestRun`) + 메트릭(#24) + walk-forward(#25) + Monte Carlo
(#26) + data quality(#21) + strategy promotion gate(#27) 결과를 *읽고* 전략
개선 후보를 markdown 리포트로 *제안*하는 advisory Agent.

## 핵심 invariant (절대 원칙)

1. **자동 반영 0건** — `auto_apply_allowed=False` 불변. 본 Agent의 어떤 출력도
   strategy 코드 / 파라미터에 자동으로 적용되지 *않는다*. 모든 제안은 운영자
   검토 → 별도 PR → 별도 백테스트 → walk-forward → paper/shadow → live 절차
   필요.
2. **주문 신호 0건** — `is_order_signal=False` 불변, BUY/SELL/HOLD 반환 X.
3. **broker / OrderExecutor / route_order 호출 0건** — 정적 grep 가드.
4. **approval queue 직접 등록 0건** — `submit_candidate` / `route_order` import X.
5. **strategy 코드 / 파라미터 mutation 0건** — `app.strategies.*` import X
   (read-only 분석은 BacktestRun row + 외부에서 주입된 결과만 사용).
6. **DB read-only** — INSERT / UPDATE / DELETE 0건 (정적 grep 가드).
7. **외부 AI / HTTP 호출 0건** — anthropic / openai / httpx / requests import 0건.
8. **emergency_stop 토글 0건** — 본 Agent는 위험 감독 (#54)이 아니라 전략 *개선
   제안* 전용. risk.set_emergency_stop / risk.emergency_stop = True 호출 X.

## 출력 구조

`StrategyResearchReport`:
- `audit_level`: HEALTHY / CAUTION / WARNING / CRITICAL
- `findings`: 관찰된 문제점 (low PF, high MDD, overfit 의심 등)
- `suggestions`: 개선 후보 (PARAMETER_TUNE / RISK_TIGHTEN / TIMEFRAME_FILTER /
  DATA_QUALITY / OVERFIT_GUARD / SHRINK_SIZE / ADD_FILTER / RE_RUN_TEST /
  PROMOTION_BLOCK / SHADOW_VALIDATE)
- `required_next_tests`: 제안 적용 *전* 반드시 통과해야 할 검증 (운영자가 수동
  실행)
- `markdown_report`: 운영자용 markdown 리포트 (자동 반영 안 됨 disclaimer 포함)
- `auto_apply_allowed=False` 항상 (가드)
- `is_order_signal=False` 항상 (가드)

자세한 정책: [`docs/strategy_researcher_agent.md`](../../../docs/strategy_researcher_agent.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import (
    AgentBase,
    AgentContext,
    AgentDecision,
    AgentMetadata,
    AgentOutput,
    AgentRole,
)
from app.db.models import BacktestRun


# ====================================================================
# Enums (NEVER BUY/SELL/HOLD — strategy improvement categories only)
# ====================================================================


class ResearchSeverity(StrEnum):
    """전략 분석 결과 단계.

    BUY/SELL/HOLD 같은 *주문 결정* 값을 *절대* 포함하지 않는다 — 본 Agent는
    advisory 분석 전용이며, 운영자가 별도 PR / 별도 백테스트 / paper/shadow를
    거쳐야만 변경이 반영된다.
    """
    HEALTHY  = "HEALTHY"   # 모든 기준 통과 — 후속 검증 권고만
    CAUTION  = "CAUTION"   # 일부 임계 근접 — 모니터링 강화
    WARNING  = "WARNING"   # 임계 위반 — 추가 검증 + 파라미터 재검토 권고
    CRITICAL = "CRITICAL"  # 다수 위반 / 자동승격 차단 — 운영자 수동 결정 필요


class FindingCode(StrEnum):
    """관찰된 문제 코드. 운영자 리뷰 시 grep 가능한 stable identifier."""
    LOW_TRADE_COUNT          = "low_trade_count"
    LOW_PROFIT_FACTOR        = "low_profit_factor"
    NEGATIVE_EXPECTANCY      = "negative_expectancy"
    LOW_WIN_RATE             = "low_win_rate"
    HIGH_MAX_DRAWDOWN        = "high_max_drawdown"
    HIGH_CONSECUTIVE_LOSSES  = "high_consecutive_losses"
    HOURLY_PNL_IMBALANCE     = "hourly_pnl_imbalance"
    WALK_FORWARD_FAIL        = "walk_forward_fail"
    WALK_FORWARD_CAUTION     = "walk_forward_caution"
    LOW_POSITIVE_FOLD_RATIO  = "low_positive_fold_ratio"
    SINGLE_FOLD_DOMINANCE    = "single_fold_dominance"
    OVERFIT_RISK_HIGH        = "overfit_risk_high"
    MONTE_CARLO_RUIN_HIGH    = "monte_carlo_ruin_high"
    MONTE_CARLO_FAT_TAIL     = "monte_carlo_fat_tail"
    DATA_QUALITY_POOR        = "data_quality_poor"
    DATA_QUALITY_WARNING     = "data_quality_warning"
    PROMOTION_BLOCKED        = "promotion_blocked"
    PROMOTION_FAILED         = "promotion_failed"
    INSUFFICIENT_HOLDOUT     = "insufficient_holdout"


class SuggestionCategory(StrEnum):
    """개선 제안 카테고리. *advisory*만 — 자동 적용 0건."""
    PARAMETER_TUNE      = "parameter_tune"      # 파라미터 후보 (예: SMA window 늘려보기)
    RISK_TIGHTEN        = "risk_tighten"        # 손절 / 포지션 한도 강화
    TIMEFRAME_FILTER    = "timeframe_filter"    # 손익 편향 시간대 회피
    DATA_QUALITY        = "data_quality"        # 데이터 quality 개선 후 재실행
    OVERFIT_GUARD       = "overfit_guard"       # walk-forward / holdout 강화
    SHRINK_SIZE         = "shrink_size"         # quantity / notional 축소
    ADD_FILTER          = "add_filter"          # 신규 진입 조건 추가 (theme / regime)
    RE_RUN_TEST         = "re_run_test"         # 단순 재실행 (데이터 갱신 후 등)
    PROMOTION_BLOCK     = "promotion_block"     # 현재 stage 승격 보류
    SHADOW_VALIDATE     = "shadow_validate"     # 변경 전 shadow 운용 권고


# ====================================================================
# Dataclasses
# ====================================================================


@dataclass(frozen=True)
class StrategyFinding:
    code:        FindingCode
    severity:    ResearchSeverity
    summary:     str
    metric_name: str | None = None
    metric_value: float | None = None
    threshold:   float | None = None
    detail:      dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StrategySuggestion:
    category:            SuggestionCategory
    severity:            ResearchSeverity
    title:               str
    rationale:           str
    proposed_change:     str
    required_validation: tuple[str, ...] = ()
    references:          tuple[str, ...] = ()


@dataclass(frozen=True)
class BacktestSummary:
    """`BacktestRun` row + 외부 metric 모듈에서 미리 계산한 값을 carry.

    본 Agent는 `summarize_metrics`를 *직접 호출하지 않으며* (caller 책임) —
    이는 strategy_researcher.py가 strategies / metrics 모듈에 의존하지 않게
    하기 위함. caller(예: API endpoint)가 트레이드 리스트를 요약해 본 dataclass
    를 채워 넣는다.
    """
    run_id:                  int
    strategy:                str
    created_at:              datetime
    params:                  dict[str, Any] = field(default_factory=dict)
    initial_cash:            int = 0
    bars_processed:          int = 0
    trade_count:             int = 0
    win_count:               int = 0
    loss_count:              int = 0
    total_pnl:               int = 0
    final_cash:              int = 0
    win_rate:                float | None = None
    profit_factor:           float | None = None
    expectancy:              float | None = None
    max_drawdown:            int = 0
    max_consecutive_losses:  int = 0
    max_consecutive_wins:    int = 0
    sharpe_ratio:            float | None = None
    avg_win:                 float | None = None
    avg_loss:                float | None = None
    hourly_pnl:              dict[int, int] = field(default_factory=dict)
    data_symbol:             str | None = None
    data_interval:           str | None = None
    data_start:              datetime | None = None
    data_end:                datetime | None = None


@dataclass(frozen=True)
class WalkForwardSummary:
    recommendation:           str | None = None       # FAIL / CAUTION / PASS
    fold_count:               int = 0
    positive_fold_ratio:      float | None = None
    single_best_fold_share:   float | None = None
    overfit_risk_score:       float | None = None
    stability_score:          float | None = None
    holdout_pnl:              int | None = None
    holdout_window:           dict[str, Any] | None = None
    warnings:                 tuple[str, ...] = ()
    overfit_flags:            tuple[str, ...] = ()


@dataclass(frozen=True)
class MonteCarloSummary:
    method:                   str | None = None
    iterations:               int = 0
    risk_of_ruin:             float | None = None     # 0-1
    p05_total_pnl:            int | None = None
    p50_total_pnl:            int | None = None
    p95_total_pnl:            int | None = None
    p05_max_drawdown:         int | None = None
    p95_max_drawdown:         int | None = None
    worst_5pct_avg_mdd:       int | None = None
    longest_losing_streak:    int | None = None
    promotion_risk_flag:      str | None = None       # PASS / CAUTION / FAIL
    stability_grade:          str | None = None       # GOOD / WARNING / POOR
    warnings:                 tuple[str, ...] = ()


@dataclass(frozen=True)
class DataQualitySummary:
    symbol:           str
    interval:         str
    score:            float | None = None              # 0-100
    grade:            str | None = None                # GOOD/WARNING/POOR/EXCLUDE/EMPTY
    missing_rate:     float | None = None
    coverage_score:   float | None = None
    notes:            tuple[str, ...] = ()


@dataclass(frozen=True)
class PromotionGateSummary:
    current_stage:    str | None = None
    target_stage:     str | None = None
    decision:         str | None = None                # PASS/CAUTION/FAIL/BLOCKED
    failed_criteria:  tuple[str, ...] = ()
    cautions:         tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class StrategyResearcherInput:
    """본 Agent의 표준 입력 — caller가 모든 외부 metric을 미리 계산해서 채움."""
    backtest:         BacktestSummary
    walk_forward:     WalkForwardSummary | None = None
    monte_carlo:      MonteCarloSummary | None = None
    data_quality:     tuple[DataQualitySummary, ...] = ()
    promotion_gate:   PromotionGateSummary | None = None
    operator_note:    str | None = None


@dataclass(frozen=True)
class StrategyResearchReport:
    audit_level:          ResearchSeverity
    findings:             tuple[StrategyFinding, ...]
    suggestions:          tuple[StrategySuggestion, ...]
    required_next_tests:  tuple[str, ...]
    markdown_report:      str
    summary_lines:        tuple[str, ...]
    strategy:             str
    run_id:               int
    auto_apply_allowed:   bool
    is_order_signal:      bool
    created_at:           datetime

    def __post_init__(self) -> None:
        if self.auto_apply_allowed is not False:
            raise ValueError(
                "StrategyResearchReport.auto_apply_allowed must be False — "
                "본 Agent의 출력은 *advisory*입니다. 자동 반영 금지."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "StrategyResearchReport.is_order_signal must be False — "
                "Strategy Researcher는 주문 신호를 만들지 않습니다."
            )


# ====================================================================
# Thresholds (운영자가 docs/strategy_researcher_agent.md에서 조정)
# ====================================================================


# Backtest core (#24)
_MIN_TRADE_COUNT_HEALTHY      = 100      # promotion_gate와 일치
_MIN_TRADE_COUNT_CAUTION      = 30
_MIN_PROFIT_FACTOR_HEALTHY    = 1.20
_MIN_PROFIT_FACTOR_CAUTION    = 1.00
_MAX_DRAWDOWN_PCT_HEALTHY     = 0.15     # initial_cash 대비
_MAX_DRAWDOWN_PCT_CAUTION     = 0.25
_MAX_CONSECUTIVE_LOSSES_HEALTHY = 5
_MAX_CONSECUTIVE_LOSSES_CAUTION = 8
_MIN_WIN_RATE_CAUTION         = 0.35

# Walk-forward (#25)
_MIN_POSITIVE_FOLD_RATIO_HEALTHY = 0.60
_MAX_SINGLE_FOLD_SHARE_HEALTHY   = 0.70
_MAX_OVERFIT_RISK_HEALTHY        = 0.50

# Monte Carlo (#26)
_MAX_RISK_OF_RUIN_HEALTHY  = 0.05
_MAX_RISK_OF_RUIN_CRITICAL = 0.10

# Data quality (#21)
_MIN_DATA_QUALITY_SCORE_HEALTHY = 75.0
_MIN_DATA_QUALITY_SCORE_HARD    = 60.0

# Hourly imbalance — 단일 hour가 전체 PnL의 N% 이상 점유하면 의심
_HOURLY_DOMINANCE_PCT = 0.50


# ====================================================================
# DB read-only helper (INSERT/UPDATE/DELETE 0건 — 정적 grep 가드)
# ====================================================================


def load_backtest_run(db: Session, run_id: int) -> BacktestRun | None:
    """`BacktestRun` row를 read-only SELECT."""
    stmt = select(BacktestRun).where(BacktestRun.id == run_id).limit(1)
    return db.execute(stmt).scalar_one_or_none()


def load_recent_backtest_runs(
    db: Session,
    *,
    strategy: str | None = None,
    limit: int = 20,
) -> list[BacktestRun]:
    """최근 BacktestRun 목록 — read-only SELECT."""
    stmt = select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)
    if strategy:
        stmt = stmt.where(BacktestRun.strategy == strategy)
    return list(db.execute(stmt).scalars())


# ====================================================================
# Pure analysis function
# ====================================================================


def analyze_strategy(inp: StrategyResearcherInput) -> StrategyResearchReport:
    """입력 백테스트 + 검증 결과를 분석해 advisory 리포트 생성.

    본 함수는 *순수* — DB / broker / 외부 호출 없음. 같은 입력이면 같은 출력.
    """
    findings: list[StrategyFinding] = []
    suggestions: list[StrategySuggestion] = []

    _check_backtest_metrics(inp.backtest, findings, suggestions)
    if inp.walk_forward is not None:
        _check_walk_forward(inp.walk_forward, findings, suggestions)
    if inp.monte_carlo is not None:
        _check_monte_carlo(inp.monte_carlo, findings, suggestions)
    if inp.data_quality:
        _check_data_quality(inp.data_quality, findings, suggestions)
    if inp.promotion_gate is not None:
        _check_promotion_gate(inp.promotion_gate, findings, suggestions)

    audit_level = _derive_audit_level(findings)
    required_next_tests = _derive_required_next_tests(findings, suggestions, inp)
    summary_lines = _build_summary_lines(audit_level, findings, suggestions, inp)
    markdown = _build_markdown(
        audit_level=audit_level,
        findings=findings,
        suggestions=suggestions,
        required_next_tests=required_next_tests,
        inp=inp,
    )

    return StrategyResearchReport(
        audit_level=audit_level,
        findings=tuple(findings),
        suggestions=tuple(suggestions),
        required_next_tests=tuple(required_next_tests),
        markdown_report=markdown,
        summary_lines=tuple(summary_lines),
        strategy=inp.backtest.strategy,
        run_id=inp.backtest.run_id,
        auto_apply_allowed=False,
        is_order_signal=False,
        created_at=datetime.now(timezone.utc),
    )


# ====================================================================
# Detector helpers — 각각 findings + suggestions append
# ====================================================================


def _check_backtest_metrics(
    bt: BacktestSummary,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
) -> None:
    if bt.trade_count < _MIN_TRADE_COUNT_CAUTION:
        findings.append(StrategyFinding(
            code=FindingCode.LOW_TRADE_COUNT,
            severity=ResearchSeverity.WARNING,
            summary=f"백테스트 트레이드 수가 {bt.trade_count}건으로 통계적 유의미 임계({_MIN_TRADE_COUNT_HEALTHY})보다 매우 낮습니다.",
            metric_name="trade_count",
            metric_value=float(bt.trade_count),
            threshold=float(_MIN_TRADE_COUNT_HEALTHY),
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.RE_RUN_TEST,
            severity=ResearchSeverity.WARNING,
            title="더 긴 데이터 구간 또는 더 다양한 시장 조건에서 재백테스트",
            rationale=f"트레이드 수 {bt.trade_count}건은 표본이 작아 PF / 승률 / MDD 모두 *추정 신뢰도가 낮음*. 통계적 의미를 갖기 위해 최소 {_MIN_TRADE_COUNT_HEALTHY}건 이상 권고.",
            proposed_change="`data_start`/`data_end`를 확장하거나 universe 종목 수를 늘려 재실행.",
            required_validation=("새 데이터 구간 백테스트 재실행", "기간 확장 후 walk-forward 재검증"),
            references=("docs/strategy_researcher_agent.md §6",),
        ))
    elif bt.trade_count < _MIN_TRADE_COUNT_HEALTHY:
        findings.append(StrategyFinding(
            code=FindingCode.LOW_TRADE_COUNT,
            severity=ResearchSeverity.CAUTION,
            summary=f"트레이드 수 {bt.trade_count}건 — 통계적 유의미 임계({_MIN_TRADE_COUNT_HEALTHY}) 미만.",
            metric_name="trade_count",
            metric_value=float(bt.trade_count),
            threshold=float(_MIN_TRADE_COUNT_HEALTHY),
        ))

    if bt.profit_factor is not None:
        if bt.profit_factor < _MIN_PROFIT_FACTOR_CAUTION:
            findings.append(StrategyFinding(
                code=FindingCode.LOW_PROFIT_FACTOR,
                severity=ResearchSeverity.CRITICAL,
                summary=f"Profit Factor {bt.profit_factor:.2f} — 1.0 미만(손실 우세).",
                metric_name="profit_factor",
                metric_value=bt.profit_factor,
                threshold=_MIN_PROFIT_FACTOR_CAUTION,
            ))
            suggestions.append(StrategySuggestion(
                category=SuggestionCategory.PARAMETER_TUNE,
                severity=ResearchSeverity.CRITICAL,
                title="진입 / 청산 임계 재검토 — 손절 폭 / 익절 비율 / 진입 confluence",
                rationale=f"PF {bt.profit_factor:.2f} < 1.0 은 평균적으로 손실. 현재 파라미터 체계로는 시장 우위 없음.",
                proposed_change="손절 비율을 작게(예: -1.5%→-1.0%) 또는 익절 비율을 크게(예: +2%→+3%) 변경한 변종 백테스트.",
                required_validation=("새 파라미터 백테스트", "walk-forward 재검증", "Monte Carlo로 ROR 재측정"),
                references=("docs/strategy_researcher_agent.md §6",),
            ))
        elif bt.profit_factor < _MIN_PROFIT_FACTOR_HEALTHY:
            findings.append(StrategyFinding(
                code=FindingCode.LOW_PROFIT_FACTOR,
                severity=ResearchSeverity.WARNING,
                summary=f"Profit Factor {bt.profit_factor:.2f} — promotion 임계({_MIN_PROFIT_FACTOR_HEALTHY:.2f}) 미만.",
                metric_name="profit_factor",
                metric_value=bt.profit_factor,
                threshold=_MIN_PROFIT_FACTOR_HEALTHY,
            ))

    if bt.expectancy is not None and bt.expectancy < 0:
        findings.append(StrategyFinding(
            code=FindingCode.NEGATIVE_EXPECTANCY,
            severity=ResearchSeverity.CRITICAL,
            summary=f"기대값 {bt.expectancy:.2f} — 음수(평균적으로 손실).",
            metric_name="expectancy",
            metric_value=bt.expectancy,
            threshold=0.0,
        ))

    if bt.win_rate is not None and bt.win_rate < _MIN_WIN_RATE_CAUTION:
        findings.append(StrategyFinding(
            code=FindingCode.LOW_WIN_RATE,
            severity=ResearchSeverity.WARNING,
            summary=f"승률 {bt.win_rate*100:.1f}% — {_MIN_WIN_RATE_CAUTION*100:.0f}% 미만.",
            metric_name="win_rate",
            metric_value=bt.win_rate,
            threshold=_MIN_WIN_RATE_CAUTION,
        ))

    # MDD as fraction of initial_cash
    if bt.initial_cash > 0 and bt.max_drawdown > 0:
        mdd_pct = bt.max_drawdown / bt.initial_cash
        if mdd_pct >= _MAX_DRAWDOWN_PCT_CAUTION:
            findings.append(StrategyFinding(
                code=FindingCode.HIGH_MAX_DRAWDOWN,
                severity=ResearchSeverity.CRITICAL,
                summary=f"Max Drawdown {mdd_pct*100:.1f}% — 임계({_MAX_DRAWDOWN_PCT_HEALTHY*100:.0f}%) 초과.",
                metric_name="max_drawdown_pct",
                metric_value=mdd_pct,
                threshold=_MAX_DRAWDOWN_PCT_HEALTHY,
            ))
            suggestions.append(StrategySuggestion(
                category=SuggestionCategory.RISK_TIGHTEN,
                severity=ResearchSeverity.CRITICAL,
                title="포지션 사이즈 / 리스크 한도 축소",
                rationale=f"MDD {mdd_pct*100:.1f}%는 운영 가능한 한도({_MAX_DRAWDOWN_PCT_HEALTHY*100:.0f}%)를 크게 초과. 자본 절반 이상 손실 위험 존재.",
                proposed_change="quantity 또는 max_order_notional을 30-50% 축소한 변종 백테스트, 또는 stop-loss 폭을 좁힘.",
                required_validation=("새 사이즈 백테스트", "Monte Carlo ROR 재측정", "shadow 운영 권고"),
                references=("docs/risk_policy.md", "docs/strategy_researcher_agent.md §6",),
            ))
        elif mdd_pct >= _MAX_DRAWDOWN_PCT_HEALTHY:
            findings.append(StrategyFinding(
                code=FindingCode.HIGH_MAX_DRAWDOWN,
                severity=ResearchSeverity.WARNING,
                summary=f"Max Drawdown {mdd_pct*100:.1f}% — promotion 임계({_MAX_DRAWDOWN_PCT_HEALTHY*100:.0f}%) 초과.",
                metric_name="max_drawdown_pct",
                metric_value=mdd_pct,
                threshold=_MAX_DRAWDOWN_PCT_HEALTHY,
            ))

    if bt.max_consecutive_losses >= _MAX_CONSECUTIVE_LOSSES_CAUTION:
        findings.append(StrategyFinding(
            code=FindingCode.HIGH_CONSECUTIVE_LOSSES,
            severity=ResearchSeverity.WARNING,
            summary=f"연속 손실 최대 {bt.max_consecutive_losses}회 — 임계({_MAX_CONSECUTIVE_LOSSES_HEALTHY})보다 큼.",
            metric_name="max_consecutive_losses",
            metric_value=float(bt.max_consecutive_losses),
            threshold=float(_MAX_CONSECUTIVE_LOSSES_HEALTHY),
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.ADD_FILTER,
            severity=ResearchSeverity.WARNING,
            title="시장 regime / 변동성 / 추세 필터 추가",
            rationale=f"연속 손실 {bt.max_consecutive_losses}회는 특정 시장 환경에서 전략이 무력함을 시사. regime / volatility / trend 필터로 그 구간 회피 후보.",
            proposed_change="MarketObserverAgent(#52)의 `recommended_stance=WATCH_ONLY/PAUSE_NEW_BUY` 시 진입 차단 등 entry filter 추가.",
            required_validation=("필터 추가 후 재백테스트", "walk-forward 재검증"),
            references=("docs/market_observer_agent.md",),
        ))

    # Hourly imbalance — 단일 hour가 전체 PnL의 50% 이상 차지하면 시간대 의존
    if bt.total_pnl != 0 and bt.hourly_pnl:
        positive_total = sum(v for v in bt.hourly_pnl.values() if v > 0)
        if positive_total > 0:
            top_hour, top_pnl = max(bt.hourly_pnl.items(), key=lambda kv: kv[1])
            if top_pnl > 0 and top_pnl / positive_total >= _HOURLY_DOMINANCE_PCT:
                findings.append(StrategyFinding(
                    code=FindingCode.HOURLY_PNL_IMBALANCE,
                    severity=ResearchSeverity.CAUTION,
                    summary=f"수익의 {top_pnl/positive_total*100:.1f}%가 단일 시간대(UTC {top_hour}h)에 집중 — 시간대 편향.",
                    metric_name="top_hour_pnl_share",
                    metric_value=top_pnl / positive_total,
                    threshold=_HOURLY_DOMINANCE_PCT,
                    detail={"top_hour_utc": top_hour, "top_hour_pnl": top_pnl},
                ))
                suggestions.append(StrategySuggestion(
                    category=SuggestionCategory.TIMEFRAME_FILTER,
                    severity=ResearchSeverity.CAUTION,
                    title=f"UTC {top_hour}h 외 시간대 진입 검토 / 회피 고려",
                    rationale=f"전체 양수 PnL의 {top_pnl/positive_total*100:.0f}%가 한 시간에 집중 — 다른 시간대는 거의 무수익이거나 손실 가능성.",
                    proposed_change="해당 시간대만 진입하는 변종 vs 시간대 무관 변종 비교 백테스트.",
                    required_validation=("시간대 필터링 변종 백테스트", "walk-forward 재검증"),
                ))


def _check_walk_forward(
    wf: WalkForwardSummary,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
) -> None:
    rec = (wf.recommendation or "").upper()
    if rec == "FAIL":
        findings.append(StrategyFinding(
            code=FindingCode.WALK_FORWARD_FAIL,
            severity=ResearchSeverity.CRITICAL,
            summary="Walk-forward 검증 FAIL — out-of-sample 안정성 미달.",
            metric_name="walk_forward_recommendation",
            detail={"warnings": list(wf.warnings)},
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.OVERFIT_GUARD,
            severity=ResearchSeverity.CRITICAL,
            title="과최적화 의심 — 파라미터 자유도 축소",
            rationale="Walk-forward FAIL은 in-sample 결과가 out-of-sample에서 재현되지 않음을 시사. 파라미터 자유도 / 데이터 스누핑 의심.",
            proposed_change="파라미터 수를 줄이거나 단순화. 또는 cross-validation으로 holdout 강화.",
            required_validation=("파라미터 단순화 백테스트", "더 긴 holdout 기간 재검증", "Monte Carlo ROR 재측정"),
            references=("docs/strategy_researcher_agent.md §6",),
        ))
    elif rec == "CAUTION":
        findings.append(StrategyFinding(
            code=FindingCode.WALK_FORWARD_CAUTION,
            severity=ResearchSeverity.WARNING,
            summary="Walk-forward 검증 CAUTION — 일부 fold에서 불안정.",
            metric_name="walk_forward_recommendation",
            detail={"warnings": list(wf.warnings)},
        ))

    if wf.positive_fold_ratio is not None and wf.positive_fold_ratio < _MIN_POSITIVE_FOLD_RATIO_HEALTHY:
        findings.append(StrategyFinding(
            code=FindingCode.LOW_POSITIVE_FOLD_RATIO,
            severity=ResearchSeverity.WARNING,
            summary=f"Positive fold ratio {wf.positive_fold_ratio*100:.0f}% — 임계({_MIN_POSITIVE_FOLD_RATIO_HEALTHY*100:.0f}%) 미만.",
            metric_name="positive_fold_ratio",
            metric_value=wf.positive_fold_ratio,
            threshold=_MIN_POSITIVE_FOLD_RATIO_HEALTHY,
        ))

    if wf.single_best_fold_share is not None and wf.single_best_fold_share > _MAX_SINGLE_FOLD_SHARE_HEALTHY:
        findings.append(StrategyFinding(
            code=FindingCode.SINGLE_FOLD_DOMINANCE,
            severity=ResearchSeverity.WARNING,
            summary=f"단일 fold가 PnL의 {wf.single_best_fold_share*100:.0f}% 점유 — fold 간 일관성 약함.",
            metric_name="single_best_fold_share",
            metric_value=wf.single_best_fold_share,
            threshold=_MAX_SINGLE_FOLD_SHARE_HEALTHY,
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.OVERFIT_GUARD,
            severity=ResearchSeverity.WARNING,
            title="특정 시장 구간에서만 작동하는지 검증",
            rationale=f"단일 fold가 PnL의 {wf.single_best_fold_share*100:.0f}%를 차지 — 그 fold가 특정 추세 / 변동성 구간일 가능성.",
            proposed_change="해당 fold 데이터를 분리해 시장 regime / volatility profile 비교. 다른 fold에서 작동하는 조건 파악 후 filter 추가.",
            required_validation=("fold별 시장 regime 분석", "regime 필터 추가 백테스트"),
        ))

    if wf.overfit_risk_score is not None and wf.overfit_risk_score > _MAX_OVERFIT_RISK_HEALTHY:
        findings.append(StrategyFinding(
            code=FindingCode.OVERFIT_RISK_HIGH,
            severity=ResearchSeverity.WARNING,
            summary=f"Overfit risk score {wf.overfit_risk_score:.2f} — 0.5 초과로 과최적화 가능성.",
            metric_name="overfit_risk_score",
            metric_value=wf.overfit_risk_score,
            threshold=_MAX_OVERFIT_RISK_HEALTHY,
        ))


def _check_monte_carlo(
    mc: MonteCarloSummary,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
) -> None:
    if mc.risk_of_ruin is not None:
        if mc.risk_of_ruin >= _MAX_RISK_OF_RUIN_CRITICAL:
            findings.append(StrategyFinding(
                code=FindingCode.MONTE_CARLO_RUIN_HIGH,
                severity=ResearchSeverity.CRITICAL,
                summary=f"Risk of Ruin {mc.risk_of_ruin*100:.1f}% — 임계({_MAX_RISK_OF_RUIN_CRITICAL*100:.0f}%) 초과.",
                metric_name="risk_of_ruin",
                metric_value=mc.risk_of_ruin,
                threshold=_MAX_RISK_OF_RUIN_CRITICAL,
            ))
            suggestions.append(StrategySuggestion(
                category=SuggestionCategory.SHRINK_SIZE,
                severity=ResearchSeverity.CRITICAL,
                title="포지션 사이즈 / 일일 risk 한도 축소",
                rationale=f"Monte Carlo ROR {mc.risk_of_ruin*100:.1f}%는 자본 청산 가능성이 너무 높음. 사이즈를 줄이지 않으면 LIVE 활성화 시 큰 손실 위험.",
                proposed_change="quantity / max_daily_loss를 50% 축소 또는 fractional Kelly 기반 사이징 적용.",
                required_validation=("축소된 사이즈 백테스트", "Monte Carlo ROR 재측정 — 5% 미만 권고", "shadow 운영"),
                references=("docs/risk_policy.md",),
            ))
        elif mc.risk_of_ruin >= _MAX_RISK_OF_RUIN_HEALTHY:
            findings.append(StrategyFinding(
                code=FindingCode.MONTE_CARLO_RUIN_HIGH,
                severity=ResearchSeverity.WARNING,
                summary=f"Risk of Ruin {mc.risk_of_ruin*100:.1f}% — 임계({_MAX_RISK_OF_RUIN_HEALTHY*100:.0f}%) 초과.",
                metric_name="risk_of_ruin",
                metric_value=mc.risk_of_ruin,
                threshold=_MAX_RISK_OF_RUIN_HEALTHY,
            ))

    if mc.p05_total_pnl is not None and mc.p50_total_pnl is not None and mc.p50_total_pnl > 0:
        # p5 / p50 ratio 가 매우 낮으면 fat tail (좌측 꼬리)
        ratio = mc.p05_total_pnl / mc.p50_total_pnl if mc.p50_total_pnl > 0 else 0
        if ratio < -0.5:  # p05가 p50의 -50% 이하 (큰 좌측 꼬리)
            findings.append(StrategyFinding(
                code=FindingCode.MONTE_CARLO_FAT_TAIL,
                severity=ResearchSeverity.WARNING,
                summary="Monte Carlo 좌측 꼬리 위험 — p05 시나리오에서 큰 손실 가능성.",
                metric_name="p05_to_p50_ratio",
                metric_value=ratio,
                detail={"p05_pnl": mc.p05_total_pnl, "p50_pnl": mc.p50_total_pnl},
            ))


def _check_data_quality(
    quality_reports: tuple[DataQualitySummary, ...],
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
) -> None:
    poor_symbols = [q for q in quality_reports
                    if q.score is not None and q.score < _MIN_DATA_QUALITY_SCORE_HARD]
    warning_symbols = [q for q in quality_reports
                       if q.score is not None
                       and _MIN_DATA_QUALITY_SCORE_HARD <= q.score < _MIN_DATA_QUALITY_SCORE_HEALTHY]

    if poor_symbols:
        findings.append(StrategyFinding(
            code=FindingCode.DATA_QUALITY_POOR,
            severity=ResearchSeverity.CRITICAL,
            summary=f"데이터 quality 미달({_MIN_DATA_QUALITY_SCORE_HARD:.0f}점 미만) 종목 {len(poor_symbols)}개 — 백테스트 결과 신뢰성 저하.",
            metric_name="data_quality_poor_count",
            metric_value=float(len(poor_symbols)),
            threshold=0.0,
            detail={"symbols": [q.symbol for q in poor_symbols[:10]]},
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.DATA_QUALITY,
            severity=ResearchSeverity.CRITICAL,
            title="데이터 quality 개선 후 재백테스트",
            rationale="POOR/EXCLUDE 등급 종목은 missing / 비정상 가격 / coverage 부족 등으로 백테스트 결과가 왜곡될 수 있음.",
            proposed_change="해당 종목 제외 또는 데이터 재수집 후 재백테스트. universe에서 제외한 변종도 비교.",
            required_validation=("문제 종목 제외 후 재백테스트", "데이터 재수집 후 재실행"),
            references=("docs/data_quality_policy.md",),
        ))
    elif warning_symbols:
        findings.append(StrategyFinding(
            code=FindingCode.DATA_QUALITY_WARNING,
            severity=ResearchSeverity.WARNING,
            summary=f"데이터 quality 경계({_MIN_DATA_QUALITY_SCORE_HEALTHY:.0f}점 미만) 종목 {len(warning_symbols)}개 — 부분 영향 가능성.",
            metric_name="data_quality_warning_count",
            metric_value=float(len(warning_symbols)),
            detail={"symbols": [q.symbol for q in warning_symbols[:10]]},
        ))


def _check_promotion_gate(
    pg: PromotionGateSummary,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
) -> None:
    decision = (pg.decision or "").upper()
    if decision == "BLOCKED":
        findings.append(StrategyFinding(
            code=FindingCode.PROMOTION_BLOCKED,
            severity=ResearchSeverity.CRITICAL,
            summary=f"승격 gate BLOCKED — {pg.current_stage} → {pg.target_stage} 차단.",
            detail={
                "failed_criteria":  list(pg.failed_criteria),
                "required_actions": list(pg.required_actions),
            },
        ))
        suggestions.append(StrategySuggestion(
            category=SuggestionCategory.PROMOTION_BLOCK,
            severity=ResearchSeverity.CRITICAL,
            title=f"{pg.target_stage} 승격 *보류* — 필수 조건 충족 후 재시도",
            rationale="Strategy promotion gate(#27)가 BLOCKED 결정을 내림. 본 단계는 *코드 강제* — 운영자가 수동으로 우회하는 것은 절대 금지.",
            proposed_change="`required_actions`의 모든 항목을 충족할 때까지 현재 stage 유지.",
            required_validation=tuple(pg.required_actions) if pg.required_actions else
                              ("missing 조건 명시화 후 재시도",),
            references=("docs/promotion_policy.md",),
        ))
    elif decision == "FAIL":
        findings.append(StrategyFinding(
            code=FindingCode.PROMOTION_FAILED,
            severity=ResearchSeverity.WARNING,
            summary=f"승격 gate FAIL — {pg.current_stage} → {pg.target_stage} 실패 ({len(pg.failed_criteria)}건).",
            detail={"failed_criteria": list(pg.failed_criteria)},
        ))


# ====================================================================
# Aggregators
# ====================================================================


def _derive_audit_level(findings: list[StrategyFinding]) -> ResearchSeverity:
    if not findings:
        return ResearchSeverity.HEALTHY
    severities = {f.severity for f in findings}
    if ResearchSeverity.CRITICAL in severities:
        return ResearchSeverity.CRITICAL
    if ResearchSeverity.WARNING in severities:
        return ResearchSeverity.WARNING
    if ResearchSeverity.CAUTION in severities:
        return ResearchSeverity.CAUTION
    return ResearchSeverity.HEALTHY


def _derive_required_next_tests(
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
    inp: StrategyResearcherInput,
) -> list[str]:
    """제안 적용 *전* 운영자가 수동 실행해야 할 검증 단계."""
    tests: list[str] = []
    seen: set[str] = set()
    for s in suggestions:
        for v in s.required_validation:
            if v not in seen:
                tests.append(v)
                seen.add(v)
    if findings and "운영자 검토 / 별도 PR" not in seen:
        tests.append("운영자 검토 / 별도 PR")
    if not tests:
        tests.append("현재 결과 정상 — 정기 재검증 권고")
    return tests


def _build_summary_lines(
    audit_level: ResearchSeverity,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
    inp: StrategyResearcherInput,
) -> list[str]:
    bt = inp.backtest
    lines: list[str] = []
    lines.append(
        f"전략 분석: {bt.strategy} (run_id={bt.run_id}) — 단계 {audit_level}."
    )
    pf_str = f"{bt.profit_factor:.2f}" if bt.profit_factor is not None else "N/A"
    lines.append(
        f"트레이드 {bt.trade_count}건, PF {pf_str}, MDD {bt.max_drawdown:,}원 — "
        f"findings {len(findings)}건, 제안 {len(suggestions)}건."
    )
    if audit_level == ResearchSeverity.HEALTHY:
        lines.append("주요 임계 통과 — 정기 walk-forward / Monte Carlo 재검증 권고.")
    elif audit_level == ResearchSeverity.CAUTION:
        lines.append("일부 임계 근접 — 모니터링 + 보완 검증 권고.")
    elif audit_level == ResearchSeverity.WARNING:
        lines.append("임계 위반 다수 — 파라미터 / 필터 보완 후 재검증 필요.")
    else:
        lines.append("심각 — 운영자 수동 결정 + 별도 PR + 백테스트 재실행 필수.")
    lines.append(
        "본 리포트는 *advisory*입니다. 어떤 제안도 자동으로 코드 / 파라미터에 "
        "반영되지 않습니다 (자동 반영 안 됨)."
    )
    return lines


# ====================================================================
# Markdown report builder
# ====================================================================


_LEVEL_LABEL_KO = {
    ResearchSeverity.HEALTHY:  "정상",
    ResearchSeverity.CAUTION:  "경계",
    ResearchSeverity.WARNING:  "경고",
    ResearchSeverity.CRITICAL: "심각",
}


def _build_markdown(
    *,
    audit_level: ResearchSeverity,
    findings: list[StrategyFinding],
    suggestions: list[StrategySuggestion],
    required_next_tests: list[str],
    inp: StrategyResearcherInput,
) -> str:
    bt = inp.backtest
    parts: list[str] = []

    parts.append(f"# Strategy Research Report — {bt.strategy}")
    parts.append("")
    parts.append(
        f"_Generated: {datetime.now(timezone.utc).isoformat()} · run_id={bt.run_id}_"
    )
    parts.append("")
    parts.append("> ⚠ **자동 반영 안 됨 / PR 검토 필요**")
    parts.append("> 본 리포트는 *advisory*입니다. 어떤 제안도 자동으로 코드 / 파라미터")
    parts.append("> 에 반영되지 *않습니다*. 적용을 검토하려면 운영자 검토 → 별도 PR →")
    parts.append("> 별도 백테스트 → walk-forward → paper/shadow → live 절차가 필요합니다.")
    parts.append("")

    # 1. 분석 대상
    parts.append("## 1. 분석 대상")
    parts.append("")
    parts.append(f"- **Strategy**: `{bt.strategy}`")
    parts.append(f"- **BacktestRun ID**: `{bt.run_id}`")
    parts.append(f"- **Created**: {bt.created_at.isoformat() if bt.created_at else 'N/A'}")
    if bt.data_symbol:
        parts.append(f"- **Symbol**: `{bt.data_symbol}` ({bt.data_interval or 'N/A'})")
    if bt.data_start and bt.data_end:
        parts.append(
            f"- **Period**: {bt.data_start.date()} → {bt.data_end.date()}"
        )
    if bt.params:
        parts.append(f"- **Params**: `{bt.params}`")
    parts.append("")

    # 2. 핵심 metric
    parts.append("## 2. 핵심 metric")
    parts.append("")
    parts.append("| Metric | Value | Threshold | Verdict |")
    parts.append("|---|---|---|---|")
    parts.append(_metric_row(
        "트레이드 수", f"{bt.trade_count}", f"≥ {_MIN_TRADE_COUNT_HEALTHY}",
        bt.trade_count >= _MIN_TRADE_COUNT_HEALTHY,
    ))
    if bt.profit_factor is not None:
        parts.append(_metric_row(
            "Profit Factor", f"{bt.profit_factor:.2f}",
            f"≥ {_MIN_PROFIT_FACTOR_HEALTHY:.2f}",
            bt.profit_factor >= _MIN_PROFIT_FACTOR_HEALTHY,
        ))
    if bt.expectancy is not None:
        parts.append(_metric_row(
            "Expectancy", f"{bt.expectancy:.2f}", "> 0",
            bt.expectancy > 0,
        ))
    if bt.win_rate is not None:
        parts.append(_metric_row(
            "Win Rate", f"{bt.win_rate*100:.1f}%",
            f"≥ {_MIN_WIN_RATE_CAUTION*100:.0f}%",
            bt.win_rate >= _MIN_WIN_RATE_CAUTION,
        ))
    if bt.initial_cash > 0 and bt.max_drawdown > 0:
        mdd_pct = bt.max_drawdown / bt.initial_cash
        parts.append(_metric_row(
            "Max Drawdown", f"{mdd_pct*100:.1f}%",
            f"≤ {_MAX_DRAWDOWN_PCT_HEALTHY*100:.0f}%",
            mdd_pct <= _MAX_DRAWDOWN_PCT_HEALTHY,
        ))
    parts.append(_metric_row(
        "연속 손실 최대", f"{bt.max_consecutive_losses}",
        f"≤ {_MAX_CONSECUTIVE_LOSSES_HEALTHY}",
        bt.max_consecutive_losses <= _MAX_CONSECUTIVE_LOSSES_HEALTHY,
    ))
    if inp.walk_forward and inp.walk_forward.recommendation:
        parts.append(_metric_row(
            "Walk-Forward", inp.walk_forward.recommendation, "PASS",
            inp.walk_forward.recommendation.upper() == "PASS",
        ))
    if inp.monte_carlo and inp.monte_carlo.risk_of_ruin is not None:
        parts.append(_metric_row(
            "Risk of Ruin", f"{inp.monte_carlo.risk_of_ruin*100:.1f}%",
            f"< {_MAX_RISK_OF_RUIN_HEALTHY*100:.0f}%",
            inp.monte_carlo.risk_of_ruin < _MAX_RISK_OF_RUIN_HEALTHY,
        ))
    parts.append("")

    # 3. Findings
    parts.append(f"## 3. Findings ({len(findings)}건)")
    parts.append("")
    if not findings:
        parts.append("_관찰된 문제 없음 — 모든 임계 통과._")
    else:
        for i, f in enumerate(findings, 1):
            parts.append(f"### {i}. `{f.code}` — {f.severity}")
            parts.append("")
            parts.append(f"- **Summary**: {f.summary}")
            if f.metric_name and f.metric_value is not None:
                threshold = f" (threshold: {f.threshold})" if f.threshold is not None else ""
                parts.append(f"- **Metric**: `{f.metric_name}` = {f.metric_value:.4g}{threshold}")
            if f.detail:
                parts.append(f"- **Detail**: `{f.detail}`")
            parts.append("")

    # 4. Suggestions
    parts.append(f"## 4. 개선 제안 ({len(suggestions)}건)")
    parts.append("")
    if not suggestions:
        parts.append("_추가 제안 없음._")
    else:
        for i, s in enumerate(suggestions, 1):
            parts.append(f"### {i}. `{s.category}` — {s.severity}: {s.title}")
            parts.append("")
            parts.append(f"- **Why**: {s.rationale}")
            parts.append(f"- **Proposed change**: {s.proposed_change}")
            if s.required_validation:
                parts.append("- **Required validation (운영자가 수동 실행)**:")
                for v in s.required_validation:
                    parts.append(f"  - [ ] {v}")
            if s.references:
                parts.append("- **References**: " +
                             ", ".join(f"`{r}`" for r in s.references))
            parts.append("")

    # 5. Required next tests
    parts.append("## 5. Required Next Tests (반드시 수동 실행)")
    parts.append("")
    for t in required_next_tests:
        parts.append(f"- [ ] {t}")
    parts.append("")

    # 6. 한계
    parts.append("## 6. 한계 (반드시 검증 필요)")
    parts.append("")
    parts.append("- 본 분석은 단일 BacktestRun + 외부 metric 결과의 *통계 요약*입니다.")
    parts.append("- 규칙 기반 판정이 시장 환경 변화 / 비정상 데이터 / 거시 이벤트를")
    parts.append("  *모두 포착하지는 못합니다*.")
    parts.append("- 제안된 파라미터 변경은 표본 외 데이터에서 다르게 작동할 수 있습니다.")
    parts.append("- 어떤 제안도 *자동으로 적용되지 않으며*, 운영자 검토 / 별도 PR /")
    parts.append("  별도 백테스트 / paper / shadow 검증을 거쳐야만 LIVE에 반영됩니다.")
    parts.append("- AI / 규칙 기반 제안은 틀릴 수 있음 — 통계적 유의성 / 인과관계 /")
    parts.append("  domain 지식과 함께 *반드시 검증*해야 합니다.")
    parts.append("")
    parts.append("---")
    parts.append("")
    parts.append("_본 Agent는 broker / OrderExecutor / route_order를 호출하지 않으며,_")
    parts.append("_strategy 코드 / 파라미터를 직접 수정하지 않습니다. 모든 출력은 advisory._")

    return "\n".join(parts)


def _metric_row(name: str, value: str, threshold: str, ok: bool) -> str:
    verdict = "✓ PASS" if ok else "✗ FAIL"
    return f"| {name} | {value} | {threshold} | {verdict} |"


# ====================================================================
# Agent class — #51 AgentBase 호환
# ====================================================================


class StrategyResearcherAgent(AgentBase):
    """#55 enhanced — DB-backed 전략 연구 advisory.

    `app.agents.roles.StrategyResearcherAgent`(#51 mock)는 stub로 남고, 본
    클래스는 BacktestRun + 외부 metric을 받아 markdown 리포트 + 구조화된
    제안을 *반환*만 한다. **자동 적용 / 자동 주문 / 자동 토글 0건**.
    """

    @property
    def metadata(self) -> AgentMetadata:
        return AgentMetadata(
            name="strategy_researcher",
            role=AgentRole.STRATEGY_RESEARCHER,
            description=(
                "BacktestRun + walk-forward + Monte Carlo + data quality + "
                "promotion gate를 read-only로 분석해 전략 개선 후보를 markdown "
                "advisory 리포트로 제안. 자동 반영 안 됨."
            ),
            inputs=[
                "BacktestSummary (BacktestRun + metrics #24)",
                "WalkForwardSummary (#25, optional)",
                "MonteCarloSummary (#26, optional)",
                "DataQualitySummary[] (#21, optional)",
                "PromotionGateSummary (#27, optional)",
            ],
            outputs=[
                "StrategyResearchReport (auto_apply_allowed=False, "
                "is_order_signal=False, markdown_report)",
            ],
            forbidden=[
                "BUY / SELL / HOLD 주문 신호 반환 금지",
                "approval queue 등록 금지",
                "broker / OrderExecutor / route_order 호출 금지",
                "strategy 코드 / 파라미터 자동 수정 금지 (advisory only)",
                "DB INSERT / UPDATE / DELETE 금지 (read-only SELECT만)",
                "외부 AI / HTTP 호출 금지",
            ],
            can_execute_order=False,
        )

    def run(self, context: AgentContext) -> AgentOutput:
        extra = context.extra or {}
        researcher_input = extra.get("researcher_input")
        if not isinstance(researcher_input, StrategyResearcherInput):
            return AgentOutput(
                role=AgentRole.STRATEGY_RESEARCHER,
                decision=AgentDecision.NO_OP,
                summary="researcher_input 미제공 — 분석 생략.",
                reasons=["context.extra['researcher_input']에 StrategyResearcherInput 필요"],
                metadata={"reason": "missing_input"},
            )
        report = analyze_strategy(researcher_input)
        decision = (
            AgentDecision.RECOMMEND
            if report.audit_level in (ResearchSeverity.WARNING, ResearchSeverity.CRITICAL)
            else AgentDecision.REPORT
        )
        return AgentOutput(
            role=AgentRole.STRATEGY_RESEARCHER,
            decision=decision,
            summary=report.summary_lines[0] if report.summary_lines else
                    f"전략 분석 완료 — {report.audit_level}",
            reasons=[f.summary for f in report.findings[:5]],
            metadata={
                "audit_level":         report.audit_level,
                "findings_count":      len(report.findings),
                "suggestions_count":   len(report.suggestions),
                "auto_apply_allowed":  False,
                "is_order_signal":     False,
            },
        )
