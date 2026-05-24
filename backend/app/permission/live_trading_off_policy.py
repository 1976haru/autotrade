"""#70 / 9-01: 실매매 기본 OFF 정책 (read-only, advisory).

실전매매는 *기본값에서 절대 자동 시작되지 않는다*. 본 모듈은 현재 안전 플래그를
입력으로 받아 "실전 주문은 기본 차단(LIVE_PATH_GATED)" 상태임을 *판정/표시* 한다.
어떤 환경변수 하나만으로도 실전 주문을 허용하지 않는다 — 별도 Live Capital Review
(#41/#72) + Manual Approval(#42) + Canary(#43) + Audit(#45) 를 모두 통과해야 한다.

**본 모듈은 실전을 *켜지 않는다*** — `is_live_authorization` / `broker_order_sent` /
`order_created` 는 항상 False. broker / OrderExecutor / route_order 호출 0건.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건, DB write 0건, 외부 HTTP / AI SDK import 0건.
- `app.core.config.get_settings` import 0건 (현재값을 *입력 DTO* 로 받음 — 실제값↔
  입력값 혼선 방지).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# reason_code (요청서 §2 고정).
LIVE_TRADING_DISABLED_BY_DEFAULT = "LIVE_TRADING_DISABLED_BY_DEFAULT"
LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT = "LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT"
LIVE_PATH_GATED = "LIVE_PATH_GATED"
LIVE_ORDER_BLOCKED_BY_DEFAULT = "LIVE_ORDER_BLOCKED_BY_DEFAULT"
LIVE_REQUIRES_EXPLICIT_APPROVAL = "LIVE_REQUIRES_EXPLICIT_APPROVAL"
LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE = "LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE"

_MESSAGES = {
    LIVE_ORDER_BLOCKED_BY_DEFAULT:
        "실전 주문은 기본값에서 차단되어 있습니다. 별도 승인 Gate 없이는 불가합니다.",
    LIVE_REQUIRES_EXPLICIT_APPROVAL:
        "실전은 Live Capital Review + Manual Approval + Canary + Audit 를 모두 통과해야 합니다.",
    LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE:
        "기본 EXE 실행에서 실전 주문은 불가능합니다 (실전 기본 OFF).",
}


@dataclass(frozen=True)
class LiveOffPolicyResult:
    """실매매 기본 OFF 정책 판정 — UI 안전 payload. 실전 활성화 아님."""

    enable_live_trading:         bool
    enable_ai_execution:         bool
    enable_futures_live_trading: bool
    kis_is_paper:                bool
    default_mode:                str

    live_path_gated:    bool
    live_order_blocked: bool
    reason_code:        str
    reason_codes:       tuple[str, ...]
    message_ko:         str

    # 불변 — 본 모듈은 실전을 절대 허가하지 않는다.
    is_live_authorization: bool = False
    broker_order_sent:     bool = False
    order_created:         bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_live_authorization", "broker_order_sent",
                     "order_created", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (실매매 기본 OFF)")
        # 정책 불변: 실전 경로는 항상 gated.
        if self.live_path_gated is not True:
            raise ValueError("live_path_gated must be True (default-OFF policy)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "enable_live_trading":         bool(self.enable_live_trading),
            "enable_ai_execution":         bool(self.enable_ai_execution),
            "enable_futures_live_trading": bool(self.enable_futures_live_trading),
            "kis_is_paper":                bool(self.kis_is_paper),
            "default_mode":                self.default_mode,
            "live_path_gated":             True,
            "live_order_blocked":          bool(self.live_order_blocked),
            "reason_code":                 self.reason_code,
            "reason_codes":                list(self.reason_codes),
            "message_ko":                  self.message_ko,
            "is_live_authorization":       False,
            "broker_order_sent":           False,
            "order_created":               False,
            "contains_secret":             False,
        }


def evaluate_live_off_policy(
    *,
    enable_live_trading: bool = False,
    enable_ai_execution: bool = False,
    enable_futures_live_trading: bool = False,
    kis_is_paper: bool = True,
    default_mode: str = "SIMULATION",
) -> LiveOffPolicyResult:
    """현재 안전 플래그 → 실매매 기본 OFF 판정.

    어떤 flag 조합이든 본 모듈은 실전을 허가하지 않는다(live_path_gated=True).
    reason_codes 는 *왜 차단/주의* 인지 누적해 표시한다.
    """
    codes: list[str] = [LIVE_ORDER_BLOCKED_BY_DEFAULT, LIVE_PATH_GATED,
                        LIVE_REQUIRES_EXPLICIT_APPROVAL]
    if not enable_live_trading:
        codes.append(LIVE_TRADING_DISABLED_BY_DEFAULT)
    if not enable_ai_execution:
        codes.append(LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT)
    # 모든 안전 플래그가 기본값(안전)이면 "기본 EXE 에서 실전 불가" 명시.
    if (not enable_live_trading and not enable_ai_execution
            and not enable_futures_live_trading and kis_is_paper):
        codes.append(LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE)

    return LiveOffPolicyResult(
        enable_live_trading=bool(enable_live_trading),
        enable_ai_execution=bool(enable_ai_execution),
        enable_futures_live_trading=bool(enable_futures_live_trading),
        kis_is_paper=bool(kis_is_paper),
        default_mode=str(default_mode),
        live_path_gated=True,
        live_order_blocked=True,
        reason_code=LIVE_ORDER_BLOCKED_BY_DEFAULT,
        reason_codes=tuple(dict.fromkeys(codes)),   # 순서보존 dedup.
        message_ko=_MESSAGES[LIVE_ORDER_BLOCKED_BY_DEFAULT],
    )


__all__ = [
    "LIVE_TRADING_DISABLED_BY_DEFAULT", "LIVE_AI_EXECUTION_DISABLED_BY_DEFAULT",
    "LIVE_PATH_GATED", "LIVE_ORDER_BLOCKED_BY_DEFAULT",
    "LIVE_REQUIRES_EXPLICIT_APPROVAL", "LIVE_NOT_AVAILABLE_IN_DEFAULT_EXE",
    "LiveOffPolicyResult", "evaluate_live_off_policy",
]
