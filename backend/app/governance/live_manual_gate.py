"""Live Manual Gate (#73) — 초소액 수동승인 모드 진입 readiness 평가.

본 게이트는 **실거래를 활성화하지 않는다**. 다음 4가지를 *판단*만 한다:

1. Paper Gate(#72) PASS 여부.
2. Promotion Gate(#27) PASS 여부.
3. 운영자 explicit opt-in 여부 (사용자가 직접 명시).
4. 안전 플래그 / 자금 한도 / 시스템 오류 기준 충족 여부.

PASS는 *Live Manual Approval 모드 진입 검토 가능* 을 의미하며 실제 `ENABLE_LIVE_TRADING`
활성화 / KIS 실주문 라우팅 / `KIS_IS_PAPER=false` 전환은 **별도 옵트인 PR + 사용자
명시 승인** 후에만 가능하다.

CLAUDE.md 절대 원칙 invariant (코드 단 + 정적 grep 가드로 강제):

- broker / OrderExecutor / route_order / KIS / 외부 HTTP / AI SDK import 0건.
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING`
  / `KIS_IS_PAPER` 변경 0건 — 본 모듈은 `settings.*` 을 *읽지도 않는다*.
- DB write (INSERT/UPDATE/DELETE/.add/.commit/.flush) 0건.
- `LiveManualGateResult.is_live_authorization=True` 생성 불가 (ValueError).
- `LiveManualGateResult.is_order_signal=True` 생성 불가 (ValueError).

극소액 정책 (default):
- 1회 주문금액 ≤ 50,000원 (권장 10,000~50,000)
- 일일 손실한도 ≤ 10,000원
- 최대 보유 종목 ≤ 3개
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ---------- enums / thresholds ----------


class LiveManualGateVerdict(StrEnum):
    """4단계 판정. PASS여도 실거래 자동 허가 *아님*."""
    PASS    = "PASS"
    CAUTION = "CAUTION"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LiveManualGateThresholds:
    """초소액 + 보수적 임계. live_manual_gate.md와 lockstep.

    필드 default는 *권장* 수치. 운영자가 평가 시 override 가능.
    """
    # 극소액 정책.
    max_order_notional_krw:        int = 50_000   # 1회 주문 ≤ 5만 원
    max_daily_loss_krw:            int = 10_000   # 일일 손실한도 ≤ 1만 원
    max_open_positions:            int = 3        # 동시 보유 ≤ 3개

    # 운영 기간.
    min_operating_days:            int = 30       # 권장 1개월 이상

    # 시스템 안정성.
    max_system_errors:             int = 0
    max_audit_missing:             int = 0
    max_approval_bypass_attempts:  int = 0

    # 안전 플래그 invariant — 본 게이트가 *확인*만 한다 (변경 X).
    # *모두 false*여야 PASS. ai_execution=true / futures_live=true 면 BLOCKED.
    require_ai_execution_disabled:    bool = True
    require_futures_live_disabled:    bool = True
    require_approval_required_true:   bool = True
    require_user_explicit_opt_in:     bool = True

    # 전제 게이트.
    require_paper_gate_pass:          bool = True
    require_promotion_gate_pass:      bool = True


# ---------- input DTO ----------


@dataclass(frozen=True)
class LiveManualGateInput:
    """Live Manual Gate 평가 입력.

    호출자가 다음을 채운다 (운영자가 직접 입력 또는 collector가 산출):
    - paper_gate_passed / promotion_gate_passed: 사전 게이트 결과.
    - user_explicit_opt_in: 운영자가 *명시*로 opt-in 했는지 (UI 토글 / `.env` /
      별도 PR 코멘트 — 본 모듈은 *판단만* 하며 자동 토글하지 *않는다*).
    - approval_required: 모드의 `requires_user_approval` capability. 본 게이트는
      `LIVE_MANUAL_APPROVAL`에서 True 인지 확인만 함.
    - ai_execution_enabled: `ENABLE_AI_EXECUTION` 현재값 (운영자가 별도로 확인).
      본 모듈은 이 값을 *받기만* 하고 변경하지 않는다.
    - futures_live_enabled: `ENABLE_FUTURES_LIVE_TRADING` 현재값.

    극소액 / 손실한도 / 보유 종목 / 운영 일수 / 시스템 오류 / audit 누락은
    DB collector 또는 운영자 입력으로 채운다.
    """
    strategy_name:                 str
    period_start:                  datetime
    period_end:                    datetime

    # 전제 게이트.
    paper_gate_passed:             bool = False
    promotion_gate_passed:         bool = False

    # 운영자 / 안전 플래그 (현재값 — 본 게이트는 변경 X).
    user_explicit_opt_in:          bool = False
    approval_required:             bool = False
    ai_execution_enabled:          bool = False
    futures_live_enabled:          bool = False
    enable_live_trading:           bool = False   # ENABLE_LIVE_TRADING 현재값

    # 극소액 정책 (현재 적용 한도).
    current_max_order_notional_krw: int = 0       # 0 = 미설정 → BLOCKED
    current_max_daily_loss_krw:     int = 0
    current_max_open_positions:     int = 0
    allowed_symbols:                tuple[str, ...] = field(default_factory=tuple)

    # 운영 로그 (DB collector 산출).
    operating_days:                 int = 0
    total_live_manual_orders:       int = 0
    approved_orders:                int = 0
    rejected_orders:                int = 0
    expired_or_cancelled_orders:    int = 0
    approval_bypass_attempts:       int = 0       # 비정상 우회 시도 (audit drift)
    audit_missing_count:            int = 0
    system_errors:                  int = 0
    emergency_stops_in_period:      int = 0

    @property
    def period_days(self) -> int:
        return max(0, (self.period_end - self.period_start).days)


# ---------- result DTO ----------


@dataclass
class LiveManualGateResult:
    """평가 결과.

    invariants:
    - `is_live_authorization=False` 항상.
    - `is_order_signal=False` 항상.
    - `live_flag_changed=False` 항상 (본 모듈은 어떤 안전 플래그도 변경 X).
    """
    strategy_name:           str
    period_start:            datetime
    period_end:              datetime
    verdict:                 LiveManualGateVerdict
    passed_criteria:         list[str] = field(default_factory=list)
    blocked_criteria:        list[str] = field(default_factory=list)
    cautions:                list[str] = field(default_factory=list)
    required_actions:        list[str] = field(default_factory=list)
    metrics:                 dict[str, Any] = field(default_factory=dict)
    thresholds:              dict[str, Any] = field(default_factory=dict)
    next_step:               str = ""
    is_live_authorization:   bool = False
    is_order_signal:         bool = False
    generated_at:            datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        if self.is_live_authorization is not False:
            raise ValueError(
                "LiveManualGateResult.is_live_authorization must be False. "
                "PASS verdict means 'eligible for LIVE_MANUAL_APPROVAL entry "
                "review with micro-amount limits and per-order approval', "
                "NOT 'authorize live trading'."
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "LiveManualGateResult.is_order_signal must be False — "
                "Live Manual Gate does not produce BUY/SELL/HOLD signals."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name":         self.strategy_name,
            "period_start":          self.period_start.isoformat(),
            "period_end":            self.period_end.isoformat(),
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
            "live_flag_changed":     False,
            "mode_changed":          False,
            "generated_at":          self.generated_at.isoformat(),
        }


# ---------- evaluator ----------


def evaluate_live_manual_gate(
    inp: LiveManualGateInput,
    thresholds: LiveManualGateThresholds | None = None,
) -> LiveManualGateResult:
    """Live Manual Gate 평가 — 입력 DTO → 결과 DTO. 외부 시스템 영향 0건."""
    th = thresholds or LiveManualGateThresholds()
    passed: list[str] = []
    blocked: list[str] = []
    cautions: list[str] = []
    actions: list[str] = []

    # --- 1) 전제 게이트 ---
    if th.require_paper_gate_pass:
        if inp.paper_gate_passed:
            passed.append("Paper Gate PASS.")
        else:
            blocked.append("Paper Gate PASS 필요 (`docs/paper_gate_policy.md` 참조).")
            actions.append(
                "Paper 모드 4주 이상 운용 + `scripts/evaluate_paper_gate.py` PASS."
            )

    if th.require_promotion_gate_pass:
        if inp.promotion_gate_passed:
            passed.append("Promotion Gate PASS.")
        else:
            blocked.append("Promotion Gate PASS 필요 (#27).")
            actions.append(
                "`/api/governance/strategy-promotion/evaluate`로 LIVE_MANUAL_APPROVAL "
                "target 평가 통과 확인."
            )

    # --- 2) 운영자 명시 opt-in ---
    if th.require_user_explicit_opt_in:
        if inp.user_explicit_opt_in:
            passed.append("운영자 명시 opt-in 확인.")
        else:
            blocked.append(
                "운영자 명시 opt-in 필요 — 자동 활성화 금지. 사용자가 별도 "
                "PR / 운영 노트로 명시해야 함."
            )
            actions.append(
                "운영자가 *수동으로* opt-in을 명시한 후 본 게이트 재평가."
            )

    # --- 3) 모드 / approval API 강제 ---
    if th.require_approval_required_true:
        if inp.approval_required:
            passed.append(
                "approval_required=True — 모든 주문이 PermissionGate 경유."
            )
        else:
            blocked.append(
                "approval_required=False — LIVE_MANUAL_APPROVAL 모드 capability와 "
                "충돌. 모든 주문이 PendingApproval 큐를 경유하지 않으면 진행 금지."
            )

    # --- 4) AI execution / futures live 비활성 ---
    if th.require_ai_execution_disabled:
        if not inp.ai_execution_enabled:
            passed.append("ENABLE_AI_EXECUTION=false — AI 무인 실행 차단.")
        else:
            blocked.append(
                "ENABLE_AI_EXECUTION=true — Live Manual 단계에서 AI 자동 실행은 "
                "금지. `LIVE_AI_*`는 별도 옵트인 PR 필요."
            )
            actions.append("`ENABLE_AI_EXECUTION=false` 로 되돌린 후 재평가.")

    if th.require_futures_live_disabled:
        if not inp.futures_live_enabled:
            passed.append("ENABLE_FUTURES_LIVE_TRADING=false — 선물 실거래 차단.")
        else:
            blocked.append(
                "ENABLE_FUTURES_LIVE_TRADING=true — Live Manual 단계는 주식 "
                "전용. 선물 LIVE는 별도 9단계 blocker (live_activation_blockers.md §3.1)."
            )
            actions.append("`ENABLE_FUTURES_LIVE_TRADING=false` 로 되돌린 후 재평가.")

    # --- 5) 극소액 정책 ---
    if inp.current_max_order_notional_krw <= 0:
        blocked.append("1회 주문 한도 미설정 — `RiskPolicy.max_order_notional` 필요.")
        actions.append("RiskPolicy 한도를 명시적으로 설정.")
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
        blocked.append("일일 손실한도 미설정 — `RiskPolicy.max_daily_loss` 필요.")
    elif inp.current_max_daily_loss_krw > th.max_daily_loss_krw:
        blocked.append(
            f"일일 손실한도 {inp.current_max_daily_loss_krw:,}원 > "
            f"권장 {th.max_daily_loss_krw:,}원 — 손실 제한이 너무 큼."
        )
        actions.append(
            f"RiskPolicy.max_daily_loss ≤ {th.max_daily_loss_krw:,} 으로 조정."
        )
    else:
        passed.append(
            f"일일 손실한도 {inp.current_max_daily_loss_krw:,}원 ≤ "
            f"{th.max_daily_loss_krw:,}원."
        )

    if inp.current_max_open_positions <= 0:
        blocked.append("동시 보유 종목 한도 미설정 — `RiskPolicy.max_positions` 필요.")
    elif inp.current_max_open_positions > th.max_open_positions:
        blocked.append(
            f"동시 보유 종목 {inp.current_max_open_positions} > "
            f"{th.max_open_positions} — 분산이 너무 넓음."
        )
        actions.append(
            f"RiskPolicy.max_positions ≤ {th.max_open_positions} 으로 조정."
        )
    else:
        passed.append(
            f"동시 보유 종목 {inp.current_max_open_positions} ≤ "
            f"{th.max_open_positions}."
        )

    if not inp.allowed_symbols:
        cautions.append(
            "allowed_symbols 미지정 — watchlist + RiskPolicy로 종목 화이트리스트 "
            "명시 권장."
        )
    else:
        passed.append(
            f"allowed_symbols 명시 — {len(inp.allowed_symbols)}개 종목."
        )

    # --- 6) 운영 일수 ---
    if inp.operating_days >= th.min_operating_days:
        passed.append(
            f"Live Manual 운영 {inp.operating_days}일 ≥ {th.min_operating_days}일."
        )
    elif inp.operating_days > 0:
        cautions.append(
            f"Live Manual 운영 {inp.operating_days}일 < {th.min_operating_days}일 — "
            "운영 기간 더 누적 권장."
        )

    # --- 7) 시스템 오류 / audit 누락 / 우회 시도 ---
    if inp.system_errors > th.max_system_errors:
        blocked.append(
            f"시스템 오류 {inp.system_errors}건 > {th.max_system_errors} — "
            "라이브 진입 전 모두 0이어야 함."
        )
        actions.append("시스템 오류 원인 분석 + 재현 가능한 fix 후 재평가.")
    else:
        passed.append(f"시스템 오류 {inp.system_errors}건.")

    if inp.audit_missing_count > th.max_audit_missing:
        blocked.append(
            f"audit 누락 {inp.audit_missing_count}건 > {th.max_audit_missing} — "
            "감사 흐름 깨짐."
        )
    else:
        passed.append(f"audit 누락 {inp.audit_missing_count}건.")

    if inp.approval_bypass_attempts > th.max_approval_bypass_attempts:
        blocked.append(
            f"approval 우회 시도 {inp.approval_bypass_attempts}건 — 즉시 차단. "
            "운영자 + 코드 검토 필요."
        )
        actions.append(
            "audit 로그 검토 + Approval 경로 외 broker.place_order 시도 차단 확인."
        )
    else:
        passed.append("approval 우회 시도 0건.")

    # --- 8) ENABLE_LIVE_TRADING 현재값 — 정보 표시 (block X) ---
    if inp.enable_live_trading:
        cautions.append(
            "ENABLE_LIVE_TRADING=true 이미 활성. 별도 옵트인 PR 통과 여부 확인 권장 — "
            "본 게이트는 readiness 평가만 수행하며 LIVE 활성화를 *수행*하지 않음."
        )

    # --- verdict ---
    if not passed and not blocked:
        verdict = LiveManualGateVerdict.UNKNOWN
    elif blocked:
        verdict = LiveManualGateVerdict.BLOCKED
    elif cautions:
        verdict = LiveManualGateVerdict.CAUTION
    else:
        verdict = LiveManualGateVerdict.PASS

    return LiveManualGateResult(
        strategy_name=inp.strategy_name,
        period_start=inp.period_start,
        period_end=inp.period_end,
        verdict=verdict,
        passed_criteria=passed,
        blocked_criteria=blocked,
        cautions=cautions,
        required_actions=actions,
        metrics={
            "period_days":                    inp.period_days,
            "paper_gate_passed":              inp.paper_gate_passed,
            "promotion_gate_passed":          inp.promotion_gate_passed,
            "user_explicit_opt_in":           inp.user_explicit_opt_in,
            "approval_required":              inp.approval_required,
            "ai_execution_enabled":           inp.ai_execution_enabled,
            "futures_live_enabled":           inp.futures_live_enabled,
            "enable_live_trading":            inp.enable_live_trading,
            "current_max_order_notional_krw": inp.current_max_order_notional_krw,
            "current_max_daily_loss_krw":     inp.current_max_daily_loss_krw,
            "current_max_open_positions":     inp.current_max_open_positions,
            "allowed_symbols_count":          len(inp.allowed_symbols),
            "operating_days":                 inp.operating_days,
            "total_live_manual_orders":       inp.total_live_manual_orders,
            "approved_orders":                inp.approved_orders,
            "rejected_orders":                inp.rejected_orders,
            "expired_or_cancelled_orders":    inp.expired_or_cancelled_orders,
            "approval_bypass_attempts":       inp.approval_bypass_attempts,
            "audit_missing_count":            inp.audit_missing_count,
            "system_errors":                  inp.system_errors,
            "emergency_stops_in_period":      inp.emergency_stops_in_period,
        },
        thresholds={
            "max_order_notional_krw":       th.max_order_notional_krw,
            "max_daily_loss_krw":           th.max_daily_loss_krw,
            "max_open_positions":           th.max_open_positions,
            "min_operating_days":           th.min_operating_days,
            "max_system_errors":            th.max_system_errors,
            "max_audit_missing":            th.max_audit_missing,
            "max_approval_bypass_attempts": th.max_approval_bypass_attempts,
        },
        next_step=_next_step_for_verdict(verdict),
    )


def _next_step_for_verdict(v: LiveManualGateVerdict) -> str:
    if v == LiveManualGateVerdict.PASS:
        return (
            "Live Manual Approval 모드 진입 *검토 가능* (실거래 자동 허가 아님). "
            "별도 옵트인 PR + 사용자 명시 승인 + KIS 실주문 라우팅 활성화 PR "
            "필요. 모든 주문은 여전히 PendingApproval 큐 경유."
        )
    if v == LiveManualGateVerdict.CAUTION:
        return (
            "PASS 임계 충족이지만 CAUTION 사유 검토 필요. 운영 기간 추가 누적 "
            "또는 운영자 점검 후 재평가."
        )
    if v == LiveManualGateVerdict.BLOCKED:
        return (
            "전제 조건 / 안전 플래그 / 운영 데이터 중 하나 이상 미달. "
            "required_actions 절차 진행 후 재평가. 실거래 진입 금지."
        )
    return "데이터 부족 — 입력 확보 후 재평가. 보수적으로 BLOCKED 취급 권장."


# ---------- markdown report ----------


def render_markdown_report(result: LiveManualGateResult) -> str:
    """Live Manual Gate 결과를 markdown 리포트로 변환.

    상단에 *실거래 허가 아님* 고지 강제. BUY/SELL/HOLD 문구 0건.
    """
    lines: list[str] = []
    lines.append(f"# Live Manual Gate Report — {result.strategy_name}")
    lines.append("")
    lines.append(
        f"_생성: {result.generated_at.isoformat()} · "
        f"기간: {result.period_start.date()} ~ {result.period_end.date()}_"
    )
    lines.append("")
    lines.append("> ⚠️ **본 리포트는 *실거래 허가가 아니다*.**")
    lines.append("> ")
    lines.append(
        "> PASS는 *Live Manual Approval 모드 진입 검토 가능 + 초소액 + 모든 주문 "
        "수동승인* 상태를 의미. 실제 LIVE 활성화는 별도 옵트인 PR + 사용자 명시 "
        "승인 + `ENABLE_LIVE_TRADING=true` 전환 (현재 default false) 모두 필요."
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
    lines.append("## 임계 (LiveManualGateThresholds)")
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
