"""#2-09: Paper Auto Loop event dataclass — AI 판단 + 가상 체결 결과.

본 모듈은 AI Paper Auto Loop 의 *결정 / 가상 체결 결과* 를 advisory ledger 에
기록하기 위한 표준 event dataclass 를 정의한다.

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 event 는 *실제 주문이 아니다*** — `is_order_signal=False` 불변.
2. **자동 적용 0건** — `auto_apply_allowed=False` 불변.
3. **실거래 허가 0건** — `is_live_authorization=False` 불변.
4. **broker / OrderExecutor / route_order import 0건** — 정적 grep 가드.
5. **외부 HTTP / AI SDK / Anthropic / OpenAI import 0건**.
6. **DB write 0건** — 본 모듈은 dataclass 정의만.
7. **secret / API key / 계좌번호 carry 0건** — 해당 필드 *존재 자체* 0건.

## DecisionAction 분류

| Action | 의미 | trade event 여부 | 허용 state |
|---|---|---|---|
| `NO_OP` | audit only — 판단 없음 (예: tick heartbeat) | ❌ | 모든 state |
| `HOLD` | AI 가 분석 후 *보류* — 신규 진입 없음 | ❌ | 모든 state |
| `BUY` | Paper 가상 매수 — paper_order_id 발급 | ✅ | RUNNING 만 |
| `SELL` | Paper 가상 매도 | ✅ | RUNNING 만 |
| `EXIT` | Paper 가상 청산 | ✅ | RUNNING 만 |

`DecisionAction` 에 BUY/SELL/EXIT 가 *있지만* — 본 enum 의 값은 *Paper 가상*
의 의미. 실제 broker 호출은 발생하지 않으며, paper_order_id 는 가상 ID 다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Enums (Paper 전용 — 실제 주문 신호 아님)
# ─────────────────────────────────────────────────────────────────────────────


class DecisionAction(StrEnum):
    """AI Paper 판단 결과 — *advisory only*.

    BUY/SELL/EXIT 값이 *있지만* — 본 enum 은 Paper 가상 체결 라벨이며
    실제 broker 주문 신호가 *아니다*. `is_order_signal=False` invariant 가
    영구 강제 (PaperLoopEvent `__post_init__`).
    """
    NO_OP   = "NO_OP"     # tick heartbeat / audit only
    HOLD    = "HOLD"      # 판단 후 보류
    BUY     = "BUY"       # Paper 가상 매수
    SELL    = "SELL"      # Paper 가상 매도
    EXIT    = "EXIT"      # Paper 가상 청산


class PaperFillStatus(StrEnum):
    """Paper 가상 체결 상태 — 실 broker 와 연결 0건."""
    NA              = "NA"                # HOLD / NO_OP — 체결 없음
    PAPER_PENDING   = "PAPER_PENDING"     # 가상 주문 접수, 체결 대기
    PAPER_FILLED    = "PAPER_FILLED"      # 가상 체결 완료
    PAPER_REJECTED  = "PAPER_REJECTED"    # 가상 거절 (RiskManager 가드 등)
    PAPER_CANCELLED = "PAPER_CANCELLED"   # 가상 취소


# 거래성 action — RUNNING state 에서만 ledger 기록 허용.
TRADE_ACTIONS: frozenset[DecisionAction] = frozenset({
    DecisionAction.BUY, DecisionAction.SELL, DecisionAction.EXIT,
})


# ─────────────────────────────────────────────────────────────────────────────
# Event dataclass
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PaperLoopEvent:
    """Paper Auto Loop 단일 결정 / 가상 체결 event.

    *주문 신호가 아니다* — `is_order_signal=False` 불변 (`__post_init__`
    ValueError). 모든 필드는 advisory.

    Required 13 + 3 invariant = 16 필드 (user spec):
    - timestamp / loop_state / strategy / symbol / decision_action /
      confidence / reason / risk_flags / paper_order_id / paper_fill_status /
      virtual_position_delta / pnl_estimate / is_order_signal=False /
      auto_apply_allowed=False / is_live_authorization=False
    + event_id (내부 식별자, ledger 가 부여)
    """

    # 식별 + 시간.
    event_id:              str
    timestamp:             str                       # ISO8601 UTC
    loop_state:            str                       # AutoPaperState 값

    # 결정 컨텍스트.
    strategy:              str
    symbol:                str
    decision_action:       DecisionAction
    confidence:            float | None              # 0~1 (None = 미부여)
    reason:                str                       # 운영자 가독 사유
    risk_flags:            list[str]                 = field(default_factory=list)

    # 가상 체결 결과 (HOLD/NO_OP 면 NA).
    paper_order_id:        str | None                = None
    paper_fill_status:     PaperFillStatus           = PaperFillStatus.NA
    virtual_position_delta: int                      = 0   # +/- 가상 보유 변화
    pnl_estimate:          float                     = 0.0  # 가상 PnL 추정 KRW

    # 메타.
    metadata:              dict[str, Any]            = field(default_factory=dict)

    # 절대 invariant.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        # invariants — caller 변경 불가.
        if self.is_order_signal is not False:
            raise ValueError("PaperLoopEvent.is_order_signal must be False.")
        if self.auto_apply_allowed is not False:
            raise ValueError("PaperLoopEvent.auto_apply_allowed must be False.")
        if self.is_live_authorization is not False:
            raise ValueError("PaperLoopEvent.is_live_authorization must be False.")
        # decision_action / paper_fill_status 강제 enum.
        if not isinstance(self.decision_action, DecisionAction):
            raise ValueError("decision_action must be DecisionAction.")
        if not isinstance(self.paper_fill_status, PaperFillStatus):
            raise ValueError("paper_fill_status must be PaperFillStatus.")
        # confidence 범위 — None 또는 [0,1].
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"confidence must be in [0,1] or None, got {self.confidence}"
            )
        # 식별 + state 비어있을 수 없음.
        if not self.event_id or not self.timestamp or not self.loop_state:
            raise ValueError("event_id / timestamp / loop_state must be non-empty.")
        if not self.strategy or not self.symbol:
            raise ValueError("strategy / symbol must be non-empty.")

    def is_trade_event(self) -> bool:
        """BUY/SELL/EXIT — 거래성 event (RUNNING 에서만 기록 허용)."""
        return self.decision_action in TRADE_ACTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":               self.event_id,
            "timestamp":              self.timestamp,
            "loop_state":             self.loop_state,
            "strategy":               self.strategy,
            "symbol":                 self.symbol,
            "decision_action":        self.decision_action.value,
            "confidence":             self.confidence,
            "reason":                 self.reason,
            "risk_flags":             list(self.risk_flags),
            "paper_order_id":         self.paper_order_id,
            "paper_fill_status":      self.paper_fill_status.value,
            "virtual_position_delta": int(self.virtual_position_delta),
            "pnl_estimate":           float(self.pnl_estimate),
            "metadata":               dict(self.metadata),
            # 절대 invariant (JSON consumer 안전).
            "is_order_signal":        False,
            "auto_apply_allowed":     False,
            "is_live_authorization":  False,
        }


def now_iso() -> str:
    """ISO8601 UTC — ledger event timestamp 표준."""
    return datetime.now(timezone.utc).isoformat()
