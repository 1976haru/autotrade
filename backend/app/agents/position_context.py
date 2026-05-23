"""3-02: 보유 포지션 context — Agent Council 의 SELL(청산) 판단 기준.

자동매매는 매수보다 *청산/매도* 가 핵심이다. SELL 은 **보유 종목(held_position)을
기준으로 한 청산** 이며 신규 숏 진입이 아니다. 본 모듈은 보유 포지션 정보(수량/
평단/현재가/손절·익절)와 청산 트리거(stop_loss/take_profit/장마감)를 정의한다.

## 절대 invariant (CLAUDE.md)
- SELL 은 *보유 청산만* — `is_short_entry=False` / `short_position=False` 영구.
- 보유 포지션이 없으면(held_position=False 또는 sellable<=0) SELL 금지.
- 실제 계좌 잔고가 아니라 Paper / virtual portfolio 기준 — 실 계좌 조회 0건.
- broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 보유 포지션 기반 SELL 트리거 reason_code (sell_reason.py 와 일관).
SELL_STOP_LOSS         = "STOP_LOSS"
SELL_TAKE_PROFIT       = "TAKE_PROFIT"
SELL_MARKET_CLOSE_EXIT = "MARKET_CLOSE_EXIT"

# 보유 없음 차단 reason.
NO_HELD_POSITION_FOR_SELL = "NO_HELD_POSITION_FOR_SELL"
SELL_QUANTITY_EXCEEDS_POSITION = "SELL_QUANTITY_EXCEEDS_POSITION"


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class PositionContext:
    """보유 포지션 스냅샷 — SELL 판단 입력 (Paper/virtual portfolio 기준)."""

    held_position:       bool = False
    symbol:              str | None = None
    quantity:            int = 0
    available_quantity:  int = 0
    average_entry_price: float | None = None
    current_price:       float | None = None
    stop_loss:           float | None = None
    take_profit:         float | None = None
    market_time_phase:   str | None = None       # PRE_MARKET/.../CLOSING/...
    market_close_exit_enabled: bool = False

    # SELL 은 보유 청산만 — 숏 진입 아님 (영구 False).
    is_short_entry:      bool = False
    short_position:      bool = False

    def __post_init__(self) -> None:
        if self.is_short_entry is not False:
            raise ValueError("PositionContext.is_short_entry must be False (no short entry)")
        if self.short_position is not False:
            raise ValueError("PositionContext.short_position must be False")

    @property
    def sellable_quantity(self) -> int:
        """청산 가능 수량 — available_quantity 우선, 없으면 quantity. 0 미만 0."""
        avail = int(self.available_quantity or 0)
        qty = int(self.quantity or 0)
        base = avail if avail > 0 else qty
        return max(0, min(base, qty if qty > 0 else base))

    @property
    def is_sellable(self) -> bool:
        return bool(self.held_position) and self.sellable_quantity > 0

    @property
    def unrealized_return_pct(self) -> float | None:
        entry = _f(self.average_entry_price)
        cur = _f(self.current_price)
        if entry is None or entry <= 0 or cur is None:
            return None
        return round((cur - entry) / entry * 100.0, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "held_position":       bool(self.held_position),
            "symbol":              self.symbol,
            "quantity":            int(self.quantity or 0),
            "available_quantity":  int(self.available_quantity or 0),
            "sellable_quantity":   self.sellable_quantity,
            "average_entry_price": _f(self.average_entry_price),
            "current_price":       _f(self.current_price),
            "stop_loss":           _f(self.stop_loss),
            "take_profit":         _f(self.take_profit),
            "unrealized_return_pct": self.unrealized_return_pct,
            "market_time_phase":   self.market_time_phase,
            "is_short_entry":      False,
            "short_position":      False,
        }


def infer_position_sell_reason(position: PositionContext | None) -> str | None:
    """보유 포지션 기반 *청산 트리거* reason_code (우선순위 적용). 없으면 None.

    우선순위: STOP_LOSS > TAKE_PROFIT > MARKET_CLOSE_EXIT. 보유 없음/현재가 없음이면
    None (vote 기반 SELL 은 council 이 별도 처리). 신규 숏 진입은 생성하지 않는다.
    """
    if position is None or not position.is_sellable:
        return None
    cur = _f(position.current_price)
    sl = _f(position.stop_loss)
    tp = _f(position.take_profit)
    if cur is not None:
        if sl is not None and sl > 0 and cur <= sl:
            return SELL_STOP_LOSS
        if tp is not None and tp > 0 and cur >= tp:
            return SELL_TAKE_PROFIT
    if position.market_close_exit_enabled \
            and str(position.market_time_phase or "").upper() == "CLOSING":
        return SELL_MARKET_CLOSE_EXIT
    return None


def cap_sell_quantity(requested: int, position: PositionContext | None) -> int:
    """SELL 수량을 보유(청산 가능) 수량 이하로 제한. position 없으면 requested 그대로."""
    req = int(requested or 0)
    if position is None:
        return max(0, req)
    return max(0, min(req, position.sellable_quantity))


__all__ = [
    "PositionContext", "infer_position_sell_reason", "cap_sell_quantity",
    "SELL_STOP_LOSS", "SELL_TAKE_PROFIT", "SELL_MARKET_CLOSE_EXIT",
    "NO_HELD_POSITION_FOR_SELL", "SELL_QUANTITY_EXCEEDS_POSITION",
]
