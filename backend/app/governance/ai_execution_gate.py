"""AI Execution Activation Gate (#75).

`LIVE_AI_EXECUTION` 모드 활성화의 *최종* readiness 평가 게이트.

본 게이트는 **실제 활성화를 수행하지 않는다**. promotion_policy, PaperGate(#72),
AI Assist Gate(#74), Manual Approval(#41/#73), Shadow/Paper 기간, OrderGuard(#38),
RiskManager(#34), AI Permission Gate(#39), AuditLog, KillSwitch(#37) 등 모든
전제 조건을 *강제로 확인* 후 READY_FOR_REVIEW / CAUTION / BLOCKED 판정만 반환.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order / AI provider / 외부 HTTP import 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  / `KIS_IS_PAPER` 변경 0건 — 본 모듈은 settings 를 *읽지도 않는다*.
- DB write 0건 (정적 grep 가드).
- 본 게이트의 PASS(READY_FOR_REVIEW)는 **실제 활성화가 아니다**. 활성화는
  별도 옵트인 PR + 사용자 명시 승인 + `.env` flag 변경 + 초소액 canary +
  즉시 kill switch 가능 모두 필요.

invariant (코드 단 강제):
- `AIExecutionActivationGateResult.is_live_authorization=False` 항상.
- `is_order_signal=False` 항상.
- `is_investment_advice=False` 항상.
- `futures_allowed=False` 항상 — 선물 AI 자동 실행은 *영구* 차단 (별도 9단계
  blocker 통과 + 별도 게이트). dataclass `__post_init__`이 True 시 ValueError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / thresholds ----------


class AIExecutionGateVerdict(StrEnum):
    """4단계 판정. READY_FOR_REVIEW도 실제 활성화 *아님*."""
    READY_FOR_REVIEW = "READY_FOR_REVIEW"   # 활성화 검토 가능 (실제 활성화 아님)
    CAUTION          = "CAUTION"
    BLOCKED          = "BLOCKED"
    UNKNOWN          = "UNKNOWN"


@dataclass(frozen=True)
class AIExecutionGateThresholds:
    """평가 임계. 보수적 default — 운영자가 *더 엄격*하게 override 가능.

    완전 자동화 위험을 낮추기 위해 모든 한도가 *극소액*으로 시작한다.
    """
    # 극소액 주문 정책.
    max_order_notional_krw:        int   = 30_000      # 1회 ≤ 3만 원 (Live Manual의 60%)
    max_daily_loss_krw:            int   = 5_000       # 일일 손실 ≤ 5천 원
    max_daily_order_count:         int   = 10          # 하루 ≤ 10건
    max_open_positions:            int   = 2           # 동시 보유 ≤ 2개
    min_symbol_whitelist_size:     int   = 1           # 최소 1종목 명시
    max_symbol_whitelist_size:     int   = 5           # 최대 5종목 (분산 제한)

    # 시간 정책 (KST 기준).
    allowed_window_start_kst:      time  = time(9, 30)   # 09:30 시가 후
    allowed_window_end_kst:        time  = time(14, 30)  # 마감 30분 전 종료
    require_explicit_time_window:  bool  = True

    # 자동 중단 / KillSwitch.
    require_kill_switch_ready:     bool  = True
    require_circuit_breaker:       bool  = True

    # AI 신호 품질.
    min_ai_confidence_threshold:   int   = 75            # 0~100, 통과 임계
    min_signal_quality_threshold:  int   = 70

    # 전제 게이트.
    require_promotion_gate_pass:   bool  = True
    require_paper_gate_pass:       bool  = True
    require_ai_assist_gate_pass:   bool  = True
    require_live_manual_gate_pass: bool  = True
    require_user_explicit_opt_in:  bool  = True

    # 운영 기간.
    min_live_manual_days:          int   = 28
    min_ai_assist_days:            int   = 28

    # 시스템 안정성.
    max_system_errors:             int   = 0
    max_audit_missing:             int   = 0
    max_approval_bypass_attempts:  int   = 0

    # AI Permission Gate / OrderGuard / RiskManager 모두 운영 중이어야 함.
    require_risk_manager_active:        bool = True
    require_order_guard_active:         bool = True
    require_ai_permission_gate_active:  bool = True
    require_audit_log_complete:         bool = True


# ---------- input DTO ----------


@dataclass(frozen=True)
class AIExecutionGateInput:
    """AI Execution Activation Gate 평가 입력.

    *모든 안전 플래그는 입력으로만 받는다* — 본 게이트는 어떤 값도 mutate
    하지 않는다. 운영자가 *현재값*을 명시 (collector 또는 수동).
    """
    strategy_name:                 str
    evaluated_at:                  datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # 전제 게이트 결과.
    promotion_gate_passed:         bool = False
    paper_gate_passed:             bool = False
    ai_assist_gate_passed:         bool = False
    live_manual_gate_passed:       bool = False

    # 운영자 / 안전 플래그 현재값.
    user_explicit_opt_in:          bool = False
    enable_live_trading:           bool = False
    enable_ai_execution:           bool = False        # 본 게이트 평가 시점에는 false 권장
    enable_futures_live_trading:   bool = False

    # 운영 기간.
    live_manual_days:              int  = 0
    ai_assist_days:                int  = 0

    # 안전 인프라 활성 여부.
    risk_manager_active:           bool = False
    order_guard_active:            bool = False
    ai_permission_gate_active:     bool = False
    audit_log_complete:            bool = False
    kill_switch_ready:             bool = False
    circuit_breaker_configured:    bool = False

    # 극소액 정책 현재 적용.
    current_max_order_notional_krw: int = 0
    current_max_daily_loss_krw:     int = 0
    current_max_daily_order_count:  int = 0
    current_max_open_positions:     int = 0
    allowed_symbols:                tuple[str, ...] = field(default_factory=tuple)

    # 시간 정책 현재값.
    explicit_time_window_set:       bool = False
    window_start_kst:               time | None = None
    window_end_kst:                 time | None = None

    # AI 신호 품질 임계 (운영자 설정).
    ai_confidence_threshold:        int  = 0
    signal_quality_threshold:       int  = 0

    # 시스템 안정성.
    system_errors:                  int  = 0
    audit_missing_count:            int  = 0
    approval_bypass_attempts:       int  = 0

    # 선물 입력 — 운영자가 강제로 false 임을 명시. True 입력은 BLOCKED.
    futures_target:                 bool = False


# ---------- result DTO ----------


@dataclass
class AIExecutionActivationGateResult:
    """평가 결과.

    invariants (코드 단 강제):
    - `is_live_authorization=False` 항상.
    - `is_order_signal=False` 항상.
    - `is_investment_advice=False` 항상.
    - `futures_allowed=False` 항상 — 선물 AI 자동 실행은 본 게이트로 *영구*
      허용되지 않는다 (별도 9단계 blocker + 별도 PR).
    """
    strategy_name:           str
    evaluated_at:            datetime
    verdict:                 AIExecutionGateVerdict
    passed_criteria:         list[str] = field(default_factory=list)
    blocked_criteria:        list[str] = field(default_factory=list)
    cautions:                list[str] = field(default_factory=list)
    required_actions:        list[str] = field(default_factory=list)
    metrics:                 dict[str, Any] = field(default_factory=dict)
    thresholds:              dict[str, Any] = field(default_factory=dict)
    next_step:               str = ""
    is_live_authorization:   bool = False
    is_order_signal:         bool = False
    is_investment_advice:    bool = False
    futures_allowed:         bool = False     # 절대 invariant — True 생성 불가
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError(
                "AIExecutionActivationGateResult.is_live_authorization must be False. "
                "READY_FOR_REVIEW verdict means 'activation review eligible' "
                "with separate opt-in PR required, NOT 'authorize live trading'."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "AIExecutionActivationGateResult.is_order_signal must be False — "
                "Gate does not produce BUY/SELL/HOLD signals."
            )
        if self.is_investment_advice is not False:
            raise ValueError(
                "AIExecutionActivationGateResult.is_investment_advice must be False — "
                "Gate output is system verification material, not investment advice."
            )
        if self.futures_allowed is not False:
            raise ValueError(
                "AIExecutionActivationGateResult.futures_allowed must be False. "
                "Futures AI execution is permanently NOT allowed by this gate; "
                "futures requires the 9-step blocker checklist in "
                "live_activation_blockers.md §3.1 and a separate gate."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":         self.strategy_name,
            "evaluated_at":          self.evaluated_at.isoformat(),
            "verdict":               self.verdict.value,
            "passed_criteria":       list(self.passed_criteria),
            "blocked_criteria":      list(self.blocked_criteria),
            "cautions":              list(self.cautions),
            "required_actions":      list(self.required_actions),
            "metrics":               dict(self.metrics),
            "thresholds":            dict(self.thresholds),
            "next_step":             self.next_step,
            "is_live_authorization": self.is_live_authorization,
            "is_order_signal":       self.is_order_signal,
            "is_investment_advice":  self.is_investment_advice,
            "futures_allowed":       self.futures_allowed,
            "live_flag_changed":     False,
            "mode_changed":          False,
            "generated_at":          self.generated_at.isoformat(),
        }


# ---------- evaluator ----------


def evaluate_ai_execution_gate(
    inp: AIExecutionGateInput,
    thresholds: AIExecutionGateThresholds | None = None,
) -> AIExecutionActivationGateResult:
    """AI Execution Activation Gate 평가. 외부 시스템 영향 0건.

    **READY_FOR_REVIEW 판정은 실제 활성화가 아니다.** 활성화는 별도 옵트인 PR +
    사용자 명시 승인 + `.env` flag 변경 + 초소액 canary + 즉시 kill switch
    가능 모두 필요.
    """
    th = thresholds or AIExecutionGateThresholds()
    passed: list[str] = []
    blocked: list[str] = []
    cautions: list[str] = []
    actions: list[str] = []

    # --- 1) 전제 게이트 ---
    if th.require_promotion_gate_pass:
        if inp.promotion_gate_passed:
            passed.append("Promotion Gate(#27) PASS.")
        else:
            blocked.append(
                "Promotion Gate(#27) 미통과 — `LIVE_AI_EXECUTION` target 평가 필요."
            )
            actions.append("`/api/governance/strategy-promotion/evaluate` 통과.")

    if th.require_paper_gate_pass:
        if inp.paper_gate_passed:
            passed.append("Paper Gate(#72) PASS.")
        else:
            blocked.append("Paper Gate(#72) 미통과.")
            actions.append("`scripts/evaluate_paper_gate.py` 통과.")

    if th.require_ai_assist_gate_pass:
        if inp.ai_assist_gate_passed:
            passed.append("AI Assist Gate(#74) PASS.")
        else:
            blocked.append("AI Assist Gate(#74) 미통과 — AI 제안 품질 검증 필요.")
            actions.append("`scripts/evaluate_ai_assist_gate.py` 통과.")

    if th.require_live_manual_gate_pass:
        if inp.live_manual_gate_passed:
            passed.append("Live Manual Gate(#73) PASS.")
        else:
            blocked.append("Live Manual Gate(#73) 미통과.")
            actions.append("`POST /api/governance/live-manual-gate/evaluate` 통과.")

    # --- 2) 운영자 명시 opt-in ---
    if th.require_user_explicit_opt_in:
        if inp.user_explicit_opt_in:
            passed.append("운영자 명시 opt-in 확인.")
        else:
            blocked.append(
                "운영자 명시 opt-in 필요 — 자동 활성화 절대 금지. "
                "사용자가 별도 PR / 운영 노트로 명시 후 재평가."
            )

    # --- 3) 안전 플래그 상태 ---
    # 본 게이트 평가 *시점*에는 ENABLE_AI_EXECUTION=false 권장 (활성화 *전*).
    if inp.enable_ai_execution:
        cautions.append(
            "ENABLE_AI_EXECUTION=true 가 이미 활성. 별도 옵트인 PR 통과 여부 "
            "확인 권장 — 본 게이트는 readiness 평가만 수행."
        )
    else:
        passed.append("ENABLE_AI_EXECUTION=false — 활성화 전 정상 상태.")

    if inp.enable_futures_live_trading:
        blocked.append(
            "ENABLE_FUTURES_LIVE_TRADING=true — 선물 AI 자동 실행은 본 게이트로 "
            "절대 허용되지 않는다. `live_activation_blockers.md` §3.1 9단계 + "
            "별도 게이트 필요."
        )
        actions.append("`ENABLE_FUTURES_LIVE_TRADING=false` 로 되돌린 후 재평가.")
    else:
        passed.append("ENABLE_FUTURES_LIVE_TRADING=false — 선물 LIVE 차단 유지.")

    if inp.futures_target:
        blocked.append(
            "futures_target=True — 선물 AI Execution은 본 게이트가 *영구* 허용하지 "
            "않는다. 선물은 별도 PR + 9단계 blocker 통과 필요."
        )

    # --- 4) 운영 기간 ---
    if inp.live_manual_days >= th.min_live_manual_days:
        passed.append(
            f"Live Manual 운영 {inp.live_manual_days}일 ≥ {th.min_live_manual_days}일."
        )
    else:
        blocked.append(
            f"Live Manual 운영 {inp.live_manual_days}일 < {th.min_live_manual_days}일 — "
            "추가 수동승인 운영 필요."
        )

    if inp.ai_assist_days >= th.min_ai_assist_days:
        passed.append(
            f"AI Assist 운영 {inp.ai_assist_days}일 ≥ {th.min_ai_assist_days}일."
        )
    else:
        blocked.append(
            f"AI Assist 운영 {inp.ai_assist_days}일 < {th.min_ai_assist_days}일."
        )

    # --- 5) 안전 인프라 ---
    if th.require_risk_manager_active and not inp.risk_manager_active:
        blocked.append("RiskManager 비활성. AI 주문 위험 평가 불가.")
    elif th.require_risk_manager_active:
        passed.append("RiskManager 활성.")

    if th.require_order_guard_active and not inp.order_guard_active:
        blocked.append("OrderGuard 비활성. 중복/쿨타임 보호 부재.")
    elif th.require_order_guard_active:
        passed.append("OrderGuard 활성.")

    if th.require_ai_permission_gate_active and not inp.ai_permission_gate_active:
        blocked.append("AI Permission Gate(#39) 비활성. AI 권한 매트릭스 검사 부재.")
    elif th.require_ai_permission_gate_active:
        passed.append("AI Permission Gate 활성.")

    if th.require_audit_log_complete and not inp.audit_log_complete:
        blocked.append("AuditLog 누락. 감사 흐름 회복 필요.")
    elif th.require_audit_log_complete:
        passed.append("AuditLog 완전 (누락 0건).")

    if th.require_kill_switch_ready and not inp.kill_switch_ready:
        blocked.append(
            "KillSwitch(#37) 미준비. 즉시 중단 경로 검증 필요."
        )
        actions.append("3-Level KillSwitch 토글 drill — KST 시장 시간 외 ON/OFF 1회씩.")
    elif th.require_kill_switch_ready:
        passed.append("KillSwitch(#37) 준비됨.")

    if th.require_circuit_breaker and not inp.circuit_breaker_configured:
        blocked.append(
            "Circuit Breaker 미설정. 비정상 손실 / API 오류 시 자동 중단 경로 부재."
        )
    elif th.require_circuit_breaker:
        passed.append("Circuit Breaker 설정됨.")

    # --- 6) 극소액 정책 ---
    if inp.current_max_order_notional_krw <= 0:
        blocked.append("1회 주문 한도 미설정 — `RiskPolicy.max_order_notional` 필요.")
    elif inp.current_max_order_notional_krw > th.max_order_notional_krw:
        blocked.append(
            f"1회 주문 한도 {inp.current_max_order_notional_krw:,}원 > "
            f"권장 {th.max_order_notional_krw:,}원 — 극소액 정책 위반."
        )
        actions.append(
            f"RiskPolicy.max_order_notional ≤ {th.max_order_notional_krw:,} 으로 조정."
        )
    else:
        passed.append(
            f"1회 주문 한도 {inp.current_max_order_notional_krw:,}원 ≤ "
            f"{th.max_order_notional_krw:,}원."
        )

    if inp.current_max_daily_loss_krw <= 0:
        blocked.append("일일 손실한도 미설정.")
    elif inp.current_max_daily_loss_krw > th.max_daily_loss_krw:
        blocked.append(
            f"일일 손실한도 {inp.current_max_daily_loss_krw:,}원 > "
            f"권장 {th.max_daily_loss_krw:,}원."
        )
    else:
        passed.append(
            f"일일 손실한도 {inp.current_max_daily_loss_krw:,}원 ≤ "
            f"{th.max_daily_loss_krw:,}원."
        )

    if inp.current_max_daily_order_count <= 0:
        blocked.append("일일 주문 수 한도 미설정.")
    elif inp.current_max_daily_order_count > th.max_daily_order_count:
        blocked.append(
            f"일일 주문 수 {inp.current_max_daily_order_count}건 > "
            f"권장 {th.max_daily_order_count}건 — 거래 빈도 너무 높음."
        )
    else:
        passed.append(
            f"일일 주문 수 {inp.current_max_daily_order_count}건 ≤ "
            f"{th.max_daily_order_count}건."
        )

    if inp.current_max_open_positions <= 0:
        blocked.append("동시 보유 종목 한도 미설정.")
    elif inp.current_max_open_positions > th.max_open_positions:
        blocked.append(
            f"동시 보유 {inp.current_max_open_positions}개 > "
            f"{th.max_open_positions}개 — 분산 너무 넓음."
        )
    else:
        passed.append(
            f"동시 보유 {inp.current_max_open_positions}개 ≤ {th.max_open_positions}개."
        )

    # --- 7) 종목 whitelist ---
    n = len(inp.allowed_symbols)
    if n < th.min_symbol_whitelist_size:
        blocked.append(
            f"허용 종목 {n}개 < {th.min_symbol_whitelist_size}개 — whitelist 필요."
        )
        actions.append("watchlist + RiskPolicy로 종목 화이트리스트 명시.")
    elif n > th.max_symbol_whitelist_size:
        blocked.append(
            f"허용 종목 {n}개 > {th.max_symbol_whitelist_size}개 — 분산 제한."
        )
    else:
        passed.append(f"허용 종목 {n}개 — whitelist 정상.")

    # --- 8) 시간 정책 ---
    if th.require_explicit_time_window:
        if not inp.explicit_time_window_set:
            blocked.append(
                "시간 정책 명시 필요 — KST 거래 윈도우(예: 09:30~14:30) 설정."
            )
            actions.append(
                f"운영 시간을 KST {th.allowed_window_start_kst.isoformat()}~"
                f"{th.allowed_window_end_kst.isoformat()} 범위 안으로 명시."
            )
        else:
            passed.append("거래 시간 윈도우 명시됨.")
            if inp.window_start_kst is not None and inp.window_end_kst is not None:
                if inp.window_start_kst < th.allowed_window_start_kst:
                    cautions.append(
                        f"시작 시간 {inp.window_start_kst.isoformat()} < 권장 "
                        f"{th.allowed_window_start_kst.isoformat()} (시가 직후 변동성)."
                    )
                if inp.window_end_kst > th.allowed_window_end_kst:
                    cautions.append(
                        f"종료 시간 {inp.window_end_kst.isoformat()} > 권장 "
                        f"{th.allowed_window_end_kst.isoformat()} (마감 동시호가 위험)."
                    )

    # --- 9) AI 신호 품질 ---
    if inp.ai_confidence_threshold < th.min_ai_confidence_threshold:
        blocked.append(
            f"AI confidence 임계 {inp.ai_confidence_threshold} < "
            f"{th.min_ai_confidence_threshold} — 자동 실행에는 더 높은 신뢰도 필요."
        )
    else:
        passed.append(
            f"AI confidence 임계 {inp.ai_confidence_threshold} ≥ "
            f"{th.min_ai_confidence_threshold}."
        )

    if inp.signal_quality_threshold < th.min_signal_quality_threshold:
        blocked.append(
            f"signal quality 임계 {inp.signal_quality_threshold} < "
            f"{th.min_signal_quality_threshold}."
        )
    else:
        passed.append(
            f"signal quality 임계 {inp.signal_quality_threshold} ≥ "
            f"{th.min_signal_quality_threshold}."
        )

    # --- 10) 시스템 안정성 ---
    if inp.system_errors > th.max_system_errors:
        blocked.append(
            f"시스템 오류 {inp.system_errors}건 > {th.max_system_errors} — "
            "AI 자동 실행 진입 전 모두 0."
        )
    else:
        passed.append(f"시스템 오류 {inp.system_errors}건.")

    if inp.audit_missing_count > th.max_audit_missing:
        blocked.append(
            f"audit 누락 {inp.audit_missing_count}건 > {th.max_audit_missing}."
        )
    else:
        passed.append(f"audit 누락 {inp.audit_missing_count}건.")

    if inp.approval_bypass_attempts > th.max_approval_bypass_attempts:
        blocked.append(
            f"approval 우회 시도 {inp.approval_bypass_attempts}건 — 즉시 차단."
        )
    else:
        passed.append("approval 우회 시도 0건.")

    # --- 11) ENABLE_LIVE_TRADING 현재값 (정보 표시) ---
    if not inp.enable_live_trading:
        cautions.append(
            "ENABLE_LIVE_TRADING=false 상태 — 본 게이트가 READY_FOR_REVIEW를 "
            "반환해도 실제 LIVE 라우팅에는 `ENABLE_LIVE_TRADING=true` 별도 활성화 "
            "+ KIS 실주문 라우팅 PR 필요."
        )

    # --- verdict ---
    if not passed and not blocked:
        verdict = AIExecutionGateVerdict.UNKNOWN
    elif blocked:
        verdict = AIExecutionGateVerdict.BLOCKED
    elif cautions:
        verdict = AIExecutionGateVerdict.CAUTION
    else:
        verdict = AIExecutionGateVerdict.READY_FOR_REVIEW

    return AIExecutionActivationGateResult(
        strategy_name=inp.strategy_name,
        evaluated_at=inp.evaluated_at,
        verdict=verdict,
        passed_criteria=passed,
        blocked_criteria=blocked,
        cautions=cautions,
        required_actions=actions,
        metrics={
            "promotion_gate_passed":         inp.promotion_gate_passed,
            "paper_gate_passed":             inp.paper_gate_passed,
            "ai_assist_gate_passed":         inp.ai_assist_gate_passed,
            "live_manual_gate_passed":       inp.live_manual_gate_passed,
            "user_explicit_opt_in":          inp.user_explicit_opt_in,
            "enable_live_trading":           inp.enable_live_trading,
            "enable_ai_execution":           inp.enable_ai_execution,
            "enable_futures_live_trading":   inp.enable_futures_live_trading,
            "live_manual_days":              inp.live_manual_days,
            "ai_assist_days":                inp.ai_assist_days,
            "risk_manager_active":           inp.risk_manager_active,
            "order_guard_active":            inp.order_guard_active,
            "ai_permission_gate_active":     inp.ai_permission_gate_active,
            "audit_log_complete":            inp.audit_log_complete,
            "kill_switch_ready":             inp.kill_switch_ready,
            "circuit_breaker_configured":    inp.circuit_breaker_configured,
            "current_max_order_notional_krw": inp.current_max_order_notional_krw,
            "current_max_daily_loss_krw":     inp.current_max_daily_loss_krw,
            "current_max_daily_order_count":  inp.current_max_daily_order_count,
            "current_max_open_positions":     inp.current_max_open_positions,
            "allowed_symbols_count":          len(inp.allowed_symbols),
            "explicit_time_window_set":       inp.explicit_time_window_set,
            "window_start_kst":               (
                inp.window_start_kst.isoformat()
                if inp.window_start_kst is not None else None
            ),
            "window_end_kst":                 (
                inp.window_end_kst.isoformat()
                if inp.window_end_kst is not None else None
            ),
            "ai_confidence_threshold":        inp.ai_confidence_threshold,
            "signal_quality_threshold":       inp.signal_quality_threshold,
            "system_errors":                  inp.system_errors,
            "audit_missing_count":            inp.audit_missing_count,
            "approval_bypass_attempts":       inp.approval_bypass_attempts,
            "futures_target":                 inp.futures_target,
        },
        thresholds={
            "max_order_notional_krw":      th.max_order_notional_krw,
            "max_daily_loss_krw":          th.max_daily_loss_krw,
            "max_daily_order_count":       th.max_daily_order_count,
            "max_open_positions":          th.max_open_positions,
            "min_symbol_whitelist_size":   th.min_symbol_whitelist_size,
            "max_symbol_whitelist_size":   th.max_symbol_whitelist_size,
            "allowed_window_start_kst":    th.allowed_window_start_kst.isoformat(),
            "allowed_window_end_kst":      th.allowed_window_end_kst.isoformat(),
            "min_ai_confidence_threshold": th.min_ai_confidence_threshold,
            "min_signal_quality_threshold": th.min_signal_quality_threshold,
            "min_live_manual_days":        th.min_live_manual_days,
            "min_ai_assist_days":          th.min_ai_assist_days,
            "max_system_errors":           th.max_system_errors,
            "max_audit_missing":           th.max_audit_missing,
            "max_approval_bypass_attempts": th.max_approval_bypass_attempts,
        },
        next_step=_next_step_for_verdict(verdict),
    )


def _next_step_for_verdict(v: AIExecutionGateVerdict) -> str:
    if v == AIExecutionGateVerdict.READY_FOR_REVIEW:
        return (
            "활성화 *검토 가능* — **실제 활성화 아님**. 별도 옵트인 PR + 사용자 "
            "명시 승인 + `ENABLE_AI_EXECUTION=true` 전환 + 초소액 canary + 즉시 "
            "kill switch 가능 모두 필요. 선물 AI Execution은 별도 게이트."
        )
    if v == AIExecutionGateVerdict.CAUTION:
        return (
            "기준 충족이지만 CAUTION 사유 검토 필요. 운영자 점검 후 재평가."
        )
    if v == AIExecutionGateVerdict.BLOCKED:
        return (
            "전제 조건 / 안전 인프라 / 운영 한도 중 하나 이상 미달. "
            "required_actions 진행 후 재평가. 활성화 절대 금지."
        )
    return "데이터 부족 — 입력 확보 후 재평가. 보수적으로 BLOCKED 취급 권장."


# ---------- markdown report ----------


def render_markdown_report(result: AIExecutionActivationGateResult) -> str:
    """결과 → markdown. 고지 강제, BUY/SELL/HOLD 0건."""
    lines: list[str] = []
    lines.append(f"# AI Execution Activation Gate Report — {result.strategy_name}")
    lines.append("")
    lines.append(
        f"_생성: {result.generated_at.isoformat()} · "
        f"평가 시점: {result.evaluated_at.isoformat()}_"
    )
    lines.append("")
    lines.append("> ⚠️ **본 리포트는 *실제 활성화가 아니다*.**")
    lines.append("> ")
    lines.append(
        "> READY_FOR_REVIEW는 *활성화 검토 가능* 상태를 의미. 실제 "
        "`LIVE_AI_EXECUTION` 활성화는 별도 옵트인 PR + 사용자 명시 승인 + "
        "`ENABLE_AI_EXECUTION=true` 전환 + 초소액 canary + 즉시 kill switch "
        "가능 모두 필요."
    )
    lines.append("> ")
    lines.append(
        "> **선물 AI Execution은 본 게이트가 *영구* 허용하지 않는다** — "
        "`futures_allowed=False` 불변."
    )
    lines.append("")
    lines.append("## 판정")
    lines.append("")
    lines.append(f"- **Verdict: `{result.verdict.value}`**")
    lines.append(f"- 다음 단계: {result.next_step}")
    lines.append("")
    if result.blocked_criteria:
        lines.append("## 차단 사유 (BLOCKED)")
        for c in result.blocked_criteria:
            lines.append(f"- 🛑 {c}")
        lines.append("")
    if result.cautions:
        lines.append("## CAUTION 항목")
        for c in result.cautions:
            lines.append(f"- ⚠️ {c}")
        lines.append("")
    if result.required_actions:
        lines.append("## 필요 조치")
        for a in result.required_actions:
            lines.append(f"- 📝 {a}")
        lines.append("")
    if result.passed_criteria:
        lines.append("## 충족 기준")
        for c in result.passed_criteria:
            lines.append(f"- ✅ {c}")
        lines.append("")
    lines.append("## 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    for k, v in result.metrics.items():
        lines.append(f"| `{k}` | {v} |")
    lines.append("")
    lines.append("## 임계 (AIExecutionGateThresholds)")
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


# ---------- read-only policy (for GET /policy) ----------


def get_policy_summary(thresholds: AIExecutionGateThresholds | None = None) -> dict[str, Any]:
    """기본 제한 / required gates / futures_allowed=false 안내.

    GET /api/governance/ai-execution-gate/policy 가 사용. 본 함수는 *상수만*
    반환 — broker / DB / settings 접근 0건.
    """
    th = thresholds or AIExecutionGateThresholds()
    return {
        "futures_allowed": False,
        "activation_requires_separate_pr": True,
        "limits": {
            "max_order_notional_krw":     th.max_order_notional_krw,
            "max_daily_loss_krw":         th.max_daily_loss_krw,
            "max_daily_order_count":      th.max_daily_order_count,
            "max_open_positions":         th.max_open_positions,
            "max_symbol_whitelist_size":  th.max_symbol_whitelist_size,
            "min_ai_confidence_threshold": th.min_ai_confidence_threshold,
            "min_signal_quality_threshold": th.min_signal_quality_threshold,
            "allowed_window_start_kst":   th.allowed_window_start_kst.isoformat(),
            "allowed_window_end_kst":     th.allowed_window_end_kst.isoformat(),
        },
        "required_gates": [
            "promotion_gate",
            "paper_gate",
            "ai_assist_gate",
            "live_manual_gate",
        ],
        "required_infrastructure": [
            "risk_manager_active",
            "order_guard_active",
            "ai_permission_gate_active",
            "audit_log_complete",
            "kill_switch_ready",
            "circuit_breaker_configured",
        ],
        "min_operating_days": {
            "live_manual": th.min_live_manual_days,
            "ai_assist":   th.min_ai_assist_days,
        },
        "disclaimer": (
            "본 게이트의 PASS(READY_FOR_REVIEW)는 *실제 활성화가 아니다*. "
            "활성화는 별도 옵트인 PR + 사용자 명시 승인 필요. 선물 AI Execution은 "
            "본 게이트로 영구 허용되지 않는다."
        ),
    }
