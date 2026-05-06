"""Virtual Fill Engine (149, MUST).

가상 주문(VirtualOrder)에 대한 체결 시뮬레이션. 실거래 broker와 분리된 결정적
로직으로 체결 / 부분체결 / 거부 시나리오를 만들어 자동매매 흐름을 end-to-end
검증할 수 있게 한다.

체결 규칙:
- MARKET 주문: 현재 시세(quote.price)에 슬리피지를 더해 즉시 체결.
  - BUY는 +slippage_bps만큼 불리, SELL은 -slippage_bps만큼 불리.
- LIMIT 주문: BUY는 quote.price ≤ limit_price일 때만 체결, SELL은 quote.price
  ≥ limit_price일 때만. 그 외엔 NEW 유지 (또는 caller가 EXPIRED 결정).
- 거래량 제약: bar_volume이 주문 수량보다 작으면 부분체결로 처리. 0이면
  미체결 (NEW 유지 + structured_reason 'no_volume').
- stale price: quote.timestamp가 stale_max_age_seconds 초과 → REJECTED.
- emergency stop: ON이면 어떤 주문도 체결 안 함 → REJECTED.

본 엔진은 외부 의존을 최소화한 순수 함수로 구성 — 호출자가 quote / volume /
emergency_stop 상태를 주입한다. RiskManager 흐름을 우회하지 않으며, 본 엔진
호출 전에 이미 RiskManager가 통과시킨 ACCEPTED 상태 주문에만 적용 가능하다.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import VirtualOrder
from app.virtual.order_ledger import (
    STATUS_ACCEPTED,
    STATUS_FILLED,
    STATUS_PARTIALLY_FILLED,
    STATUS_REJECTED,
    transition,
)


@dataclass(frozen=True)
class FillContext:
    """가상 체결에 필요한 외부 상태 — 호출자가 한 번에 모아서 주입."""
    quote_price:               int        # 현재 시세
    quote_timestamp:           datetime | None  # 시세 timestamp (UTC)
    bar_volume:                int        # 해당 봉의 거래량 (시뮬용)
    emergency_stop_enabled:    bool = False
    stale_max_age_seconds:     int = 60   # 143과 동일 default
    slippage_bps:              int = 5    # 0.05% (단타 KOSPI 평균값 가정)


@dataclass(frozen=True)
class FillOutcome:
    """체결 결과. 호출자가 caller-side 상태(VirtualPositionEngine 등)를 갱신할
    때 참조."""
    final_status:    str            # FILLED / PARTIALLY_FILLED / REJECTED / ACCEPTED(미체결)
    filled_delta:    int            # 이번 호출에서 추가된 체결 수량
    fill_price:      int | None     # 체결가 (slippage 적용 후). 미체결 시 None
    structured_reason: str          # 'fill' / 'partial_fill' / 'no_volume' /
                                    # 'limit_not_crossed' / 'stale_price' /
                                    # 'emergency_stop'


def _is_stale(quote_ts: datetime | None, max_age_seconds: int) -> bool:
    if quote_ts is None or max_age_seconds <= 0:
        return False
    if quote_ts.tzinfo is None:
        quote_ts = quote_ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - quote_ts).total_seconds()
    return age > max_age_seconds


def _apply_slippage(price: int, side: str, slippage_bps: int) -> int:
    """단타 시뮬용 단순 슬리피지 — 시장가 BUY는 불리하게 위로, SELL은 아래로.
    가격 정수 보존 (한국 주식 호가는 정수)."""
    if slippage_bps <= 0:
        return price
    delta = max(1, int(price * slippage_bps / 10_000))
    return price + delta if side == "BUY" else max(1, price - delta)


def simulate_fill(
    db: Session,
    order: VirtualOrder,
    ctx: FillContext,
) -> FillOutcome:
    """ACCEPTED/PARTIALLY_FILLED 주문에 대해 한 번의 체결 시도를 시뮬레이션.

    호출자는 한 봉 = 한 번의 simulate_fill 호출을 가정. 잔량이 남으면
    PARTIALLY_FILLED 상태로 유지되어 다음 봉에서 다시 호출 가능.
    """
    if order.status not in (STATUS_ACCEPTED, STATUS_PARTIALLY_FILLED):
        # 본 엔진은 이미 ACCEPTED를 통과한 주문에만 적용 — caller 책임.
        # 안전 측 NO-OP 응답.
        return FillOutcome(
            final_status=order.status, filled_delta=0,
            fill_price=order.avg_fill_price,
            structured_reason="not_accepted",
        )

    # Hard-reject: emergency stop. 060 invariant와 같은 맥락으로 fill 단계에서도
    # 차단해 시스템 전체에서 어떤 신규 체결도 일어나지 않도록.
    if ctx.emergency_stop_enabled:
        transition(db, order, to_status=STATUS_REJECTED,
                   reason="emergency_stop")
        return FillOutcome(
            final_status=STATUS_REJECTED, filled_delta=0,
            fill_price=None, structured_reason="emergency_stop",
        )

    # Hard-reject: stale price. 143 invariant 일관성.
    if _is_stale(ctx.quote_timestamp, ctx.stale_max_age_seconds):
        transition(db, order, to_status=STATUS_REJECTED,
                   reason="stale_price")
        return FillOutcome(
            final_status=STATUS_REJECTED, filled_delta=0,
            fill_price=None, structured_reason="stale_price",
        )

    # 거래량 0 → 시세는 있지만 매수자/매도자 없는 상태. 체결 불가.
    if ctx.bar_volume <= 0:
        return FillOutcome(
            final_status=order.status, filled_delta=0,
            fill_price=order.avg_fill_price,
            structured_reason="no_volume",
        )

    remaining = order.quantity - order.filled_quantity
    if remaining <= 0:
        # 이미 전량 체결된 주문이 호출됨 — 안전 측 NO-OP.
        return FillOutcome(
            final_status=order.status, filled_delta=0,
            fill_price=order.avg_fill_price,
            structured_reason="already_filled",
        )

    # LIMIT 주문 — 가격 조건 미충족이면 미체결.
    if order.order_type == "LIMIT" and order.limit_price is not None:
        if order.side == "BUY"  and ctx.quote_price > order.limit_price:
            return FillOutcome(
                final_status=order.status, filled_delta=0,
                fill_price=order.avg_fill_price,
                structured_reason="limit_not_crossed",
            )
        if order.side == "SELL" and ctx.quote_price < order.limit_price:
            return FillOutcome(
                final_status=order.status, filled_delta=0,
                fill_price=order.avg_fill_price,
                structured_reason="limit_not_crossed",
            )
        # LIMIT은 슬리피지 X — 지정한 가격에서만 체결 가정 (시장가 갭은 운영자
        # 가 별도로 모니터링; MVP는 단순화).
        fill_price = ctx.quote_price
    else:
        # MARKET — slippage 적용.
        fill_price = _apply_slippage(ctx.quote_price, order.side, ctx.slippage_bps)

    # 체결 수량: 주문 잔량과 봉 거래량의 min. 부분 체결 가능.
    fill_qty = min(remaining, ctx.bar_volume)
    is_full = (fill_qty == remaining)

    # cumulative average price — 새 부분 체결을 기존 weighted avg에 합친다.
    if order.filled_quantity == 0 or order.avg_fill_price is None:
        new_avg = fill_price
    else:
        cur_total = order.avg_fill_price * order.filled_quantity
        add_total = fill_price * fill_qty
        new_avg = int(round((cur_total + add_total) / (order.filled_quantity + fill_qty)))

    next_status = STATUS_FILLED if is_full else STATUS_PARTIALLY_FILLED
    reason      = "fill" if is_full else "partial_fill"
    transition(db, order, to_status=next_status, reason=reason,
               filled_delta=fill_qty, avg_fill_price=new_avg)

    return FillOutcome(
        final_status=next_status, filled_delta=fill_qty,
        fill_price=fill_price, structured_reason=reason,
    )
