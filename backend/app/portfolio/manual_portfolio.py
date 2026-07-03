"""수동 직접 보유 포트폴리오 계산 — order_audit_log FIFO 기반.

안전 원칙 (CLAUDE.md 절대 원칙 준수):
- broker / OrderExecutor / route_order / KIS / Anthropic import 0건.
- DB read-only (INSERT 0건).
- is_order_signal=False, is_live_authorization=False 영구.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog
from app.universe.default_universe import FALLBACK_TOP402_NAMES

KST = timezone(timedelta(hours=9))

MANUAL_BUY_REASON = "manual_buy"
MANUAL_SELL_REASON = "manual_sell"
_MANUAL_REASONS = (MANUAL_BUY_REASON, MANUAL_SELL_REASON)


def _kst_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def today_kst() -> date:
    return datetime.now(timezone.utc).astimezone(KST).date()


@dataclass
class ManualHolding:
    symbol: str
    name: str
    quantity: int
    avg_price: int        # FIFO 계산 기준 (KIS 블렌딩 아님)
    current_price: int    # broker.get_positions() market_price
    market_value: int
    cost_basis: int
    unrealized_pnl: int
    unrealized_pnl_pct: float
    first_bought_at: str  # ISO date string
    holding_days: int
    weight_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":            self.symbol,
            "name":              self.name,
            "quantity":          self.quantity,
            "avg_price":         self.avg_price,
            "current_price":     self.current_price,
            "market_value":      self.market_value,
            "cost_basis":        self.cost_basis,
            "unrealized_pnl":    self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "first_bought_at":   self.first_bought_at,
            "holding_days":      self.holding_days,
            "weight_pct":        self.weight_pct,
        }


def _fetch_manual_orders(db: Session) -> list[OrderAuditLog]:
    """manual_buy/sell 체결 전건, 오래된 것 먼저 (FIFO 전체 계산에 필요)."""
    return (
        db.query(OrderAuditLog)
        .filter(
            OrderAuditLog.trade_reason.in_(_MANUAL_REASONS),
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.filled_quantity > 0,
        )
        .order_by(OrderAuditLog.id)
        .all()
    )


def compute_manual_holdings(
    db: Session,
    broker_positions: list[Any],
) -> list[ManualHolding]:
    """FIFO 매칭으로 현재 수동 직접 보유 계산.

    broker_positions: broker.get_positions() 반환값 — market_price 취득 용.
    avg_price는 FIFO DB 계산 기준이며 KIS 계좌 avg와 다를 수 있음.
    """
    orders = _fetch_manual_orders(db)

    # symbol → deque of {"qty": int, "price": int, "at": datetime}
    buy_queue: dict[str, deque] = defaultdict(deque)

    for o in orders:
        qty   = int(o.filled_quantity or 0)
        price = int(o.avg_fill_price or 0)
        if qty <= 0 or price <= 0:
            continue
        if o.trade_reason == MANUAL_BUY_REASON:
            buy_queue[o.symbol].append({"qty": qty, "price": price, "at": o.created_at})
        elif o.trade_reason == MANUAL_SELL_REASON:
            remaining = qty
            while remaining > 0 and buy_queue[o.symbol]:
                lot  = buy_queue[o.symbol][0]
                take = min(remaining, lot["qty"])
                lot["qty"] -= take
                remaining  -= take
                if lot["qty"] == 0:
                    buy_queue[o.symbol].popleft()

    # 현재가 맵 (broker positions)
    price_map: dict[str, int] = {}
    for p in broker_positions:
        sym = getattr(p, "symbol", None)
        mp  = int(getattr(p, "market_price", 0) or 0)
        if sym and mp > 0:
            price_map[sym] = mp

    today = today_kst()
    holdings: list[ManualHolding] = []

    for symbol, queue in buy_queue.items():
        lots = [lot for lot in queue if lot["qty"] > 0]
        if not lots:
            continue
        total_qty  = sum(lot["qty"] for lot in lots)
        if total_qty <= 0:
            continue
        total_cost = sum(lot["qty"] * lot["price"] for lot in lots)
        avg_price  = total_cost // max(1, total_qty)
        first_at   = min(lot["at"] for lot in lots)
        first_date = _kst_date(first_at)
        holding_days  = max(0, (today - first_date).days)
        current_price = price_map.get(symbol, 0)
        market_value  = current_price * total_qty
        cost_basis    = avg_price * total_qty
        unrealized    = market_value - cost_basis
        unreal_pct    = round(
            (current_price / avg_price - 1) * 100, 2
        ) if avg_price > 0 and current_price > 0 else 0.0

        holdings.append(ManualHolding(
            symbol=symbol,
            name=FALLBACK_TOP402_NAMES.get(symbol, symbol),
            quantity=total_qty,
            avg_price=avg_price,
            current_price=current_price,
            market_value=market_value,
            cost_basis=cost_basis,
            unrealized_pnl=unrealized,
            unrealized_pnl_pct=unreal_pct,
            first_bought_at=first_date.isoformat(),
            holding_days=holding_days,
        ))

    # 시장가치 내림차순 정렬 (미조회분은 cost_basis 기준)
    holdings.sort(key=lambda h: -(h.market_value if h.market_value > 0 else h.cost_basis))

    # 비중 계산
    total_val  = sum(h.market_value for h in holdings)
    total_cost = sum(h.cost_basis for h in holdings)
    denom = total_val if total_val > 0 else total_cost
    for h in holdings:
        v = h.market_value if total_val > 0 else h.cost_basis
        h.weight_pct = round(v / denom * 100, 1) if denom > 0 else 0.0

    return holdings


def compute_period_realized_pnl(
    db: Session,
    *,
    since: date,
    until: date,
) -> int:
    """기간 내 수동 청산 round-trip 실현 손익 (수수료 미차감 gross)."""
    orders = _fetch_manual_orders(db)

    since_dt = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
    until_dt = datetime.combine(until + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)

    buy_queue: dict[str, deque] = defaultdict(deque)
    pnl = 0

    for o in orders:
        qty   = int(o.filled_quantity or 0)
        price = int(o.avg_fill_price or 0)
        if qty <= 0 or price <= 0:
            continue

        created = o.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        if o.trade_reason == MANUAL_BUY_REASON:
            buy_queue[o.symbol].append({"qty": qty, "price": price})
        elif o.trade_reason == MANUAL_SELL_REASON:
            in_period = since_dt <= created < until_dt
            remaining, buy_cost, matched = qty, 0, 0
            while remaining > 0 and buy_queue[o.symbol]:
                lot  = buy_queue[o.symbol][0]
                take = min(remaining, lot["qty"])
                if in_period:
                    buy_cost += lot["price"] * take
                    matched  += take
                lot["qty"] -= take
                remaining  -= take
                if lot["qty"] == 0:
                    buy_queue[o.symbol].popleft()
            if in_period and matched > 0:
                pnl += price * matched - buy_cost

    return pnl


def resolve_period_dates(
    period: str,
    today: date,
    from_: str | None,
    to_: str | None,
) -> tuple[date, date]:
    if period == "today":
        return today, today
    if period == "1w":
        return today - timedelta(days=7), today
    if period == "1m":
        return today - timedelta(days=30), today
    # custom
    try:
        since = date.fromisoformat(from_) if from_ else today - timedelta(days=30)
        until = date.fromisoformat(to_) if to_ else today
    except ValueError:
        since, until = today, today
    return since, until
