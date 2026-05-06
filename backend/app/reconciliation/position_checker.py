"""Position reconciliation core (212, MUST).

OrderAuditLog의 executed=True + filled_quantity > 0 행을 walk하면서 symbol별
순포지션 (BUY filled - SELL filled)을 산출하고, broker.get_positions()와
비교해 drift를 식별한다.

비교 정책:
- quantity가 1주 이상 다르면 mismatch — broker가 더 많거나 audit이 더 많거나.
- broker에는 있지만 audit에 0이면 'broker_only' (수동 broker 주문 흔적 등).
- audit에는 있지만 broker에는 없으면 'audit_only' (broker drop / 수동 청산 등).
- 두 view가 각각 0이면 reconciliation에 등장시키지 않는다.

avg_price는 weighted average로 산출하지만 reconciliation 판단 기준에서는 제외 —
quantity가 같으면 in_sync로 본다 (KIS LIVE에서 avg_price round/통화 변환 차이는
정상적인 운영 노이즈).
"""

from collections import defaultdict
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brokers.base import BrokerAdapter, Position
from app.db.models import OrderAuditLog


# OrderAuditLog는 archived 분리(168) 이전에는 모두 hot. archived row도
# reconciliation에는 포함해야 한다 — symbol의 과거 BUY가 archived라고 해서
# 보유 포지션이 사라지지 않는다. archived 필터는 hot UI 전용.


@dataclass(frozen=True)
class AuditPositionRow:
    """audit log walk 결과 — symbol당 순포지션."""
    symbol:        str
    net_quantity:  int          # net BUY filled - SELL filled
    buy_quantity:  int
    sell_quantity: int
    avg_buy_price: int          # weighted average — 분석용 surface


@dataclass(frozen=True)
class PositionMismatch:
    symbol:           str
    broker_quantity:  int        # 0 if broker has no position
    audit_quantity:   int        # 0 if audit has no net position
    quantity_diff:    int        # broker - audit
    kind:             str        # 'quantity_mismatch' | 'broker_only' | 'audit_only'


@dataclass(frozen=True)
class ReconciliationReport:
    in_sync:           bool
    broker_symbol_count: int
    audit_symbol_count:  int
    matched_count:       int    # symbols where broker and audit agree on quantity
    mismatches:          list[PositionMismatch]


def aggregate_audit_positions(db: Session) -> list[AuditPositionRow]:
    """OrderAuditLog의 executed + filled rows를 symbol별로 집계.

    - BUY filled_quantity는 + 누적, SELL filled_quantity는 - 누적.
    - avg_buy_price는 BUY 행만 weighted average. SELL은 avg에서 차감하지 않음
      (FIFO pair-matching은 virtual.position_engine 책임 — reconciliation은 net
      quantity만 본다).
    - net_quantity == 0인 symbol은 결과에서 제외.
    """
    stmt = select(
        OrderAuditLog.symbol,
        OrderAuditLog.side,
        OrderAuditLog.filled_quantity,
        OrderAuditLog.avg_fill_price,
    ).where(
        OrderAuditLog.executed.is_(True),
        OrderAuditLog.filled_quantity > 0,
    )
    rows = db.execute(stmt).all()

    by_symbol_buy:  dict[str, int] = defaultdict(int)
    by_symbol_sell: dict[str, int] = defaultdict(int)
    buy_notional:   dict[str, int] = defaultdict(int)
    for symbol, side, qty, price in rows:
        if not symbol or qty is None or qty <= 0:
            continue
        if side == "BUY":
            by_symbol_buy[symbol] += int(qty)
            if price is not None:
                buy_notional[symbol] += int(qty) * int(price)
        elif side == "SELL":
            by_symbol_sell[symbol] += int(qty)

    out: list[AuditPositionRow] = []
    for symbol in sorted(set(by_symbol_buy) | set(by_symbol_sell)):
        buy = by_symbol_buy.get(symbol, 0)
        sell = by_symbol_sell.get(symbol, 0)
        net = buy - sell
        if net == 0:
            continue
        avg = int(buy_notional[symbol] / buy) if buy > 0 else 0
        out.append(AuditPositionRow(
            symbol=symbol,
            net_quantity=net,
            buy_quantity=buy,
            sell_quantity=sell,
            avg_buy_price=avg,
        ))
    return out


def compare_positions(
    broker_positions: list[Position],
    audit_positions:  list[AuditPositionRow],
) -> list[PositionMismatch]:
    """broker view vs audit view 비교 — symbol별 quantity diff."""
    broker_by_symbol = {p.symbol: int(p.quantity) for p in broker_positions if p.quantity != 0}
    audit_by_symbol  = {a.symbol: a.net_quantity for a in audit_positions}

    mismatches: list[PositionMismatch] = []
    for symbol in sorted(set(broker_by_symbol) | set(audit_by_symbol)):
        b = broker_by_symbol.get(symbol, 0)
        a = audit_by_symbol.get(symbol, 0)
        if b == a:
            continue
        if b == 0 and a != 0:
            kind = "audit_only"
        elif a == 0 and b != 0:
            kind = "broker_only"
        else:
            kind = "quantity_mismatch"
        mismatches.append(PositionMismatch(
            symbol=symbol,
            broker_quantity=b,
            audit_quantity=a,
            quantity_diff=b - a,
            kind=kind,
        ))
    return mismatches


async def reconcile(db: Session, broker: BrokerAdapter) -> ReconciliationReport:
    """broker.get_positions()와 audit log를 비교해 ReconciliationReport 반환.

    broker.get_positions가 raise하면 caller가 감싸야 한다 — 본 함수는 read만.
    """
    broker_positions = await broker.get_positions()
    audit_positions  = aggregate_audit_positions(db)
    mismatches       = compare_positions(broker_positions, audit_positions)

    broker_count = sum(1 for p in broker_positions if p.quantity != 0)
    audit_count  = len(audit_positions)
    union_count  = len({p.symbol for p in broker_positions if p.quantity != 0}
                      | {a.symbol for a in audit_positions})

    return ReconciliationReport(
        in_sync=len(mismatches) == 0,
        broker_symbol_count=broker_count,
        audit_symbol_count=audit_count,
        matched_count=union_count - len(mismatches),
        mismatches=mismatches,
    )


def report_to_dict(report: ReconciliationReport) -> dict:
    """ReconciliationReport를 JSON-serializable dict로 변환."""
    return {
        "in_sync":              report.in_sync,
        "broker_symbol_count":  report.broker_symbol_count,
        "audit_symbol_count":   report.audit_symbol_count,
        "matched_count":        report.matched_count,
        "mismatches":           [asdict(m) for m in report.mismatches],
    }
