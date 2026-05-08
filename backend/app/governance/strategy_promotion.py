"""Strategy Promotion Gate (#27).

`docs/promotion_policy.md`의 단계별 승격 기준을 코드 단으로 강제한다.
검증되지 않은 전략이 Paper / Live Manual / AI Assist / AI Execution 단계로
올라가지 못하도록 *판단 결과*를 반환한다.

CLAUDE.md 절대 원칙:
- 본 모듈은 *판단만* 한다 — 실제 모드 변경, broker 호출, LIVE flag 변경은
  하지 않는다. 호출자(운영자 / 별도 PR)가 결과를 보고 직접 결정.
- broker / RiskManager / PermissionGate / OrderExecutor import 0건.
- AI 추천(`ai_recommended=True`)만으로 승격 불가 — 사람 승인 + 코드 기준
  모두 필요.
- 사람 승인(`human_approved=True`)이 있어도 코드 기준 미달이면 FAIL.
- 코드 기준 PASS여도 사람 승인이 없으면 LIVE 단계는 BLOCKED.

자세한 정책: `docs/strategy_promotion_gate.md` (본 PR로 신설).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


# ---------- 단계 정의 ----------


class PromotionStage(StrEnum):
    """단계 — promotion_policy.md의 운용 모드와 lockstep."""
    BACKTEST              = "BACKTEST"
    LIVE_SHADOW           = "LIVE_SHADOW"
    PAPER                 = "PAPER"
    LIVE_MANUAL_APPROVAL  = "LIVE_MANUAL_APPROVAL"
    LIVE_AI_ASSIST        = "LIVE_AI_ASSIST"
    LIVE_AI_EXECUTION     = "LIVE_AI_EXECUTION"


# 다음 단계 매핑 — 한 번에 한 단계만 승격 검토.
_NEXT_STAGE: dict[PromotionStage, PromotionStage] = {
    PromotionStage.BACKTEST:             PromotionStage.LIVE_SHADOW,
    PromotionStage.LIVE_SHADOW:          PromotionStage.PAPER,
    PromotionStage.PAPER:                PromotionStage.LIVE_MANUAL_APPROVAL,
    PromotionStage.LIVE_MANUAL_APPROVAL: PromotionStage.LIVE_AI_ASSIST,
    PromotionStage.LIVE_AI_ASSIST:       PromotionStage.LIVE_AI_EXECUTION,
}


def next_stage(current: PromotionStage) -> PromotionStage | None:
    return _NEXT_STAGE.get(current)


# LIVE 자금이 들어가는 단계.
LIVE_STAGES: frozenset[PromotionStage] = frozenset({
    PromotionStage.LIVE_MANUAL_APPROVAL,
    PromotionStage.LIVE_AI_ASSIST,
    PromotionStage.LIVE_AI_EXECUTION,
})


# ---------- 결정 ----------


class PromotionDecision(StrEnum):
    PASS    = "PASS"      # 코드 기준 충족 + (LIVE 단계라면) 사람 승인까지
    CAUTION = "CAUTION"   # 일부 기준 약하지만 통과 가능 — 운영자 검토
    FAIL    = "FAIL"      # 코드 기준 미달
    BLOCKED = "BLOCKED"   # 정책상 차단 (예: AI Execution 영구 차단, 사람 승인 부재)


# ---------- 정책 임계 (promotion_policy.md / backtest_metrics.md / ... 와 lockstep) ----------


# Backtest
MIN_TRADE_COUNT          = 100
MIN_PROFIT_FACTOR        = 1.2
MIN_HOLDOUT_PNL          = 0
MAX_DRAWDOWN_PCT         = 0.15   # 운영 자본의 15%
MAX_CONSEC_LOSSES        = 5

# Walk-forward
MIN_POSITIVE_FOLD_RATIO  = 0.6
MAX_SINGLE_FOLD_PNL_SHARE = 0.7

# Monte Carlo
MAX_RISK_OF_RUIN_FAIL    = 0.05    # 5% 초과면 FAIL
MAX_RISK_OF_RUIN_AI_EXEC = 0.01    # AI Execution 단계는 1% 이하
MAX_WORST_5PCT_MDD_PCT   = 0.30    # 운영 자본의 30%

# Data Quality (#21)
MIN_DATA_QUALITY_SCORE_HARD  = 60   # 60 미만 즉시 FAIL
MIN_DATA_QUALITY_SCORE_BASIC = 75   # 75 이상이 기본 허용

# Paper / Shadow
MIN_SHADOW_DAYS         = 28
MIN_PAPER_DAYS          = 28
MIN_LIVE_MANUAL_DAYS    = 28


# ---------- 입력 / 결과 DTO ----------


@dataclass(frozen=True)
class PromotionInput:
    """전략 승격 평가 입력. 호출자가 채우는 단일 dataclass.

    필드는 모두 optional/default — 미제공 시 보수적으로 가정 (기준 미달 처리).
    """
    strategy_name:   str
    current_stage:   PromotionStage
    target_stage:    PromotionStage

    # ---- Backtest metrics (#23/24) ----
    trade_count:           int   = 0
    expectancy:            float = 0.0
    profit_factor:         float | None = None
    max_drawdown:          int   = 0
    max_consecutive_losses: int  = 0
    win_rate:              float = 0.0
    initial_cash:          int   = 10_000_000
    cost_adjusted:         bool  = False    # 수수료/세금 반영
    slippage_adjusted:     bool  = False    # 슬리피지 반영

    # ---- Walk-forward (#25) ----
    walk_forward_passed:        bool | None = None
    walk_forward_recommendation: str | None = None  # PASS / CAUTION / FAIL
    positive_fold_ratio:        float | None = None
    holdout_pnl:                int | None  = None
    single_best_fold_pnl_share: float | None = None

    # ---- Monte Carlo (#26) ----
    monte_carlo_run:        bool = False
    monte_carlo_risk_of_ruin: float | None = None
    monte_carlo_worst_5pct_mdd: int | None = None
    monte_carlo_longest_losing_streak: int | None = None

    # ---- Data Quality (#21) ----
    data_quality_score:     float | None = None   # 0~100
    data_quality_grade:     str | None   = None   # GOOD / WARNING / POOR / EXCLUDE

    # ---- Paper / Shadow operational reality ----
    shadow_days:                  int = 0
    paper_days:                   int = 0
    live_manual_days:             int = 0
    daily_loss_limit_violations:  int = 0
    risk_policy_violations:       int = 0
    audit_log_missing_count:      int = 0
    partial_fill_audit_ok:        bool = True

    # ---- Approval flags ----
    human_approved:    bool = False
    ai_recommended:    bool = False
    ai_recommendation_accuracy: float | None = None  # 0~1, LIVE_AI_ASSIST→AI_EXECUTION 단계


@dataclass
class PromotionResult:
    strategy_name:   str
    current_stage:   PromotionStage
    target_stage:    PromotionStage
    decision:        PromotionDecision
    failed_criteria: list[str] = field(default_factory=list)
    cautions:        list[str] = field(default_factory=list)
    warnings:        list[str] = field(default_factory=list)
    required_actions: list[str] = field(default_factory=list)
    passed_criteria: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy_name":   self.strategy_name,
            "current_stage":   self.current_stage.value,
            "target_stage":    self.target_stage.value,
            "decision":        self.decision.value,
            "failed_criteria": list(self.failed_criteria),
            "cautions":        list(self.cautions),
            "warnings":        list(self.warnings),
            "required_actions": list(self.required_actions),
            "passed_criteria": list(self.passed_criteria),
            # invariant — 본 모듈은 mode를 변경하지 않는다.
            "mode_changed":    False,
            "live_flag_changed": False,
        }


# ---------- 평가 메인 ----------


def evaluate_promotion(inp: PromotionInput) -> PromotionResult:
    """승격 단계별 코드 기준을 평가해 PromotionResult 반환.

    어떤 경우에도 *판단만* — 실제 모드 변경, broker 호출, LIVE flag 변경 없음.
    """
    target = inp.target_stage
    expected_next = next_stage(inp.current_stage)
    if expected_next is None or target != expected_next:
        return PromotionResult(
            strategy_name=inp.strategy_name,
            current_stage=inp.current_stage, target_stage=target,
            decision=PromotionDecision.BLOCKED,
            failed_criteria=[
                f"current={inp.current_stage.value} 에서 target={target.value} 로 직접 승격 불가. "
                f"한 번에 한 단계씩만 진행 (다음: {expected_next.value if expected_next else '없음'})."
            ],
            required_actions=["target_stage 를 다음 단계로 지정하거나 단계별 평가 반복."],
        )

    # 단계별 평가 dispatch.
    if target == PromotionStage.LIVE_SHADOW:
        return _eval_to_shadow(inp)
    if target == PromotionStage.PAPER:
        return _eval_to_paper(inp)
    if target == PromotionStage.LIVE_MANUAL_APPROVAL:
        return _eval_to_live_manual(inp)
    if target == PromotionStage.LIVE_AI_ASSIST:
        return _eval_to_live_ai_assist(inp)
    if target == PromotionStage.LIVE_AI_EXECUTION:
        return _eval_to_live_ai_execution(inp)
    # 도달 불가 — next_stage 매핑이 보장.
    return PromotionResult(
        strategy_name=inp.strategy_name,
        current_stage=inp.current_stage, target_stage=target,
        decision=PromotionDecision.BLOCKED,
        failed_criteria=[f"unsupported target stage: {target}"],
    )


# ---------- 단계별 평가 ----------


def _eval_to_shadow(inp: PromotionInput) -> PromotionResult:
    """BACKTEST → LIVE_SHADOW. 백테스트 통과 + Walk-forward + (옵션) MC + Data Quality."""
    r = _empty_result(inp)
    _check_backtest_basic(inp, r)
    _check_walk_forward(inp, r, required=True)
    _check_monte_carlo(inp, r, hard_threshold=MAX_RISK_OF_RUIN_FAIL, required=False)
    _check_data_quality(inp, r)
    _finalize(r, inp)
    return r


def _eval_to_paper(inp: PromotionInput) -> PromotionResult:
    """LIVE_SHADOW → PAPER. Shadow 운영 ≥28일 + audit 무누락."""
    r = _empty_result(inp)
    _check_backtest_basic(inp, r)
    _check_walk_forward(inp, r, required=True)
    _check_monte_carlo(inp, r, hard_threshold=MAX_RISK_OF_RUIN_FAIL, required=False)
    _check_data_quality(inp, r)
    _check_shadow_operational(inp, r)
    _finalize(r, inp)
    return r


def _eval_to_live_manual(inp: PromotionInput) -> PromotionResult:
    """PAPER → LIVE_MANUAL_APPROVAL. Paper 운영 ≥28일 + violations==0 + 사람 승인."""
    r = _empty_result(inp)
    _check_backtest_basic(inp, r)
    _check_walk_forward(inp, r, required=True)
    _check_monte_carlo(inp, r, hard_threshold=MAX_RISK_OF_RUIN_FAIL, required=True)
    _check_data_quality(inp, r)
    _check_paper_operational(inp, r)
    _check_human_approval(inp, r)
    _finalize(r, inp)
    return r


def _eval_to_live_ai_assist(inp: PromotionInput) -> PromotionResult:
    """LIVE_MANUAL_APPROVAL → LIVE_AI_ASSIST. AI 추천 정확도 보고 + 1개월 수동승인 운용."""
    r = _empty_result(inp)
    _check_backtest_basic(inp, r)
    _check_walk_forward(inp, r, required=True)
    _check_monte_carlo(inp, r, hard_threshold=MAX_RISK_OF_RUIN_FAIL, required=True)
    _check_data_quality(inp, r)
    _check_live_manual_operational(inp, r)
    _check_ai_recommendation_accuracy(inp, r)
    _check_human_approval(inp, r)
    _finalize(r, inp)
    return r


def _eval_to_live_ai_execution(inp: PromotionInput) -> PromotionResult:
    """LIVE_AI_ASSIST → LIVE_AI_EXECUTION. **기본 BLOCKED** — CLAUDE.md 절대 원칙.

    모든 고급 기준 충족 + 사람 승인 + AI 추천 정확도까지 PASS여도, 본 모듈은
    실 LIVE flag를 변경하지 않는다. 결과는 '승격 검토 가능'까지만.
    """
    r = _empty_result(inp)
    _check_backtest_basic(inp, r)
    _check_walk_forward(inp, r, required=True)
    # 더 보수적인 ROR 임계 (1%).
    _check_monte_carlo(
        inp, r, hard_threshold=MAX_RISK_OF_RUIN_AI_EXEC, required=True,
    )
    _check_data_quality(inp, r)
    _check_live_manual_operational(inp, r)
    _check_ai_recommendation_accuracy(inp, r)
    _check_human_approval(inp, r)

    # AI Execution은 사람 승인 + 코드 기준 모두 통과해도 본 모듈에서 *PASS 반환 안 함*.
    # 운영자가 별도 옵트인 PR + 환경변수로만 활성화 가능 (CLAUDE.md).
    if not r.failed_criteria:
        # 코드 기준은 다 통과 — 그래도 BLOCKED.
        r.cautions.append(
            "LIVE_AI_EXECUTION은 코드 기준 통과만으로 활성화되지 않는다 — "
            "별도 옵트인 PR + ENABLE_AI_EXECUTION=true 명시 + 운영자 직접 토글 필요. "
            "본 결과는 '승격 검토 가능'까지만 의미함."
        )
        r.required_actions.append(
            "별도 옵트인 PR로 ENABLE_AI_EXECUTION=true + LIVE_AI_EXECUTION 모드 활성화."
        )
        r.decision = PromotionDecision.BLOCKED
        return r
    _finalize(r, inp)
    # 코드 기준 미달이면 FAIL이 그대로 반영. 사람 승인 부재면 BLOCKED.
    return r


# ---------- 공통 helpers ----------


def _empty_result(inp: PromotionInput) -> PromotionResult:
    return PromotionResult(
        strategy_name=inp.strategy_name,
        current_stage=inp.current_stage,
        target_stage=inp.target_stage,
        decision=PromotionDecision.PASS,
    )


def _finalize(r: PromotionResult, inp: PromotionInput) -> None:
    """failed_criteria가 있으면 FAIL. LIVE 단계 + 사람 승인 부재면 BLOCKED."""
    if r.failed_criteria:
        r.decision = PromotionDecision.FAIL
        return
    # LIVE 단계 + 사람 승인 부재 → BLOCKED.
    if inp.target_stage in LIVE_STAGES and not inp.human_approved:
        r.decision = PromotionDecision.BLOCKED
        r.failed_criteria.append("LIVE 단계 승격에는 사람 승인(human_approved=true) 필수.")
        r.required_actions.append("운영자 직접 검토 후 human_approved=true로 재요청.")
        return
    if r.cautions:
        r.decision = PromotionDecision.CAUTION
        return
    r.decision = PromotionDecision.PASS


# ---------- 기준 체크 ----------


def _check_backtest_basic(inp: PromotionInput, r: PromotionResult) -> None:
    """공통 backtest 기준 — 모든 단계."""
    if inp.trade_count < MIN_TRADE_COUNT:
        r.failed_criteria.append(
            f"거래 수 부족: {inp.trade_count} < {MIN_TRADE_COUNT}회. "
            "표본 부족으로 통계 신뢰도 약함."
        )
    else:
        r.passed_criteria.append(f"거래 수 {inp.trade_count}회 ≥ {MIN_TRADE_COUNT}")

    if inp.expectancy <= 0:
        r.failed_criteria.append(
            f"기대값(expectancy) 양수 아님: {inp.expectancy:.2f}. "
            "평균적으로 손실 — 승격 불가."
        )
    else:
        r.passed_criteria.append(f"expectancy={inp.expectancy:.2f} > 0")

    if inp.profit_factor is None:
        r.failed_criteria.append(
            "Profit Factor 미산출 — 손실 거래 0건이거나 표본 부족. 추가 백테스트 필요."
        )
    elif inp.profit_factor < MIN_PROFIT_FACTOR:
        r.failed_criteria.append(
            f"Profit Factor {inp.profit_factor:.2f} < 기준 {MIN_PROFIT_FACTOR}."
        )
    else:
        r.passed_criteria.append(f"Profit Factor {inp.profit_factor:.2f} ≥ {MIN_PROFIT_FACTOR}")

    max_dd_limit = int(inp.initial_cash * MAX_DRAWDOWN_PCT)
    if inp.max_drawdown > max_dd_limit:
        r.failed_criteria.append(
            f"MDD {inp.max_drawdown:,} > 한도 {max_dd_limit:,} "
            f"(운영 자본의 {MAX_DRAWDOWN_PCT:.0%})."
        )
    else:
        r.passed_criteria.append(f"MDD {inp.max_drawdown:,} ≤ {max_dd_limit:,}")

    if inp.max_consecutive_losses > MAX_CONSEC_LOSSES:
        r.failed_criteria.append(
            f"최장 연속손실 {inp.max_consecutive_losses}회 > {MAX_CONSEC_LOSSES} — "
            "운영 자본 / 운영자 심리 영향 우려."
        )

    if not inp.cost_adjusted:
        r.failed_criteria.append(
            "수수료/세금 미반영 백테스트 — 승격 불가. "
            "BacktestConfig.commission_bps + tax_bps 명시 필수 (#23)."
        )
    if not inp.slippage_adjusted:
        r.failed_criteria.append(
            "슬리피지 미반영 백테스트 — 승격 불가. "
            "BacktestConfig.slippage_bps≥5 명시 필수 (#23)."
        )


def _check_walk_forward(inp: PromotionInput, r: PromotionResult,
                       *, required: bool) -> None:
    """Walk-forward (#25) gate."""
    if inp.walk_forward_passed is None and inp.walk_forward_recommendation is None:
        if required:
            r.failed_criteria.append(
                "Walk-forward 결과 없음 — POST /api/backtest/walk-forward 실행 필요."
            )
        return

    rec = inp.walk_forward_recommendation
    if rec == "FAIL":
        r.failed_criteria.append(
            "Walk-forward 추천 FAIL — promotion_policy.md 기준 미달."
        )
    elif rec == "CAUTION":
        r.cautions.append(
            "Walk-forward 추천 CAUTION — 한 fold 대박 의존 또는 overfit 의심."
        )
    elif rec == "PASS":
        r.passed_criteria.append("Walk-forward 추천 PASS")

    if inp.positive_fold_ratio is not None:
        if inp.positive_fold_ratio < MIN_POSITIVE_FOLD_RATIO:
            r.failed_criteria.append(
                f"양수 fold 비율 {inp.positive_fold_ratio:.0%} < "
                f"기준 {MIN_POSITIVE_FOLD_RATIO:.0%}."
            )

    if inp.holdout_pnl is not None and inp.holdout_pnl <= MIN_HOLDOUT_PNL:
        r.failed_criteria.append(
            f"holdout PnL {inp.holdout_pnl} ≤ {MIN_HOLDOUT_PNL} — out-of-sample 실패."
        )

    if (inp.single_best_fold_pnl_share is not None
            and inp.single_best_fold_pnl_share > MAX_SINGLE_FOLD_PNL_SHARE):
        r.cautions.append(
            f"한 fold가 양수 수익의 {inp.single_best_fold_pnl_share:.0%} 차지 — "
            f"기준 {MAX_SINGLE_FOLD_PNL_SHARE:.0%} 초과. '한 번의 대박' 의심."
        )


def _check_monte_carlo(inp: PromotionInput, r: PromotionResult,
                      *, hard_threshold: float, required: bool) -> None:
    """Monte Carlo (#26) gate."""
    if not inp.monte_carlo_run:
        if required:
            r.failed_criteria.append(
                "Monte Carlo 미실행 — POST /api/backtest/monte-carlo 실행 필요."
            )
        else:
            r.warnings.append(
                "Monte Carlo 미실행 — 다음 단계 승격 전에 권장."
            )
        return

    ror = inp.monte_carlo_risk_of_ruin
    if ror is None:
        r.failed_criteria.append("Monte Carlo risk_of_ruin 미산출.")
    elif ror > hard_threshold:
        r.failed_criteria.append(
            f"파산위험 {ror:.1%} > 한도 {hard_threshold:.0%} ({inp.target_stage.value} 단계)."
        )
    elif ror >= 0.01:
        r.cautions.append(
            f"파산위험 {ror:.1%} — 위험 구간. 사이즈 축소 또는 추가 검증 권장."
        )
    else:
        r.passed_criteria.append(f"파산위험 {ror:.1%} ≤ {hard_threshold:.0%}")

    worst5 = inp.monte_carlo_worst_5pct_mdd
    if worst5 is not None:
        limit = int(inp.initial_cash * MAX_WORST_5PCT_MDD_PCT)
        if worst5 > limit:
            r.failed_criteria.append(
                f"최악 5% MDD {worst5:,} > 한도 {limit:,} "
                f"(운영 자본의 {MAX_WORST_5PCT_MDD_PCT:.0%})."
            )

    if (inp.monte_carlo_longest_losing_streak is not None
            and inp.monte_carlo_longest_losing_streak >= 8):
        r.cautions.append(
            f"Monte Carlo 최장 연속손실 {inp.monte_carlo_longest_losing_streak}회 — "
            "Position size 축소 검토."
        )


def _check_data_quality(inp: PromotionInput, r: PromotionResult) -> None:
    """Data Quality (#21) gate."""
    grade = inp.data_quality_grade
    if grade == "EXCLUDE":
        r.failed_criteria.append(
            "데이터 품질 EXCLUDE — 백테스트 결과 신뢰 불가, 승격 불가."
        )
        return
    score = inp.data_quality_score
    if score is None:
        r.warnings.append(
            "데이터 품질 점수 미제공 — scripts/check_data_quality.py 실행 권장."
        )
        return
    if score < MIN_DATA_QUALITY_SCORE_HARD:
        r.failed_criteria.append(
            f"데이터 품질 점수 {score:.1f} < {MIN_DATA_QUALITY_SCORE_HARD} — 즉시 FAIL."
        )
    elif score < MIN_DATA_QUALITY_SCORE_BASIC:
        r.cautions.append(
            f"데이터 품질 점수 {score:.1f} < {MIN_DATA_QUALITY_SCORE_BASIC} — "
            "운영자 검토 필요."
        )
    else:
        r.passed_criteria.append(f"데이터 품질 점수 {score:.1f} ≥ {MIN_DATA_QUALITY_SCORE_BASIC}")


def _check_shadow_operational(inp: PromotionInput, r: PromotionResult) -> None:
    if inp.shadow_days < MIN_SHADOW_DAYS:
        r.failed_criteria.append(
            f"Shadow 운영 {inp.shadow_days}일 < {MIN_SHADOW_DAYS}일."
        )
    if inp.audit_log_missing_count > 0:
        r.failed_criteria.append(
            f"audit 로그 누락 {inp.audit_log_missing_count}회 > 0 — 승격 불가."
        )


def _check_paper_operational(inp: PromotionInput, r: PromotionResult) -> None:
    if inp.paper_days < MIN_PAPER_DAYS:
        r.failed_criteria.append(
            f"Paper 운영 {inp.paper_days}일 < {MIN_PAPER_DAYS}일."
        )
    if inp.daily_loss_limit_violations > 0:
        r.failed_criteria.append(
            f"일일 손실한도 위반 {inp.daily_loss_limit_violations}회 > 0 — 승격 불가."
        )
    if inp.risk_policy_violations > 0:
        r.failed_criteria.append(
            f"RiskPolicy 위반 {inp.risk_policy_violations}회 > 0 — 승격 불가."
        )
    if inp.audit_log_missing_count > 0:
        r.failed_criteria.append(
            f"audit 로그 누락 {inp.audit_log_missing_count}회 > 0 — 승격 불가."
        )
    if not inp.partial_fill_audit_ok:
        r.failed_criteria.append(
            "partial fill / rejected order audit 비정상 — 승격 불가."
        )


def _check_live_manual_operational(inp: PromotionInput, r: PromotionResult) -> None:
    if inp.live_manual_days < MIN_LIVE_MANUAL_DAYS:
        r.failed_criteria.append(
            f"LIVE_MANUAL_APPROVAL 운영 {inp.live_manual_days}일 < {MIN_LIVE_MANUAL_DAYS}일."
        )
    if inp.audit_log_missing_count > 0:
        r.failed_criteria.append(
            f"audit 로그 누락 {inp.audit_log_missing_count}회 — 승격 불가."
        )


def _check_ai_recommendation_accuracy(inp: PromotionInput, r: PromotionResult) -> None:
    if inp.ai_recommendation_accuracy is None:
        r.failed_criteria.append(
            "AI 추천 정확도 미보고 — 승격 검토에 필수."
        )
    elif inp.ai_recommendation_accuracy < 0.6:
        r.failed_criteria.append(
            f"AI 추천 정확도 {inp.ai_recommendation_accuracy:.0%} < 60%."
        )


def _check_human_approval(inp: PromotionInput, r: PromotionResult) -> None:
    """LIVE 단계 사람 승인 — _finalize에서 BLOCKED 분기로 처리되지만,
    AI 추천만으로는 승격 불가 invariant도 본 함수에서 명시.
    """
    if inp.ai_recommended and not inp.human_approved:
        r.warnings.append(
            "AI 추천(ai_recommended=true)만 있음 — 사람 승인 필요. "
            "AI 추천만으로는 승격 불가 (CLAUDE.md 절대 원칙)."
        )
        r.required_actions.append("운영자가 직접 검토 후 human_approved=true 명시.")
