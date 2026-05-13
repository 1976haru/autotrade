"""AI Assist Gate evaluator (#74).

LIVE_AI_ASSIST 모드의 AI 제안 품질을 검증하는 *read-only 분석 게이트*. AI
자동매매(`LIVE_AI_EXECUTION`)로 진입하기 전 필수 검증 단계.

CLAUDE.md 절대 원칙:
- 본 모듈은 *판단만* 한다 — broker / OrderExecutor / route_order /
  AI provider / 외부 HTTP 어떤 것도 호출 / import 0건.
- DB는 read-only (collector 측). 본 evaluator는 입력 DTO만 받음.
- 본 게이트의 PASS는 **`LIVE_AI_EXECUTION` 허가가 *아니다***. PASS는 AI
  Assist 품질이 다음 검증 단계로 갈 수 있다는 의미일 뿐. LIVE_AI_EXECUTION은
  `AIExecutionGate`(#45) + 추가 사용자 승인 + 별도 옵트인 PR 필요.
- 본 결과는 *투자 조언이 아니라* AI Assist 기능의 *시스템 검증 자료*.

평가 지표 (`docs/ai_assist_gate.md` 와 lockstep):
- proposal_count            : AI 제안 총 수 (audit row 기준)
- approved_proposals        : 운영자가 승인 후 broker로 진행된 수
- approved_expectancy       : 승인된 제안의 평균 손익 (운영자가 산출)
- approved_loss_rate        : 승인된 제안 중 손실 비율
- risk_rejection_rate       : RiskManager가 사전 거절한 비율
- operator_rejection_rate   : 운영자가 거절한 비율
- expired_or_cancelled_rate : TTL / 운영자 취소 비율
- confidence_calibration    : confidence 와 결과 일치도 (0~1)
- rejected_but_would_have_won: 거절했으나 사후 가격이 유리했던 케이스 (시스템 학습 신호)
- ai_decision_audit_drift   : audit row 누락 추정 수
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / thresholds ----------


class AIAssistGateVerdict(StrEnum):
    """4단계 판정. PASS여도 LIVE_AI_EXECUTION 자동 허가 *아님*."""
    PASS    = "PASS"
    CAUTION = "CAUTION"
    FAIL    = "FAIL"
    UNKNOWN = "UNKNOWN"


class AIAssistFailureReason(StrEnum):
    """AI 제안 실패 사유 태그 — collector / 운영자가 사후 분석에 사용.

    BUY / SELL / HOLD 등 *주문 신호*는 *없다* (advisory tag만).
    """
    LOW_CONFIDENCE              = "low_confidence"
    DATA_STALE                  = "data_stale"
    PRICE_GAP                   = "price_gap"
    LIQUIDITY                   = "liquidity"
    RISK_LIMIT                  = "risk_limit"
    OPERATOR_REJECTED           = "operator_rejected"
    APPROVAL_EXPIRED            = "approval_expired"
    EMERGENCY_STOP              = "emergency_stop"
    REGIME_MISMATCH             = "regime_mismatch"
    NEWS_OR_THEME_OVERHEATED    = "news_or_theme_overheated"
    DUPLICATE_OR_COOLDOWN       = "duplicate_or_cooldown"
    UNCATEGORIZED               = "uncategorized"


@dataclass(frozen=True)
class AIAssistGateThresholds:
    """평가 임계. promotion_policy / ai_permission_gate / ai_execution_policy
    와 lockstep — 모든 default 값은 *보수적*.
    """
    # 핵심 PASS 기준.
    min_proposal_count:            int   = 100
    min_approved_expectancy:       float = 0.0          # *양수*
    max_approved_loss_rate:        float = 0.55         # 승인된 제안의 손실 비율 상한
    max_risk_rejection_rate:       float = 0.6          # 너무 자주 거절되면 AI 신호 품질 낮음
    max_operator_rejection_rate:   float = 0.5
    max_expired_or_cancelled_rate: float = 0.3
    min_confidence_calibration:    float = 0.5

    # 시스템 안정성.
    max_ai_decision_audit_drift:   int   = 0
    max_emergency_stops_in_period: int   = 2

    # 운영 기간.
    min_active_days:               int   = 28

    # CAUTION 임계 (PASS 임계 통과해도 surface).
    caution_confidence_calibration:        float = 0.65
    caution_rejected_but_would_have_won:   float = 0.25  # 정상 거절 대비 비율
    caution_failure_reason_concentration:  float = 0.4    # 단일 reason이 40% 초과


# ---------- input DTO ----------


@dataclass(frozen=True)
class AIAssistGateInput:
    """AI Assist Gate 평가 입력.

    수치는 collector(`ai_assist_gate_collector.py`)가 OrderAuditLog +
    AgentDecisionLog + PendingApproval 등을 read-only로 산출하거나, 운영자가
    수동으로 채운다. 본 evaluator는 입력 DTO만 사용한다.
    """
    strategy_name:                 str
    period_start:                  datetime
    period_end:                    datetime

    # 표본 / 흐름.
    proposal_count:                int   = 0
    approved_proposals:            int   = 0
    risk_rejected_proposals:       int   = 0
    operator_rejected_proposals:   int   = 0
    expired_or_cancelled:          int   = 0

    # 결과 품질 (운영자가 별도 trade ledger 또는 수동 산출).
    approved_expectancy:           float = 0.0
    approved_winning_pnl_sum:      int   = 0
    approved_losing_pnl_sum:       int   = 0
    approved_loss_count:           int   = 0
    approved_win_count:            int   = 0

    # confidence 분석.
    confidence_calibration:        float = 0.0    # 0~1
    avg_confidence:                float | None = None
    rejected_but_would_have_won:   int   = 0      # 거절했으나 사후 유리했던 케이스

    # 시스템 안정성.
    ai_decision_audit_drift:       int   = 0
    emergency_stops_in_period:     int   = 0
    active_days:                   int   = 0

    # failure reason 분포 (tag → count).
    failure_reason_counts:         dict[str, int] = field(default_factory=dict)

    @property
    def total_decided(self) -> int:
        return (
            self.approved_proposals
            + self.risk_rejected_proposals
            + self.operator_rejected_proposals
            + self.expired_or_cancelled
        )

    @property
    def period_days(self) -> int:
        return max(0, (self.period_end - self.period_start).days)

    @property
    def approved_loss_rate(self) -> float | None:
        total = self.approved_win_count + self.approved_loss_count
        if total <= 0:
            return None
        return self.approved_loss_count / total


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


# ---------- result DTO ----------


@dataclass
class AIAssistGateResult:
    """평가 결과.

    invariants (코드 단 강제):
    - `is_live_authorization=False` 항상 — PASS는 `LIVE_AI_EXECUTION` 허가가
      아니라 *다음 검증 단계 진입 가능*을 의미.
    - `is_order_signal=False` 항상 — Gate는 BUY/SELL/HOLD 신호를 만들지 않음.
    - `is_investment_advice=False` 항상 — 본 리포트는 *시스템 검증 자료*.
    """
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 AIAssistGateVerdict
    passed_criteria:         list[str] = field(default_factory=list)
    failed_criteria:         list[str] = field(default_factory=list)
    cautions:                list[str] = field(default_factory=list)
    failure_reason_tags:     dict[str, int] = field(default_factory=dict)
    metrics:                 dict[str, Any] = field(default_factory=dict)
    thresholds:              dict[str, Any] = field(default_factory=dict)
    next_step:               str = ""
    is_live_authorization:   bool = False
    is_order_signal:         bool = False
    is_investment_advice:    bool = False
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError(
                "AIAssistGateResult.is_live_authorization must be False. "
                "PASS verdict means 'AI Assist quality is eligible for "
                "further verification', NOT 'authorize LIVE_AI_EXECUTION'."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "AIAssistGateResult.is_order_signal must be False — "
                "AI Assist Gate does not produce BUY/SELL/HOLD signals."
            )
        if self.is_investment_advice is not False:
            raise ValueError(
                "AIAssistGateResult.is_investment_advice must be False — "
                "AI Assist Gate output is system verification material, "
                "not investment advice."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":         self.strategy_name,
            "period_start":          self.period_start.isoformat(),
            "period_end":            self.period_end.isoformat(),
            "verdict":               self.verdict.value,
            "passed_criteria":       list(self.passed_criteria),
            "failed_criteria":       list(self.failed_criteria),
            "cautions":              list(self.cautions),
            "failure_reason_tags":   dict(self.failure_reason_tags),
            "metrics":               dict(self.metrics),
            "thresholds":            dict(self.thresholds),
            "next_step":             self.next_step,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
            "is_investment_advice":  self.is_investment_advice,
            "live_flag_changed":     False,
            "mode_changed":          False,
            "generated_at":          self.generated_at.isoformat(),
        }


# ---------- evaluator ----------


def evaluate_ai_assist_gate(
    inp: AIAssistGateInput,
    thresholds: AIAssistGateThresholds | None = None,
) -> AIAssistGateResult:
    """AI Assist Gate 평가. 외부 시스템 영향 0건.

    PASS 라벨은 *AI Assist 품질이 다음 검증 단계 진입 가능* 을 의미하며 실거래
    자동 허가가 아니다. AI 자동매매 활성화에는 `AIExecutionGate`(#45) + 별도
    옵트인 PR + 사용자 명시 승인 필요.
    """
    th = thresholds or AIAssistGateThresholds()
    passed: list[str] = []
    failed: list[str] = []
    cautions: list[str] = []

    period_days  = inp.period_days
    total_decided = inp.total_decided

    risk_rej_rate    = _rate(inp.risk_rejected_proposals,    inp.proposal_count)
    op_rej_rate      = _rate(inp.operator_rejected_proposals, inp.proposal_count)
    expired_rate     = _rate(inp.expired_or_cancelled,        inp.proposal_count)
    loss_rate        = inp.approved_loss_rate
    audit_drift      = inp.ai_decision_audit_drift

    # --- 1) 표본 ---
    if inp.proposal_count >= th.min_proposal_count:
        passed.append(
            f"AI 제안 {inp.proposal_count}건 ≥ {th.min_proposal_count}건."
        )
    else:
        failed.append(
            f"AI 제안 {inp.proposal_count}건 < {th.min_proposal_count}건 — 표본 부족."
        )

    # --- 2) 운영 기간 ---
    if period_days >= th.min_active_days:
        passed.append(f"운영 기간 {period_days}일 ≥ {th.min_active_days}일.")
    else:
        failed.append(
            f"운영 기간 {period_days}일 < {th.min_active_days}일 — 기간 부족."
        )

    # --- 3) 기대값 (승인된 제안의 평균 손익) ---
    if inp.approved_expectancy > th.min_approved_expectancy:
        passed.append(
            f"승인 제안 expectancy {inp.approved_expectancy:.2f} > 0."
        )
    else:
        failed.append(
            f"승인 제안 expectancy {inp.approved_expectancy:.2f} ≤ 0 — "
            "AI 제안이 수익 기여 못 함."
        )

    # --- 4) 승인 제안 손실 비율 ---
    if loss_rate is None:
        cautions.append("승인 제안 표본 부족 — loss rate 계산 불가.")
    elif loss_rate > th.max_approved_loss_rate:
        failed.append(
            f"승인 제안 손실율 {loss_rate:.1%} > {th.max_approved_loss_rate:.1%} — "
            "AI 신호 품질 의심."
        )
    else:
        passed.append(
            f"승인 제안 손실율 {loss_rate:.1%} ≤ {th.max_approved_loss_rate:.1%}."
        )

    # --- 5) Risk rejection 비율 ---
    if risk_rej_rate > th.max_risk_rejection_rate:
        failed.append(
            f"RiskManager 거절율 {risk_rej_rate:.1%} > {th.max_risk_rejection_rate:.1%} — "
            "AI 신호가 리스크 한도를 자주 위반."
        )
    else:
        passed.append(
            f"RiskManager 거절율 {risk_rej_rate:.1%} ≤ {th.max_risk_rejection_rate:.1%}."
        )

    # --- 6) Operator rejection 비율 ---
    if op_rej_rate > th.max_operator_rejection_rate:
        failed.append(
            f"운영자 거절율 {op_rej_rate:.1%} > {th.max_operator_rejection_rate:.1%} — "
            "AI 제안이 운영자 기준과 자주 충돌."
        )
    else:
        passed.append(
            f"운영자 거절율 {op_rej_rate:.1%} ≤ {th.max_operator_rejection_rate:.1%}."
        )

    # --- 7) Expired/cancelled 비율 ---
    if expired_rate > th.max_expired_or_cancelled_rate:
        cautions.append(
            f"승인 만료/취소 비율 {expired_rate:.1%} > "
            f"{th.max_expired_or_cancelled_rate:.1%} — TTL 또는 결재 지연 점검."
        )

    # --- 8) Confidence calibration ---
    if inp.confidence_calibration < th.min_confidence_calibration:
        failed.append(
            f"confidence calibration {inp.confidence_calibration:.2f} < "
            f"{th.min_confidence_calibration:.2f} — AI 자신도가 결과와 불일치."
        )
    elif inp.confidence_calibration < th.caution_confidence_calibration:
        cautions.append(
            f"confidence calibration {inp.confidence_calibration:.2f} 가 "
            f"{th.caution_confidence_calibration:.2f} 미만 — 모니터링 권장."
        )
        passed.append(
            f"confidence calibration {inp.confidence_calibration:.2f} ≥ "
            f"{th.min_confidence_calibration:.2f}."
        )
    else:
        passed.append(
            f"confidence calibration {inp.confidence_calibration:.2f} ≥ "
            f"{th.caution_confidence_calibration:.2f}."
        )

    # --- 9) audit drift ---
    if audit_drift > th.max_ai_decision_audit_drift:
        failed.append(
            f"AI 결정 audit 누락 {audit_drift}건 > {th.max_ai_decision_audit_drift} — "
            "감사 흐름 깨짐."
        )
    else:
        passed.append(f"AI 결정 audit 누락 {audit_drift}건.")

    # --- 10) 위험 이벤트 ---
    if inp.emergency_stops_in_period > th.max_emergency_stops_in_period:
        failed.append(
            f"긴급정지 {inp.emergency_stops_in_period}회 > "
            f"{th.max_emergency_stops_in_period} — 운영 안정성 부족."
        )
    else:
        passed.append(
            f"긴급정지 {inp.emergency_stops_in_period}회 ≤ "
            f"{th.max_emergency_stops_in_period}."
        )

    # --- 11) rejected_but_would_have_won (CAUTION) ---
    if inp.risk_rejected_proposals + inp.operator_rejected_proposals > 0:
        ratio = _rate(
            inp.rejected_but_would_have_won,
            inp.risk_rejected_proposals + inp.operator_rejected_proposals,
        )
        if ratio > th.caution_rejected_but_would_have_won:
            cautions.append(
                f"거절했으나 사후 유리했던 비율 {ratio:.1%} > "
                f"{th.caution_rejected_but_would_have_won:.1%} — RiskPolicy / "
                "운영자 기준이 너무 보수적일 가능성."
            )

    # --- 12) failure reason concentration (CAUTION) ---
    failure_total = sum(inp.failure_reason_counts.values())
    top_reason: str | None = None
    top_share = 0.0
    if failure_total > 0:
        top_reason, top_count = max(
            inp.failure_reason_counts.items(), key=lambda kv: kv[1],
        )
        top_share = top_count / failure_total
        if top_share > th.caution_failure_reason_concentration:
            cautions.append(
                f"실패 사유 집중 — `{top_reason}` 이 전체 실패 사유의 "
                f"{top_share:.1%} 차지 (> {th.caution_failure_reason_concentration:.1%})."
            )

    # --- verdict ---
    if not passed and not failed:
        verdict = AIAssistGateVerdict.UNKNOWN
    elif failed:
        verdict = AIAssistGateVerdict.FAIL
    elif cautions:
        verdict = AIAssistGateVerdict.CAUTION
    else:
        verdict = AIAssistGateVerdict.PASS

    return AIAssistGateResult(
        strategy_name=inp.strategy_name,
        period_start=inp.period_start,
        period_end=inp.period_end,
        verdict=verdict,
        passed_criteria=passed,
        failed_criteria=failed,
        cautions=cautions,
        failure_reason_tags=dict(inp.failure_reason_counts),
        metrics={
            "period_days":                period_days,
            "proposal_count":             inp.proposal_count,
            "approved_proposals":         inp.approved_proposals,
            "risk_rejected_proposals":    inp.risk_rejected_proposals,
            "operator_rejected_proposals": inp.operator_rejected_proposals,
            "expired_or_cancelled":       inp.expired_or_cancelled,
            "total_decided":              total_decided,
            "approved_expectancy":        round(inp.approved_expectancy, 4),
            "approved_winning_pnl_sum":   inp.approved_winning_pnl_sum,
            "approved_losing_pnl_sum":    inp.approved_losing_pnl_sum,
            "approved_win_count":         inp.approved_win_count,
            "approved_loss_count":        inp.approved_loss_count,
            "approved_loss_rate":         (
                None if loss_rate is None else round(loss_rate, 4)
            ),
            "risk_rejection_rate":        round(risk_rej_rate, 4),
            "operator_rejection_rate":    round(op_rej_rate, 4),
            "expired_or_cancelled_rate":  round(expired_rate, 4),
            "confidence_calibration":     round(inp.confidence_calibration, 4),
            "avg_confidence":             inp.avg_confidence,
            "rejected_but_would_have_won": inp.rejected_but_would_have_won,
            "ai_decision_audit_drift":    audit_drift,
            "emergency_stops_in_period":  inp.emergency_stops_in_period,
            "active_days":                inp.active_days,
            "top_failure_reason":         top_reason,
            "top_failure_share":          round(top_share, 4),
        },
        thresholds={
            "min_proposal_count":            th.min_proposal_count,
            "min_approved_expectancy":       th.min_approved_expectancy,
            "max_approved_loss_rate":        th.max_approved_loss_rate,
            "max_risk_rejection_rate":       th.max_risk_rejection_rate,
            "max_operator_rejection_rate":   th.max_operator_rejection_rate,
            "max_expired_or_cancelled_rate": th.max_expired_or_cancelled_rate,
            "min_confidence_calibration":    th.min_confidence_calibration,
            "max_ai_decision_audit_drift":   th.max_ai_decision_audit_drift,
            "max_emergency_stops_in_period": th.max_emergency_stops_in_period,
            "min_active_days":               th.min_active_days,
        },
        next_step=_next_step_for_verdict(verdict),
    )


def _next_step_for_verdict(v: AIAssistGateVerdict) -> str:
    if v == AIAssistGateVerdict.PASS:
        return (
            "AI Assist 품질 OK — *다음 검증 단계* 진입 검토 가능. "
            "LIVE_AI_EXECUTION 활성화는 별도 옵트인 PR + AIExecutionGate(#45) "
            "+ 사용자 명시 승인 모두 필요. **본 PASS는 실거래 자동 허가가 아니다.**"
        )
    if v == AIAssistGateVerdict.CAUTION:
        return (
            "PASS 임계 충족이지만 CAUTION 사유 검토 필요. 추가 운용 또는 "
            "운영자 점검 후 재평가."
        )
    if v == AIAssistGateVerdict.FAIL:
        return (
            "AI Assist 품질 미달. 표본 / 기대값 / 신호 효율 / 시스템 안정성 "
            "지표 보완 후 재평가. LIVE_AI_EXECUTION 진입 금지."
        )
    return "데이터 부족 — 입력 확보 후 재평가. 보수적으로 FAIL 취급 권장."


# ---------- markdown report ----------


def render_markdown_report(result: AIAssistGateResult) -> str:
    """AI Assist Gate 결과 → markdown.

    상단 고지 강제: 본 리포트는 *투자 조언이 아니라 AI Assist 기능의
    시스템 검증 자료*. PASS != LIVE_AI_EXECUTION 허가. BUY/SELL/HOLD 문구 0건.
    """
    lines: list[str] = []
    lines.append(f"# AI Assist Gate Report — {result.strategy_name}")
    lines.append("")
    lines.append(
        f"_생성: {result.generated_at.isoformat()} · "
        f"기간: {result.period_start.date()} ~ {result.period_end.date()}_"
    )
    lines.append("")
    lines.append("## 중요 고지")
    lines.append("")
    lines.append(
        "> 이 리포트는 **투자 조언이 아니라** AI Assist 기능의 *시스템 검증 "
        "자료*입니다. AI 자동매매 또는 실거래 허가가 아닙니다."
    )
    lines.append("> ")
    lines.append(
        "> PASS는 AI Assist 품질이 *다음 검증 단계로 진행 가능*함을 의미할 "
        "뿐이며, `LIVE_AI_EXECUTION` 활성화는 `AIExecutionGate`(#45) + "
        "별도 옵트인 PR + 사용자 명시 승인 모두 필요합니다."
    )
    lines.append("")
    lines.append("## 1. 결론")
    lines.append("")
    lines.append(f"- **Verdict: `{result.verdict.value}`**")
    lines.append(f"- 다음 단계: {result.next_step}")
    lines.append("")
    if result.failed_criteria:
        lines.append("## 2. 미충족 기준 (FAIL 사유)")
        for c in result.failed_criteria:
            lines.append(f"- ❌ {c}")
        lines.append("")
    if result.cautions:
        lines.append("## 3. CAUTION 항목")
        for c in result.cautions:
            lines.append(f"- ⚠️ {c}")
        lines.append("")
    if result.passed_criteria:
        lines.append("## 4. 충족 기준")
        for c in result.passed_criteria:
            lines.append(f"- ✅ {c}")
        lines.append("")
    lines.append("## 5. 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    for k, v in result.metrics.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    if result.failure_reason_tags:
        lines.append("## 6. Failure Reason 분포")
        lines.append("")
        lines.append("| 태그 | 건수 |")
        lines.append("|---|---|")
        for k, v in sorted(
            result.failure_reason_tags.items(), key=lambda kv: -kv[1],
        ):
            lines.append(f"| `{k}` | {v} |")
        lines.append("")
    lines.append("## 7. 임계 (AIAssistGateThresholds)")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    for k, v in result.thresholds.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "본 리포트는 *판단 보조 자료*입니다. RiskManager / PermissionGate / "
        "OrderExecutor 우회 금지. 본 게이트 평가로 어떤 LIVE 플래그 / 안전 "
        "플래그도 변경되지 않습니다."
    )
    return "\n".join(lines)
