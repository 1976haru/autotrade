"""AI Permission Gate (#39).

AI 주문 권한을 단계별로 명확히 분리한다.

5 단계 (`AiPermissionLevel`):
- `FULL_STOP`            — AI 완전 중지 (kill switch / disable_ai_orders).
- `RECOMMEND_ONLY`       — AI는 신호/추천만, 주문 흐름 진입 X.
- `APPROVAL_REQUIRED`    — AI가 NEEDS_APPROVAL 큐에 제안, 운영자가 승인.
- `VIRTUAL_EXECUTION`    — AI가 가상(virtual broker)에서 자동 실행.
- `LIMITED_LIVE_EXECUTION` — AI가 실거래 실행 가능 (제한적, 모든 가드 통과 시).

5 행동 (`AiAction`):
- `RECOMMEND`             — 단순 신호 / 추천 생성.
- `SUBMIT_FOR_APPROVAL`   — 운영자 승인 큐에 주문 제안.
- `VIRTUAL_EXECUTE`       — 가상 broker로 자동 실행.
- `LIVE_EXECUTE`          — 실 broker 호출.
- `FUTURES_LIVE_EXECUTE`  — 선물 실거래 (별도 flag 필요).

본 모듈은 *권한 판정만* 한다. 실제 RiskManager / PermissionGate /
OrderExecutor / route_order 흐름은 변경하지 않는다 — 기존 검사
(`disable_ai_orders`, `min_ai_confidence`, `enforce_ai_reasoning`,
`enable_ai_execution`, `enable_live_trading`)는 그대로 유지. 본 gate는
*advisory + 명시 권한 표시* 레이어로, UI/Agent/audit가 "어떤 mode에서 어떤
AI 행동이 허용되는지" 한 곳에서 조회 가능하게 한다.

**중요한 보안 invariant**:
- AI API Key는 권한 조건이 아니다 — 본 모듈은 API key를 입력으로 받지 않는다.
- API Key 보유 != 주문 권한. 권한은 mode + flags + operator approval로만 결정.
- AI Agent는 broker / OrderExecutor를 import하지 않는다 (CLAUDE.md 절대 원칙 5/6).
- 본 모듈도 broker / OrderExecutor / route_order 호출 0건 (테스트 가드).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.core.modes import OperationMode


class AiPermissionLevel(StrEnum):
    """현재 AI에게 주어진 최대 권한 레벨."""
    FULL_STOP              = "FULL_STOP"
    RECOMMEND_ONLY         = "RECOMMEND_ONLY"
    APPROVAL_REQUIRED      = "APPROVAL_REQUIRED"
    VIRTUAL_EXECUTION      = "VIRTUAL_EXECUTION"
    LIMITED_LIVE_EXECUTION = "LIMITED_LIVE_EXECUTION"


class AiAction(StrEnum):
    """AI가 수행하려는 구체적 행동. evaluate가 mode/flags와 결합해 판정."""
    RECOMMEND            = "RECOMMEND"
    SUBMIT_FOR_APPROVAL  = "SUBMIT_FOR_APPROVAL"
    VIRTUAL_EXECUTE      = "VIRTUAL_EXECUTE"
    LIVE_EXECUTE         = "LIVE_EXECUTE"
    FUTURES_LIVE_EXECUTE = "FUTURES_LIVE_EXECUTE"


@dataclass(frozen=True)
class AiPermissionFlags:
    """평가에 필요한 모든 flag 묶음. *API Key는 포함하지 않는다*.

    필드:
    - `enable_live_trading` — env `ENABLE_LIVE_TRADING`. LIVE 모드 진입 가드.
    - `enable_ai_execution` — env `ENABLE_AI_EXECUTION`. LIVE_AI_EXECUTION 가드.
    - `enable_futures_live_trading` — env `ENABLE_FUTURES_LIVE_TRADING`.
    - `emergency_stop` — RiskManager kill switch (LEVEL_1+).
    - `disable_ai_orders` — RiskPolicy AI-only kill switch (#178).

    호출자는 RiskManager 인스턴스 / Settings에서 채워서 넘긴다.
    """
    enable_live_trading:          bool = False
    enable_ai_execution:          bool = False
    enable_futures_live_trading:  bool = False
    emergency_stop:               bool = False
    disable_ai_orders:            bool = False


@dataclass(frozen=True)
class AiPermissionDecision:
    """`evaluate_ai_permission`의 결과.

    `allowed=True`이면 reasons는 비어 있다. `audit_note`는 audit row에 carry할
    수 있는 사람이 읽는 한 줄 요약.
    """
    allowed:    bool
    level:      AiPermissionLevel
    action:     AiAction
    mode:       OperationMode
    reasons:    list[str] = field(default_factory=list)
    audit_note: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed":     self.allowed,
            "level":       self.level.value,
            "action":      self.action.value,
            "mode":        self.mode.value,
            "reasons":     list(self.reasons),
            "audit_note":  self.audit_note,
        }


# ====================================================================
# 권한 매트릭스 — mode → 최대 허용 level
# ====================================================================
#
# 운영자 / Agent / UI가 한눈에 매트릭스를 본다. 운영자가 universe별 더
# 보수적인 정책을 원하면 향후 옵트인 PR에서 override 가능 (현재는 fixed).

_MODE_MAX_LEVEL: dict[OperationMode, AiPermissionLevel] = {
    OperationMode.SIMULATION:           AiPermissionLevel.RECOMMEND_ONLY,
    OperationMode.PAPER:                AiPermissionLevel.RECOMMEND_ONLY,
    OperationMode.LIVE_SHADOW:          AiPermissionLevel.RECOMMEND_ONLY,
    # LIVE_MANUAL_APPROVAL은 *운영자 수동 주문* 모드 — AI 추천도 차단.
    OperationMode.LIVE_MANUAL_APPROVAL: AiPermissionLevel.FULL_STOP,
    OperationMode.LIVE_AI_ASSIST:       AiPermissionLevel.APPROVAL_REQUIRED,
    OperationMode.VIRTUAL_AI_EXECUTION: AiPermissionLevel.VIRTUAL_EXECUTION,
    OperationMode.LIVE_AI_EXECUTION:    AiPermissionLevel.LIMITED_LIVE_EXECUTION,
}


# 각 level이 허용하는 action 집합. level별 *cumulative*가 아닌 *exact* —
# RECOMMEND_ONLY는 RECOMMEND만 허용, VIRTUAL_EXECUTION은 RECOMMEND + VIRTUAL_EXECUTE
# 허용 (SUBMIT_FOR_APPROVAL은 별도 모드).
_LEVEL_ALLOWED_ACTIONS: dict[AiPermissionLevel, set[AiAction]] = {
    AiPermissionLevel.FULL_STOP: set(),
    AiPermissionLevel.RECOMMEND_ONLY: {
        AiAction.RECOMMEND,
    },
    AiPermissionLevel.APPROVAL_REQUIRED: {
        AiAction.RECOMMEND,
        AiAction.SUBMIT_FOR_APPROVAL,
    },
    AiPermissionLevel.VIRTUAL_EXECUTION: {
        AiAction.RECOMMEND,
        AiAction.VIRTUAL_EXECUTE,
    },
    AiPermissionLevel.LIMITED_LIVE_EXECUTION: {
        AiAction.RECOMMEND,
        AiAction.SUBMIT_FOR_APPROVAL,
        AiAction.VIRTUAL_EXECUTE,
        AiAction.LIVE_EXECUTE,
        # FUTURES_LIVE_EXECUTE는 enable_futures_live_trading flag가 별도 통과해야 함.
        AiAction.FUTURES_LIVE_EXECUTE,
    },
}


def current_ai_level(
    mode: OperationMode,
    flags: AiPermissionFlags,
) -> AiPermissionLevel:
    """flags + mode → 현재 AI에게 *실제로* 주어진 권한 level.

    1. emergency_stop / disable_ai_orders → FULL_STOP (mode 무관).
    2. mode의 max level이 RECOMMEND_ONLY 이상이지만 enable_ai_execution=False면
       LIMITED_LIVE_EXECUTION → APPROVAL_REQUIRED로 강등 (live execution 차단).
    3. mode의 max level이 LIMITED_LIVE_EXECUTION이지만 enable_live_trading=False면
       역시 APPROVAL_REQUIRED로 강등.
    4. 그 외엔 mode의 max level 그대로.
    """
    if flags.emergency_stop or flags.disable_ai_orders:
        return AiPermissionLevel.FULL_STOP
    base = _MODE_MAX_LEVEL.get(mode, AiPermissionLevel.FULL_STOP)
    if base == AiPermissionLevel.LIMITED_LIVE_EXECUTION:
        if not flags.enable_live_trading or not flags.enable_ai_execution:
            return AiPermissionLevel.APPROVAL_REQUIRED
    return base


def evaluate_ai_permission(
    *,
    action: AiAction,
    mode:   OperationMode,
    flags:  AiPermissionFlags,
) -> AiPermissionDecision:
    """`action`이 현재 mode + flags에서 허용되는지 판정.

    반환:
    - `allowed=True` + 빈 reasons → 통과.
    - `allowed=False` + reasons → 차단 사유.

    `audit_note`는 항상 채워 audit row / UI surface에 그대로 carry 가능.
    """
    level = current_ai_level(mode, flags)
    allowed_actions = _LEVEL_ALLOWED_ACTIONS[level]
    reasons: list[str] = []
    allowed = action in allowed_actions

    # 분기별 사유.
    if level == AiPermissionLevel.FULL_STOP:
        if flags.emergency_stop:
            reasons.append("emergency_stop is enabled — all AI actions blocked")
        elif flags.disable_ai_orders:
            reasons.append("AI orders disabled by operator kill-switch")
        else:
            reasons.append(
                f"mode {mode.value} does not permit AI actions (FULL_STOP)"
            )

    if not allowed and level != AiPermissionLevel.FULL_STOP:
        reasons.append(
            f"action {action.value} not in level {level.value} "
            f"allowed_actions={sorted(a.value for a in allowed_actions)}"
        )

    # FUTURES_LIVE_EXECUTE는 별도 flag 추가 검사. 본 PR 시점 `enable_futures_
    # live_trading=False`(default)이므로 항상 차단.
    if action == AiAction.FUTURES_LIVE_EXECUTE and not flags.enable_futures_live_trading:
        allowed = False
        reasons.append(
            "futures live trading is disabled by ENABLE_FUTURES_LIVE_TRADING flag"
        )

    audit_note = (
        f"AI permission OK: mode={mode.value}, level={level.value}, action={action.value}"
        if allowed else
        f"AI permission BLOCKED: mode={mode.value}, level={level.value}, "
        f"action={action.value} — {'; '.join(reasons)}"
    )

    return AiPermissionDecision(
        allowed=allowed,
        level=level,
        action=action,
        mode=mode,
        reasons=reasons,
        audit_note=audit_note,
    )


# ====================================================================
# Matrix surface — UI / API용 직렬화
# ====================================================================


def build_permission_matrix() -> dict:
    """모든 mode × action 조합의 default 허용 여부 (flags가 default일 때).

    flags가 default(`AiPermissionFlags()`)이면 enable_live_trading=False +
    enable_ai_execution=False — LIMITED_LIVE_EXECUTION이 APPROVAL_REQUIRED로
    강등된 보수적 매트릭스를 얻는다. 운영자가 LIVE 활성화 후 매트릭스가
    어떻게 변하는지는 별도로 조회.
    """
    default_flags = AiPermissionFlags()
    out: dict[str, dict[str, bool]] = {}
    for mode in OperationMode:
        out[mode.value] = {}
        for action in AiAction:
            r = evaluate_ai_permission(action=action, mode=mode, flags=default_flags)
            out[mode.value][action.value] = r.allowed
    return out


def build_status(
    *,
    mode: OperationMode,
    flags: AiPermissionFlags,
) -> dict:
    """현재 AI 상태 + 매트릭스 + 안내문구. UI / API 응답으로 사용."""
    level = current_ai_level(mode, flags)
    allowed_actions = sorted(a.value for a in _LEVEL_ALLOWED_ACTIONS[level])
    blocked_actions = sorted(
        a.value for a in AiAction if a not in _LEVEL_ALLOWED_ACTIONS[level]
    )
    requires_approval = (level == AiPermissionLevel.APPROVAL_REQUIRED)
    virtual_only      = (level == AiPermissionLevel.VIRTUAL_EXECUTION)
    return {
        "mode":             mode.value,
        "level":            level.value,
        "allowed_actions":  allowed_actions,
        "blocked_actions":  blocked_actions,
        "requires_human_approval": requires_approval,
        "virtual_only":     virtual_only,
        "live_execution_disabled": (
            level != AiPermissionLevel.LIMITED_LIVE_EXECUTION
        ),
        "futures_live_disabled":   not flags.enable_futures_live_trading,
        "flags":            {
            "enable_live_trading":         flags.enable_live_trading,
            "enable_ai_execution":         flags.enable_ai_execution,
            "enable_futures_live_trading": flags.enable_futures_live_trading,
            "emergency_stop":              flags.emergency_stop,
            "disable_ai_orders":           flags.disable_ai_orders,
        },
        "matrix":           build_permission_matrix(),
        "notice": (
            "AI API Key는 주문 권한이 아닙니다. 권한은 운용모드 + 안전 flag + "
            "운영자 승인으로만 결정됩니다."
        ),
    }


# ====================================================================
# Module invariants (코드 단 안전 보장)
# ====================================================================
#
# 본 모듈은 broker / OrderExecutor / route_order / app.brokers.* 어떤 모듈도
# import하지 않는다. AI API Key / 계좌번호 / Secret 어떤 형태로도 입력으로
# 받지 않는다 — `AiPermissionFlags`에 명시된 boolean flag만이 결정 인자.
# 본 invariant는 tests/test_ai_permission_gate.py가 grep으로 강제.
