"""P-03: 최대 동시 보유 종목 수 제한 가드 — pure helper.

`PaperCapitalConfig.max_concurrent_positions` (P-03) 기준으로 신규 BUY 의 동시
보유 한도 초과 여부를 *advisory* 로 평가하는 순수 함수 모듈.

설계 원칙:
- 본 모듈은 broker / OrderExecutor / route_order / DB / 외부 HTTP / AI SDK
  import 0건 — pure helper.
- 본 가드는 *advisory* — 실 주문 차단은 RiskManager / route_order 단계에서.
  본 PR 은 *결과 dataclass 반환* 만, 호출 흐름은 caller (Paper consumer 등)
  가 책임.
- HOLD / SELL / EXIT 는 *제한하지 않는다* — Paper 포지션을 *닫는* 방향은
  보유 종목 수 한도와 무관. BUY (또는 신규 진입) 만 차단 대상.
- 같은 종목 *추가 매수* 는 *기존 보유 종목 수에 포함되어 있으므로* 차단 X
  (한도 = 보유 *고유* 종목 수 기준).
- `EMERGENCY_STOP` 같은 loop 상태는 본 가드 *밖* 에서 처리 — 본 함수는
  capital cap 만 본다.

CLAUDE.md 절대 원칙 (정적 grep + AST 가드, 테스트로 lock):
- broker / OrderExecutor / route_order 어떤 호출 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (안전 flag 와 결합 0건)
- app.brokers / app.execution / app.kis_paper.engine import 0건
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Verdict + result dataclass
# ============================================================================


class ConcurrentBuyVerdict(StrEnum):
    """가드 평가 결과 라벨.

    - ALLOW                    — 신규 BUY 진입 가능 (보유 수 < 한도, 또는
      추가 매수 = 같은 종목 carry).
    - BLOCKED_MAX_POSITIONS    — 보유 *고유* 종목 수가 한도 이상이고 *신규*
      종목 BUY → 차단 권고.
    - SKIP_NON_BUY             — action 이 BUY 가 아님 — 본 가드 무관 (SELL /
      EXIT / HOLD / WATCH 등).
    """
    ALLOW                 = "ALLOW"
    BLOCKED_MAX_POSITIONS = "BLOCKED_MAX_POSITIONS"
    SKIP_NON_BUY          = "SKIP_NON_BUY"


@dataclass(frozen=True)
class ConcurrentBuyCheckResult:
    """advisory 평가 결과 — broker / route_order 호출 0건.

    `is_order_signal=False` / `is_live_authorization=False` 영구 — 본 결과는
    Paper Auto Loop 가 *참고* 하는 advisory 데이터이지 실 주문 트리거가
    아니다.
    """

    verdict:                     ConcurrentBuyVerdict
    current_unique_symbol_count: int        # 입력 시점 *고유* 보유 종목 수
    max_concurrent_positions:    int        # 적용된 한도
    symbol:                      str | None = None
    action:                      str | None = None    # caller-provided
    reason:                      str        = ""
    is_existing_position:        bool       = False   # 추가 매수 케이스
    metadata:                    dict[str, Any] = field(default_factory=dict)

    is_order_signal:             bool = False
    is_live_authorization:       bool = False
    is_paper_only:               bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("ConcurrentBuyCheckResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("ConcurrentBuyCheckResult.is_live_authorization must be False")
        if self.is_paper_only is not True:
            raise ValueError("ConcurrentBuyCheckResult.is_paper_only must be True")
        if self.max_concurrent_positions < 0:
            raise ValueError(
                f"max_concurrent_positions must be >= 0, got {self.max_concurrent_positions}"
            )
        if self.current_unique_symbol_count < 0:
            raise ValueError(
                f"current_unique_symbol_count must be >= 0, "
                f"got {self.current_unique_symbol_count}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                     self.verdict.value,
            "current_unique_symbol_count": int(self.current_unique_symbol_count),
            "max_concurrent_positions":    int(self.max_concurrent_positions),
            "symbol":                      self.symbol,
            "action":                      self.action,
            "reason":                      self.reason,
            "is_existing_position":        self.is_existing_position,
            "metadata":                    dict(self.metadata),
            "is_order_signal":             self.is_order_signal,
            "is_live_authorization":       self.is_live_authorization,
            "is_paper_only":               self.is_paper_only,
        }


# ============================================================================
# Public helper
# ============================================================================


# action 정규화 — caller 가 어떤 표현을 써도 받아들이도록 lower-case 비교.
_BUY_TOKENS = frozenset({"buy", "open", "open_long", "long", "enter", "entry"})
_NON_BLOCKING_TOKENS = frozenset({
    "sell", "exit", "close", "close_long", "hold", "watch", "no_signal",
    "reject", "skip",
})


def _normalize_action(action: str | None) -> str:
    return (action or "").strip().lower()


def _is_buy_action(action: str | None) -> bool:
    return _normalize_action(action) in _BUY_TOKENS


def check_concurrent_buy_allowed(
    *,
    action:                      str | None,
    symbol:                      str | None,
    current_held_symbols:        list[str] | tuple[str, ...] | set[str] | None,
    max_concurrent_positions:    int,
) -> ConcurrentBuyCheckResult:
    """신규 BUY 가 허용되는지 *advisory* 로 평가.

    Args:
        action: caller 의 매매 의도 — 대소문자 / 공백 무시. "buy" 일 때만
            한도 체크. 그 외 (SELL / EXIT / HOLD / ...) → SKIP_NON_BUY.
        symbol: 신규 진입 후보 종목 코드 — 같은 종목 추가 매수면 보유 수에
            카운트되지 않으므로 ALLOW.
        current_held_symbols: 현재 *보유* 중인 고유 종목 코드 컬렉션. None /
            빈 컬렉션이면 0건 보유로 간주.
        max_concurrent_positions: 적용 한도 — `PaperCapitalConfig.
            max_concurrent_positions` 그대로 전달 권장.

    Returns:
        ConcurrentBuyCheckResult.

    Notes:
        - HOLD / SELL / EXIT 는 한도와 무관 (SKIP_NON_BUY).
        - 추가 매수 (symbol in current_held_symbols) 는 ALLOW + is_existing_position=True.
        - max_concurrent_positions=0 은 *어떤* 신규 BUY 도 BLOCKED (운영자
          명시 사용자 설정 시).
    """
    held: set[str] = set()
    if current_held_symbols:
        held = {str(s) for s in current_held_symbols if s}
    unique_count = len(held)

    # 1. BUY 가 아닌 모든 action → 가드 무관.
    if not _is_buy_action(action):
        return ConcurrentBuyCheckResult(
            verdict=ConcurrentBuyVerdict.SKIP_NON_BUY,
            current_unique_symbol_count=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            symbol=symbol,
            action=action,
            reason="action is not BUY — concurrent positions guard skipped",
        )

    # 2. 추가 매수 — 보유 종목 수 *변화 없음*. ALLOW.
    if symbol and symbol in held:
        return ConcurrentBuyCheckResult(
            verdict=ConcurrentBuyVerdict.ALLOW,
            current_unique_symbol_count=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            symbol=symbol,
            action=action,
            reason="existing position — adding to held symbol does not change unique count",
            is_existing_position=True,
        )

    # 3. 신규 종목 BUY — unique_count + 1 이 한도 이하인지.
    target_count = unique_count + 1
    if target_count > int(max_concurrent_positions):
        return ConcurrentBuyCheckResult(
            verdict=ConcurrentBuyVerdict.BLOCKED_MAX_POSITIONS,
            current_unique_symbol_count=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            symbol=symbol,
            action=action,
            reason=(
                f"max_concurrent_positions={max_concurrent_positions} 한도 도달 — "
                f"신규 종목 BUY 차단 (current_unique={unique_count})"
            ),
        )

    return ConcurrentBuyCheckResult(
        verdict=ConcurrentBuyVerdict.ALLOW,
        current_unique_symbol_count=unique_count,
        max_concurrent_positions=int(max_concurrent_positions),
        symbol=symbol,
        action=action,
        reason=(
            f"신규 종목 BUY 허용 (current_unique={unique_count} < "
            f"{max_concurrent_positions})"
        ),
    )


__all__ = [
    "ConcurrentBuyVerdict",
    "ConcurrentBuyCheckResult",
    "check_concurrent_buy_allowed",
]
