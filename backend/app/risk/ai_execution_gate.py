"""AI Execution Gate (#45).

`LIVE_AI_EXECUTION` 모드에서 AI가 자동 주문을 실행할 수 있는지 *최종* 권한을
판정하는 게이트. 기존 RiskManager / AiPermissionGate(#39) / OrderGuard(#38) /
PermissionGate(#41) 위에 추가되는 *얇은* 안전 레이어로, 다음 책임만을 가진다:

1. **기본 비활성** — `ENABLE_AI_EXECUTION=false` (default)이면 항상 BLOCKED.
   `ENABLE_LIVE_TRADING=false` 역시 BLOCKED.
2. **canary mode** — `is_canary_mode=True`이면 모든 통과 후보를 `CANARY_ONLY`
   로 분류 (실제 broker 주문 X). 운영자가 1~2주 canary 운용 후 명시적으로
   해제할 때까지 자동 실행은 시뮬 단계에 머문다.
3. **사전 정의 범위** — symbol whitelist / max notional / time window / daily
   count / confidence / explanation / exit plan 등 보수적 한도.
4. **결과 carry** — `audit_note`와 `to_audit_meta()`로 audit row의
   `ai_decision_meta.ai_execution_gate_result`에 영구화.

본 게이트는 RiskManager를 *대체하지 않는다*. route_order의 가드 체인은:

    OrderGuard → RiskManager → AiPermissionGate → AIExecutionGate
              → PermissionGate (필요 시) → OrderExecutor → Broker

AIExecutionGate는 마지막 *AI-specific* 권한 검사 — RiskManager가 신호 품질 /
한도 / 손실 제어 / 시세 stale 등 일반 가드를, 본 게이트가 *AI 자동 실행에만*
적용되는 추가 보수적 한도를 담당한다.

**중요 invariant (테스트로 강제):**
- 본 모듈은 broker / OrderExecutor / route_order를 import하지 않는다 — 순수
  의사결정 함수.
- API key / 계좌번호 / Secret을 인자로 받지 않는다 (#39 invariant 상속).
- 기본 정책으로 `evaluate_ai_execution`을 호출하면 어떤 입력에서도 BLOCKED
  를 반환한다 (`enable_ai_execution=False` default).
- canary mode에서 통과 시 `CANARY_ONLY`, 절대 `ALLOW`를 반환하지 않는다.
- 본 PR에서 `ENABLE_AI_EXECUTION=true`로 바꾸는 코드 경로 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from typing import Any

from app.core.modes import MODE_CAPABILITIES, OperationMode


# ====================================================================
# Decisions / Inputs / Results
# ====================================================================


class AIExecutionDecision(StrEnum):
    """AI execution gate 판정.

    - `ALLOW`         — 모든 가드 통과 + canary mode False. AI가 실제 주문을
                         만들 수 있는 *유일한* 상태. 본 PR 시점에서 default
                         정책으로는 도달 불가능 (enable_ai_execution=False).
    - `CANARY_ONLY`   — 모든 가드 통과 but canary mode True. AI가 *기록만*
                         하고 broker로는 진행하지 않는다. 운영자가 canary
                         결과 검토 후 명시적으로 canary mode를 해제해야 ALLOW.
    - `BLOCKED`       — 한 가드라도 실패. reasons에 모든 위반 사유 누적.
    """

    ALLOW       = "ALLOW"
    CANARY_ONLY = "CANARY_ONLY"
    BLOCKED     = "BLOCKED"


@dataclass(frozen=True)
class AIExecutionPolicy:
    """AIExecutionGate 정책. RiskPolicy와 분리된 별도 dataclass — RiskManager
    인스턴스가 본 정책을 직접 참조하지 않는다 (책임 분리).

    **모든 필드 기본값은 보수적**:
    - `enable_ai_execution=False` — 기본 비활성 (`ENABLE_AI_EXECUTION` env)
    - `enable_live_trading=False` — 기본 비활성 (`ENABLE_LIVE_TRADING` env)
    - `is_canary_mode=True` — canary 우선; ALLOW 분기는 운영자가 명시 해제
                              해야 도달
    - `min_confidence=80` — AI 자동 실행은 높은 confidence만
    - `min_quality_score=70` — 신호 quality도 보수적
    - `require_explanation=True` — reasoning 없는 자동 실행 거부
    - `require_exit_plan=True` — target/stop 없는 자동 실행 거부
    - `max_notional_per_order=100_000` (10만원) — 극소액 시작
    - `symbol_whitelist=frozenset()` — *비어 있으면 모든 종목 차단* (안전 측)
    - `max_orders_per_day=3` — 일일 한도 매우 보수적
    - `window_start_hour_kst=10`, `window_end_hour_kst=14` — 개장 직후/마감
                              직전 변동성 회피
    """

    # 운영 게이트 (env flag)
    enable_ai_execution: bool = False
    enable_live_trading: bool = False

    # canary
    is_canary_mode: bool = True

    # 신호 품질
    min_confidence:      int  = 80
    min_quality_score:   int  = 70
    require_explanation: bool = True
    require_exit_plan:   bool = True

    # 주문 크기
    max_notional_per_order: int = 100_000  # 10만원 (극소액 시작)

    # 종목 / 시간
    # 비어 있으면 *모든 종목 차단* — 운영자가 명시적으로 등록한 종목만 허용.
    # `frozenset()` is the documented safe default; `None` would mean "all".
    symbol_whitelist: frozenset[str] = field(default_factory=frozenset)
    window_start_hour_kst: int = 10  # 09:30 변동성 회피 → 10시 시작
    window_end_hour_kst:   int = 14  # 14시까지 (장마감 1.5시간 전)

    # 빈도 / 일일
    max_orders_per_day: int = 3

    # canary mode 안내문
    canary_note: str = "AI execution canary only; no broker order sent"


@dataclass(frozen=True)
class AIExecutionInput:
    """게이트 평가 입력. RiskManager가 carry하지 않는 *AI 자동 실행에만 필요한*
    필드들을 담는다 — caller(미래의 LIVE_AI_EXECUTION 흐름)가 broker / DB
    스냅샷 + AI 메타데이터를 한 객체로 전달."""

    mode:               OperationMode
    symbol:             str
    quantity:           int
    latest_price:       int

    # AI 메타데이터 — AICandidate(#44) 또는 strategy 내부의 AI 결정에서 carry
    confidence:         int = 0
    quality_score:      int = 0
    explanation:        str | None = None
    target_price:       int | None = None  # exit plan
    stop_price:         int | None = None  # exit plan

    agent_name:         str | None = None  # 누가 만든 결정 (#187 AgentDecisionLog과 cross-ref)
    agent_chain_id:     str | None = None
    strategy:           str | None = None

    # Pre-trade 누계 — 호출자가 DB 조회로 채워서 전달
    today_ai_order_count: int = 0

    # 상위 가드 결과 — RiskManager / AiPermissionGate / OrderGuard /
    # PermissionGate를 *이미 통과한* 후 본 게이트가 호출됐다는 signal.
    # carry해서 audit row에 함께 영구화. 본 게이트는 이 결과들을 *재검증*
    # 하지 않는다 (책임 분리) — 다만 한 가드라도 PASS=False이면 BLOCKED.
    risk_passed:           bool = False
    permission_passed:     bool = False  # AiPermissionGate (#39)
    order_guard_passed:    bool = False
    manual_approval_done:  bool = True   # LIVE_AI_EXECUTION은 사람 승인 없음 — default True

    # KST evaluation time — 테스트가 결정론적으로 검증할 수 있도록 주입 가능.
    # None이면 datetime.now(KST)로 fallback.
    now_kst:               datetime | None = None


@dataclass
class AIExecutionResult:
    """게이트 판정 결과. audit row의 ai_decision_meta에 carry."""

    decision:    AIExecutionDecision
    reasons:     list[str]            = field(default_factory=list)
    passed:      list[str]            = field(default_factory=list)
    audit_note:  str                  = ""
    notional:    int                  = 0
    is_canary:   bool                 = False
    evaluated_at: datetime            = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def allowed_to_execute(self) -> bool:
        """실제 broker 주문이 허용되는가. CANARY_ONLY는 False (기록만)."""
        return self.decision == AIExecutionDecision.ALLOW

    @property
    def actual_broker_order_sent(self) -> bool:
        """편의 표면 — 본 게이트만 보면 broker 주문이 *나갈 예정이었는지*
        리턴. caller가 실제 주문 후 audit row에 채울 때 같은 의미로 사용
        가능 (게이트 자체는 broker를 호출하지 않는다)."""
        return self.allowed_to_execute

    def to_audit_meta(self) -> dict[str, Any]:
        """audit row의 ai_decision_meta.ai_execution_gate_result에 영구화."""
        return {
            "decision":     self.decision.value,
            "reasons":      list(self.reasons),
            "passed":       list(self.passed),
            "audit_note":   self.audit_note,
            "notional":     self.notional,
            "is_canary":    self.is_canary,
            "evaluated_at": self.evaluated_at.isoformat(),
        }


# ====================================================================
# Helper: KST window
# ====================================================================

_KST = timezone(timedelta(hours=9))


def _is_within_window(now_kst: datetime, start_h: int, end_h: int) -> bool:
    """[start_h, end_h)에 포함되면 True. 동일 day boundary 가정."""
    cur = now_kst.time()
    start = time(hour=max(0, min(23, start_h)))
    end   = time(hour=max(0, min(23, end_h)))
    if start >= end:
        return False
    return start <= cur < end


# ====================================================================
# Gate
# ====================================================================


def evaluate_ai_execution(
    *,
    inp:    AIExecutionInput,
    policy: AIExecutionPolicy,
) -> AIExecutionResult:
    """순수 함수 — AI 자동 실행 권한 판정.

    **순서 (한 가드라도 실패하면 reasons에 누적, 모든 가드 평가 후 결정):**

    1. mode == LIVE_AI_EXECUTION + capability.ai_can_execute=True
    2. enable_ai_execution=True (env)
    3. enable_live_trading=True (env)
    4. confidence >= min_confidence
    5. quality_score >= min_quality_score
    6. explanation 존재 (require_explanation=True 시)
    7. exit plan 존재 — target_price + stop_price (require_exit_plan=True 시)
    8. notional <= max_notional_per_order
    9. symbol in whitelist (비어 있으면 모두 차단)
    10. now_kst in [window_start, window_end)
    11. today_ai_order_count < max_orders_per_day
    12. risk_passed + permission_passed + order_guard_passed (상위 가드 결과 carry)

    **결정**:
    - reasons 비어 있고 is_canary_mode=False → ALLOW
    - reasons 비어 있고 is_canary_mode=True  → CANARY_ONLY
    - reasons 1+                              → BLOCKED
    """
    reasons: list[str] = []
    passed:  list[str] = []
    notional = max(0, int(inp.latest_price)) * max(0, int(inp.quantity))

    # 1. mode capability
    cap = MODE_CAPABILITIES.get(inp.mode, {})
    if inp.mode != OperationMode.LIVE_AI_EXECUTION:
        reasons.append(
            f"mode {inp.mode.value} is not LIVE_AI_EXECUTION — "
            "AIExecutionGate is only meaningful in LIVE_AI_EXECUTION"
        )
    elif not cap.get("ai_can_execute"):
        reasons.append("mode capability ai_can_execute=False")
    else:
        passed.append("mode_capability")

    # 2. ENABLE_AI_EXECUTION
    if not policy.enable_ai_execution:
        reasons.append(
            "ENABLE_AI_EXECUTION=false (default — opt-in required)"
        )
    else:
        passed.append("enable_ai_execution_flag")

    # 3. ENABLE_LIVE_TRADING
    if not policy.enable_live_trading:
        reasons.append(
            "ENABLE_LIVE_TRADING=false (default — opt-in required)"
        )
    else:
        passed.append("enable_live_trading_flag")

    # 4. confidence
    if inp.confidence < policy.min_confidence:
        reasons.append(
            f"confidence {inp.confidence} < min {policy.min_confidence}"
        )
    else:
        passed.append("min_confidence")

    # 5. quality_score
    if inp.quality_score < policy.min_quality_score:
        reasons.append(
            f"quality_score {inp.quality_score} < min {policy.min_quality_score}"
        )
    else:
        passed.append("min_quality_score")

    # 6. explanation
    if policy.require_explanation:
        if not inp.explanation or not inp.explanation.strip():
            reasons.append("explanation is required but missing")
        else:
            passed.append("require_explanation")

    # 7. exit plan
    if policy.require_exit_plan:
        if not (inp.target_price and inp.target_price > 0
                and inp.stop_price and inp.stop_price > 0):
            reasons.append(
                "exit plan is required (target_price + stop_price > 0)"
            )
        else:
            passed.append("require_exit_plan")

    # 8. max notional
    if notional > policy.max_notional_per_order:
        reasons.append(
            f"notional {notional} > max_notional_per_order "
            f"{policy.max_notional_per_order}"
        )
    else:
        passed.append("max_notional_per_order")

    # 9. symbol whitelist — 비어 있으면 모두 차단 (안전 측)
    if not policy.symbol_whitelist:
        reasons.append(
            "symbol_whitelist is empty — no symbol allowed for AI execution"
        )
    elif inp.symbol not in policy.symbol_whitelist:
        reasons.append(
            f"symbol {inp.symbol} not in whitelist "
            f"({sorted(policy.symbol_whitelist)})"
        )
    else:
        passed.append("symbol_whitelist")

    # 10. KST time window
    now_kst = inp.now_kst or datetime.now(_KST)
    if not _is_within_window(
        now_kst, policy.window_start_hour_kst, policy.window_end_hour_kst,
    ):
        reasons.append(
            f"now KST {now_kst.strftime('%H:%M')} not in execution window "
            f"[{policy.window_start_hour_kst:02d}:00, "
            f"{policy.window_end_hour_kst:02d}:00)"
        )
    else:
        passed.append("execution_window")

    # 11. daily count
    if inp.today_ai_order_count >= policy.max_orders_per_day:
        reasons.append(
            f"today_ai_order_count {inp.today_ai_order_count} >= "
            f"max_orders_per_day {policy.max_orders_per_day}"
        )
    else:
        passed.append("max_orders_per_day")

    # 12. 상위 가드 통과 여부
    if not inp.risk_passed:
        reasons.append("RiskManager guard not passed (risk_passed=False)")
    else:
        passed.append("risk_passed")
    if not inp.permission_passed:
        reasons.append(
            "AiPermissionGate not passed (permission_passed=False)"
        )
    else:
        passed.append("permission_passed")
    if not inp.order_guard_passed:
        reasons.append("OrderGuard not passed (order_guard_passed=False)")
    else:
        passed.append("order_guard_passed")

    # 결정: 한 가드라도 위반이면 BLOCKED. 모두 통과 + canary면 CANARY_ONLY.
    if reasons:
        decision = AIExecutionDecision.BLOCKED
        is_canary = False
        audit_note = (
            f"AI execution BLOCKED: {len(reasons)} reason(s) — "
            f"{'; '.join(reasons)}"
        )
    elif policy.is_canary_mode:
        decision = AIExecutionDecision.CANARY_ONLY
        is_canary = True
        audit_note = policy.canary_note
    else:
        decision = AIExecutionDecision.ALLOW
        is_canary = False
        audit_note = (
            f"AI execution ALLOW: notional={notional} "
            f"symbol={inp.symbol} confidence={inp.confidence}"
        )

    return AIExecutionResult(
        decision=decision,
        reasons=reasons,
        passed=passed,
        audit_note=audit_note,
        notional=notional,
        is_canary=is_canary,
    )


# ====================================================================
# Status surface — UI / API
# ====================================================================


def build_policy_status(policy: AIExecutionPolicy) -> dict[str, Any]:
    """현재 정책을 dict로 — UI/API가 read-only로 노출.

    - `live_ai_execution_disabled`: ENABLE_AI_EXECUTION=False면 True (기본)
    - `is_default_blocked`: 빈 default 정책으로 평가 시 BLOCKED인지
    - `notice`: 운영자 안내문 — "AI API Key는 주문 권한이 아닙니다"
    """
    return {
        "enable_ai_execution":    policy.enable_ai_execution,
        "enable_live_trading":    policy.enable_live_trading,
        "is_canary_mode":         policy.is_canary_mode,
        "min_confidence":         policy.min_confidence,
        "min_quality_score":      policy.min_quality_score,
        "require_explanation":    policy.require_explanation,
        "require_exit_plan":      policy.require_exit_plan,
        "max_notional_per_order": policy.max_notional_per_order,
        "symbol_whitelist":       sorted(policy.symbol_whitelist),
        "window_start_hour_kst":  policy.window_start_hour_kst,
        "window_end_hour_kst":    policy.window_end_hour_kst,
        "max_orders_per_day":     policy.max_orders_per_day,
        "live_ai_execution_disabled": (
            not policy.enable_ai_execution
            or not policy.enable_live_trading
        ),
        "canary_note": policy.canary_note,
        "notice": (
            "AI API Key는 주문 권한이 아닙니다. AI 자동 실행은 "
            "ENABLE_AI_EXECUTION + ENABLE_LIVE_TRADING + 운영자 명시 opt-in + "
            "사전 정의 범위 (notional/symbol/window/daily count) + canary "
            "운용 후에만 활성화됩니다."
        ),
    }


def build_default_blocked_policy() -> AIExecutionPolicy:
    """기본 정책 helper — 모든 boolean opt-in이 False, whitelist 비어 있음.

    `evaluate_ai_execution(any_input, build_default_blocked_policy())`은
    *어떤 입력에서도* BLOCKED를 반환한다 (테스트 invariant).
    """
    return AIExecutionPolicy()


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / app.brokers / app.execution
#   어떤 모듈도 import하지 않는다. 순수 의사결정 함수.
# - API key / 계좌번호 / Secret을 함수 인자로 받지 않는다 (#39 invariant 상속).
# - 본 PR에서 ENABLE_AI_EXECUTION / ENABLE_LIVE_TRADING을 True로 set하는 코드 0건.
# - 정책 default는 BLOCKED이 보장 — `test_ai_execution_gate.py`로 강제.
