"""#2-10: AI Paper 자동매수/매도 skeleton — AI 판단 → Paper ledger 변환.

AI Agent 의 *advisory recommendation* (방향 + 사유 + confidence) 을 받아
Paper 가상 체결 ledger 에 기록 가능한 `PaperDecision` 으로 변환하고,
`backend/app/auto_paper/ledger.py` 의 in-memory ring 에 append 한다.

## 핵심 변환 규칙

| AI direction | current_position | 결과 action | virtual_delta |
|---|---|---|---|
| BUY  | 0       | `BUY`    | +size (skeleton=1) |
| BUY  | > 0     | `HOLD`   | 0 (이미 보유 — 중복 매수 회피) |
| SELL | 0       | `HOLD`   | 0 (보유 없음 — 매도 불가) |
| SELL | > 0     | `SELL`   | -size (skeleton=1) |
| EXIT | > 0     | `EXIT`   | -current_position (전량 청산) |
| EXIT | 0       | `NO_OP`  | 0 (청산할 포지션 없음) |
| HOLD | any     | `HOLD`   | 0 |
| NO_OP| any     | `NO_OP`  | 0 |

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 변환 / 기록은 *실 broker 호출 0건*** — `PaperDecision.is_order_signal=
   False` 불변 (`__post_init__` ValueError).
2. **자동 적용 0건** — `auto_apply_allowed=False`.
3. **실거래 허가 0건** — `is_live_authorization=False`.
4. **broker / OrderExecutor / route_order import 0건** — 정적 grep 가드.
5. **외부 HTTP / AI SDK / Anthropic / OpenAI import 0건** — 본 모듈은 *결정론적*
   변환기. AI 판단 *생성* 은 다른 모듈 (4-02 / 4-03 / 4-04) 의 책임.
6. **DB write 0건** — ledger 만 (in-memory).
7. **paper_fill_status 정책**: skeleton 은 *즉시 PAPER_FILLED* — 가상 체결은
   slippage / latency 모델링 없음. 후속 PR 에서 PAPER_PENDING → PAPER_FILLED
   2-step 흐름 + 부분체결 / 슬리피지 시뮬레이션 추가 가능.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.auto_paper.events import (
    DecisionAction,
    PaperFillStatus,
    PaperLoopEvent,
)
from app.auto_paper.ledger import record_paper_event


# skeleton 의 가상 주문 size — 후속 PR 에서 strategy sizing hint 사용.
DEFAULT_VIRTUAL_TRADE_SIZE = 1


# ─────────────────────────────────────────────────────────────────────────────
# Direction enum — AI 판단이 caller 에서 들어올 때 사용
# ─────────────────────────────────────────────────────────────────────────────


# `DecisionAction` 과 동일 라벨이지만 *입력 의미* (caller intent) 만 표현.
# 변환기가 current_position 과 결합해 실제 ledger action 을 결정한다.
class AIDirection:
    BUY    = "BUY"
    SELL   = "SELL"
    EXIT   = "EXIT"
    HOLD   = "HOLD"
    NO_OP  = "NO_OP"


_VALID_DIRECTIONS: frozenset[str] = frozenset({
    AIDirection.BUY, AIDirection.SELL, AIDirection.EXIT,
    AIDirection.HOLD, AIDirection.NO_OP,
})


# ─────────────────────────────────────────────────────────────────────────────
# Input dataclass — caller (paper_tick_handler 등) 가 채워서 전달
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AIRecommendationInput:
    """AI Agent 가 caller 에게 전달하는 *advisory recommendation* 입력.

    본 dataclass 는 *주문 요청 객체가 아니다* — `is_order_signal=False` 영구.
    caller (예: `paper_tick_handler`) 가 본 객체를 `convert_to_paper_decision()`
    에 넘기면 변환기가 ledger 기록까지 수행.
    """

    strategy:         str
    symbol:           str
    direction:        str             # AIDirection 값
    reason:           str
    confidence:       float | None    = None
    risk_flags:       list[str]       = field(default_factory=list)
    params:           dict[str, Any]  = field(default_factory=dict)
    current_position: int             = 0    # 가상 보유 — caller 가 카운터 관리
    metadata:         dict[str, Any]  = field(default_factory=dict)

    # invariant.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("AIRecommendationInput.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("AIRecommendationInput.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("AIRecommendationInput.is_live_authorization must be False.")
        if self.direction not in _VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(_VALID_DIRECTIONS)}, "
                f"got {self.direction!r}"
            )
        if not self.strategy or not self.symbol:
            raise ValueError("strategy / symbol must be non-empty.")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1] or None, got {self.confidence}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Output dataclass — 변환 결과 (ledger 기록 *전* state)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperDecision:
    """변환 결과 — *advisory paper 결정*. 실 broker 호출 0건.

    `action` 은 *실제로 ledger 에 들어갈* DecisionAction 값. 입력 direction 과
    다를 수 있음 (예: BUY 입력이지만 이미 보유 중 → HOLD 로 변환).
    """

    strategy:               str
    symbol:                 str
    action:                 DecisionAction
    confidence:             float | None
    reason:                 str
    risk_flags:             list[str]            = field(default_factory=list)
    paper_order_id:         str | None           = None
    paper_fill_status:      PaperFillStatus      = PaperFillStatus.NA
    virtual_position_delta: int                  = 0
    pnl_estimate:           float                = 0.0
    # 입력 direction 보존 — caller 가 변환 결과 vs 원입력 비교 가능.
    source_direction:       str                  = AIDirection.HOLD
    metadata:               dict[str, Any]       = field(default_factory=dict)

    # invariant.
    is_order_signal:        bool = False
    auto_apply_allowed:     bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("PaperDecision.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("PaperDecision.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("PaperDecision.is_live_authorization must be False.")
        if not isinstance(self.action, DecisionAction):
            raise ValueError("action must be DecisionAction.")
        if not isinstance(self.paper_fill_status, PaperFillStatus):
            raise ValueError("paper_fill_status must be PaperFillStatus.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":                self.strategy,
            "symbol":                  self.symbol,
            "action":                  self.action.value,
            "confidence":              self.confidence,
            "reason":                  self.reason,
            "risk_flags":              list(self.risk_flags),
            "paper_order_id":          self.paper_order_id,
            "paper_fill_status":       self.paper_fill_status.value,
            "virtual_position_delta":  int(self.virtual_position_delta),
            "pnl_estimate":            float(self.pnl_estimate),
            "source_direction":        self.source_direction,
            "metadata":                dict(self.metadata),
            # 절대 invariant.
            "is_order_signal":         False,
            "auto_apply_allowed":      False,
            "is_live_authorization":   False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 변환기 — 결정론적 휴리스틱
# ─────────────────────────────────────────────────────────────────────────────


def _new_paper_order_id() -> str:
    return f"paper-{uuid.uuid4().hex[:12]}"


def convert_to_paper_decision(
    rec: AIRecommendationInput,
    *,
    virtual_trade_size: int = DEFAULT_VIRTUAL_TRADE_SIZE,
    auto_fill: bool = True,
) -> PaperDecision:
    """AI recommendation → PaperDecision (ledger 기록 *전*).

    Args:
        rec:                AI 입력.
        virtual_trade_size: BUY/SELL 의 가상 size (skeleton default 1).
        auto_fill:          True 면 BUY/SELL/EXIT 가 즉시 `PAPER_FILLED`,
                            False 면 `PAPER_PENDING` (후속 PR 에서 2-step).

    *broker 호출 0건* — 본 함수는 dataclass 변환만.
    """
    direction = rec.direction
    pos = int(rec.current_position)
    size = max(1, int(virtual_trade_size))
    fill_status: PaperFillStatus = (
        PaperFillStatus.PAPER_FILLED if auto_fill else PaperFillStatus.PAPER_PENDING
    )

    # 매핑 표 (docstring 참조).
    if direction == AIDirection.BUY:
        if pos > 0:
            # 이미 보유 — 중복 매수 회피 → HOLD 로 변환.
            return PaperDecision(
                strategy=rec.strategy, symbol=rec.symbol,
                action=DecisionAction.HOLD,
                confidence=rec.confidence,
                reason=f"BUY suppressed (already holding {pos}); {rec.reason}",
                risk_flags=list(rec.risk_flags),
                paper_order_id=None,
                paper_fill_status=PaperFillStatus.NA,
                virtual_position_delta=0,
                source_direction=direction,
                metadata=dict(rec.metadata),
            )
        return PaperDecision(
            strategy=rec.strategy, symbol=rec.symbol,
            action=DecisionAction.BUY,
            confidence=rec.confidence,
            reason=rec.reason,
            risk_flags=list(rec.risk_flags),
            paper_order_id=_new_paper_order_id(),
            paper_fill_status=fill_status,
            virtual_position_delta=size,
            source_direction=direction,
            metadata=dict(rec.metadata),
        )

    if direction == AIDirection.SELL:
        if pos <= 0:
            # 보유 없음 — 매도 불가 → HOLD audit.
            return PaperDecision(
                strategy=rec.strategy, symbol=rec.symbol,
                action=DecisionAction.HOLD,
                confidence=rec.confidence,
                reason=f"SELL suppressed (no position, pos={pos}); {rec.reason}",
                risk_flags=list(rec.risk_flags),
                paper_order_id=None,
                paper_fill_status=PaperFillStatus.NA,
                virtual_position_delta=0,
                source_direction=direction,
                metadata=dict(rec.metadata),
            )
        return PaperDecision(
            strategy=rec.strategy, symbol=rec.symbol,
            action=DecisionAction.SELL,
            confidence=rec.confidence,
            reason=rec.reason,
            risk_flags=list(rec.risk_flags),
            paper_order_id=_new_paper_order_id(),
            paper_fill_status=fill_status,
            virtual_position_delta=-min(size, pos),
            source_direction=direction,
            metadata=dict(rec.metadata),
        )

    if direction == AIDirection.EXIT:
        if pos <= 0:
            # 청산할 포지션 없음 — NO_OP audit.
            return PaperDecision(
                strategy=rec.strategy, symbol=rec.symbol,
                action=DecisionAction.NO_OP,
                confidence=rec.confidence,
                reason=f"EXIT suppressed (no position, pos={pos}); {rec.reason}",
                risk_flags=list(rec.risk_flags),
                paper_order_id=None,
                paper_fill_status=PaperFillStatus.NA,
                virtual_position_delta=0,
                source_direction=direction,
                metadata=dict(rec.metadata),
            )
        return PaperDecision(
            strategy=rec.strategy, symbol=rec.symbol,
            action=DecisionAction.EXIT,
            confidence=rec.confidence,
            reason=rec.reason,
            risk_flags=list(rec.risk_flags),
            paper_order_id=_new_paper_order_id(),
            paper_fill_status=fill_status,
            virtual_position_delta=-pos,    # 전량 청산.
            source_direction=direction,
            metadata=dict(rec.metadata),
        )

    if direction == AIDirection.HOLD:
        return PaperDecision(
            strategy=rec.strategy, symbol=rec.symbol,
            action=DecisionAction.HOLD,
            confidence=rec.confidence,
            reason=rec.reason,
            risk_flags=list(rec.risk_flags),
            paper_order_id=None,
            paper_fill_status=PaperFillStatus.NA,
            virtual_position_delta=0,
            source_direction=direction,
            metadata=dict(rec.metadata),
        )

    # NO_OP (audit only — tick heartbeat).
    return PaperDecision(
        strategy=rec.strategy, symbol=rec.symbol,
        action=DecisionAction.NO_OP,
        confidence=rec.confidence,
        reason=rec.reason,
        risk_flags=list(rec.risk_flags),
        paper_order_id=None,
        paper_fill_status=PaperFillStatus.NA,
        virtual_position_delta=0,
        source_direction=direction,
        metadata=dict(rec.metadata),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 변환 + 기록 통합 — caller 가 자주 사용하는 단축 helper
# ─────────────────────────────────────────────────────────────────────────────


def process_ai_recommendation(
    rec:                AIRecommendationInput,
    *,
    loop_state:         str,
    virtual_trade_size: int  = DEFAULT_VIRTUAL_TRADE_SIZE,
    auto_fill:          bool = True,
    record:             bool = True,
) -> tuple[PaperDecision, PaperLoopEvent | None]:
    """AI 입력 → 변환 → (선택적) ledger 기록 통합 흐름.

    `loop_state != "RUNNING"` + trade action → `LedgerStateError`
    (ledger 가 거부). HOLD / NO_OP 는 모든 state 에서 기록 허용.

    `record=False` 면 변환만 (test / dry-run).

    Returns:
        (PaperDecision, PaperLoopEvent | None) — 두 번째 값은 record=False
        또는 ledger 기록 *실패* 시 None.
    """
    decision = convert_to_paper_decision(
        rec, virtual_trade_size=virtual_trade_size, auto_fill=auto_fill,
    )
    if not record:
        return decision, None

    event = record_paper_event(
        loop_state=loop_state,
        strategy=decision.strategy,
        symbol=decision.symbol,
        decision_action=decision.action,
        reason=decision.reason,
        confidence=decision.confidence,
        risk_flags=decision.risk_flags,
        paper_order_id=decision.paper_order_id,
        paper_fill_status=decision.paper_fill_status,
        virtual_position_delta=decision.virtual_position_delta,
        pnl_estimate=decision.pnl_estimate,
        metadata={
            "source_direction": decision.source_direction,
            **dict(decision.metadata),
        },
    )
    return decision, event


__all__ = [
    "DEFAULT_VIRTUAL_TRADE_SIZE",
    "AIDirection",
    "AIRecommendationInput",
    "PaperDecision",
    "convert_to_paper_decision",
    "process_ai_recommendation",
]
