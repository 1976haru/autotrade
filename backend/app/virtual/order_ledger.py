"""Virtual Order Ledger service (148, MUST).

VirtualOrder 테이블의 라이프사이클 상태 전이를 관리. 외부 의존이 거의 없는
순수 데이터 계층 — RiskManager / PermissionGate / FillEngine 등이 본 모듈을
호출해 상태를 갱신한다.

상태 전이 규칙 (단방향):
    NEW → ACCEPTED   → PARTIALLY_FILLED → FILLED
    NEW → ACCEPTED                       ↘ CANCELLED
    NEW → REJECTED
    NEW → ACCEPTED   →                     CANCELLED
    NEW → ACCEPTED   → PARTIALLY_FILLED  ↘ EXPIRED
                                          ↘ CANCELLED

Terminal states: FILLED / CANCELLED / REJECTED / EXPIRED. 한 번 terminal에
도달한 주문은 다시 전이될 수 없다 (이중 결정 차단).
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import VirtualOrder


# 상태 enum 대신 문자열 상수 — DB 컬럼이 String이라 매칭 단순화.
STATUS_NEW              = "NEW"
STATUS_ACCEPTED         = "ACCEPTED"
STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STATUS_FILLED           = "FILLED"
STATUS_CANCELLED        = "CANCELLED"
STATUS_REJECTED         = "REJECTED"
STATUS_EXPIRED          = "EXPIRED"

TERMINAL_STATES = frozenset({STATUS_FILLED, STATUS_CANCELLED,
                             STATUS_REJECTED, STATUS_EXPIRED})


# 허용되는 전이 그래프. key가 from-state, value는 from-state에서 갈 수 있는
# 모든 다음 상태의 집합. NEW는 모든 결정의 출발점.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_NEW: frozenset({
        STATUS_ACCEPTED, STATUS_REJECTED, STATUS_CANCELLED,
    }),
    STATUS_ACCEPTED: frozenset({
        STATUS_PARTIALLY_FILLED, STATUS_FILLED,
        STATUS_CANCELLED, STATUS_EXPIRED,
        # 149: 체결 단계의 hard-reject (emergency_stop, stale_price 등)는
        # ACCEPTED → REJECTED로 surface. 운영자 cancel과 구분하기 위해 별도.
        STATUS_REJECTED,
    }),
    STATUS_PARTIALLY_FILLED: frozenset({
        STATUS_FILLED, STATUS_CANCELLED, STATUS_EXPIRED,
        # 부분 체결 후 emergency_stop이 켜지면 잔량은 REJECTED.
        STATUS_REJECTED,
    }),
    # terminal states는 어떤 전이도 허용 안 함.
    STATUS_FILLED:    frozenset(),
    STATUS_CANCELLED: frozenset(),
    STATUS_REJECTED:  frozenset(),
    STATUS_EXPIRED:   frozenset(),
}


class VirtualOrderError(Exception):
    """Lifecycle 위반 (terminal에서의 전이, 미허용 from→to 전이 등)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_order(
    db:               Session,
    *,
    symbol:           str,
    side:             str,
    quantity:         int,
    order_type:       str = "MARKET",
    limit_price:      int | None = None,
    requested_price:  int | None = None,
    strategy:         str | None = None,
    mode:             str,
    audit_id:         int | None = None,
) -> VirtualOrder:
    """NEW 상태로 주문 등록. 실제 처리(RiskManager 평가 등)는 caller 책임."""
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    if side not in ("BUY", "SELL"):
        raise ValueError(f"unknown side: {side}")
    order = VirtualOrder(
        symbol=symbol,
        side=side,
        quantity=quantity,
        order_type=order_type,
        limit_price=limit_price,
        requested_price=requested_price,
        strategy=strategy,
        mode=mode,
        status=STATUS_NEW,
        filled_quantity=0,
        audit_id=audit_id,
    )
    db.add(order)
    db.flush()
    return order


def transition(
    db:                Session,
    order:             VirtualOrder,
    *,
    to_status:         str,
    reason:            str | None = None,
    note:              str | None = None,
    filled_delta:      int = 0,
    avg_fill_price:    int | None = None,
) -> VirtualOrder:
    """주문 상태를 to_status로 전이. 잘못된 전이는 VirtualOrderError.

    filled_delta: 이번 전이에서 추가 체결된 수량. PARTIALLY_FILLED/FILLED 시
    호출자가 명시. cumulative filled_quantity는 본 함수가 계산.
    avg_fill_price: 부분/전량 체결 시 평균 체결가 (cumulative). 호출자가 새
    weighted average를 산출해 넘긴다.
    """
    if to_status not in _ALLOWED_TRANSITIONS:
        raise VirtualOrderError(f"unknown target status: {to_status}")
    allowed = _ALLOWED_TRANSITIONS.get(order.status, frozenset())
    if to_status not in allowed:
        raise VirtualOrderError(
            f"transition not allowed: {order.status} → {to_status}"
        )

    if filled_delta < 0:
        raise ValueError("filled_delta must be non-negative")
    new_filled = order.filled_quantity + filled_delta
    if new_filled > order.quantity:
        raise VirtualOrderError(
            f"filled_quantity {new_filled} exceeds order.quantity {order.quantity}"
        )

    order.status = to_status
    if reason is not None:
        order.structured_reason = reason
    if note is not None:
        order.note = note
    if filled_delta > 0:
        order.filled_quantity = new_filled
        if avg_fill_price is not None:
            order.avg_fill_price = avg_fill_price
    if to_status in TERMINAL_STATES:
        order.filled_at = _now()
    order.updated_at = _now()
    db.flush()
    return order


def is_terminal(order: VirtualOrder) -> bool:
    return order.status in TERMINAL_STATES
