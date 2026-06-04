"""거래 일생(trade lifecycle) 테이블 — `order_audit_log` 의 BUY/SELL 을 종목별
FIFO 로 짝지어 '한 거래 = 한 줄' 로 보여주는 read-only 빌더 (STEP 3, 2026-06-03).

운영자가 로그가 아니라 *표* 로 거래를 한눈에 보게 한다:
  종목 / 매수시각·가격 / 매도시각·가격 / 보유시간 / 손익(원·%) / 채택전략 / 매수사유.

CLAUDE.md 절대 원칙:
  - read-only DB SELECT 만 — broker / OrderExecutor / route_order 호출 0건,
    새 주문 생성 0건, DB write(INSERT/UPDATE/DELETE) 0건.
  - 민감정보(계좌번호 / app_key / secret / token) 미포함 — order_audit_log 의
    화이트리스트 필드(symbol/side/qty/price/strategy/reason/time)만 carry.
  - 손익은 *체결가 기준* 이며, 체결가가 없으면 주문가/현재가 proxy(`price_basis=
    "ESTIMATE"`)로 표시 — 추정임을 명시 (체결 조회가 켜지면 FILL 로 정확해진다).

비용(거래세/수수료/슬리피지) 반영은 STEP 4 cost tracker 가 본 행을 입력으로 받아
별도로 계산한다 — 여기서는 *총손익(gross)* 만.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


# CSV / 응답에 내보내는 화이트리스트 필드 — 계좌/secret 계열 0건.
CSV_COLUMNS = (
    "symbol", "strategy", "status", "quantity",
    "buy_time", "buy_price", "sell_time", "sell_price",
    "holding_minutes", "gross_pnl_krw", "gross_pnl_pct",
    "price_basis", "buy_reason",
)


@dataclass
class TradeRow:
    symbol:          str
    strategy:        str | None
    status:          str               # "OPEN" | "CLOSED"
    quantity:        int
    buy_time:        str | None        # ISO
    buy_price:       int | None
    sell_time:       str | None        # ISO (None if OPEN)
    sell_price:      int | None
    holding_minutes: float | None      # None if OPEN
    gross_pnl_krw:   int | None        # None if OPEN; 비용 미반영(STEP4가 차감)
    gross_pnl_pct:   float | None
    price_basis:     str               # "FILL" | "ESTIMATE"
    buy_reason:      str | None
    buy_audit_id:    int | None = None
    sell_audit_id:   int | None = None
    # invariant — 본 행은 *표시용* 이며 주문 신호가 아니다.
    is_order_signal: bool = field(default=False)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["is_order_signal"] = False
        return d


def _utc_iso(dt: datetime | None) -> str | None:
    """created_at(naive, UTC 로 저장) → tz-aware ISO('+00:00').

    naive ISO 를 그대로 내보내면 프론트의 `new Date(iso)` 가 *로컬(KST)* 로 잘못
    해석해 UTC 값을 KST 인 양 표시한다(예: UTC 00:53 → 화면 00:53, 실제 KST 09:53).
    UTC 마커를 붙여 보내면 프론트가 KST 로 정확히 변환한다. order audit endpoint
    의 `_ensure_utc` 규약과 동일.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _price_of(row: OrderAuditLog) -> tuple[int | None, str]:
    """(price, basis) — 체결 평균가가 있으면 FILL, 없으면 주문 당시 현재가 proxy."""
    avg = getattr(row, "avg_fill_price", None)
    if avg is not None and int(avg) > 0:
        return int(avg), "FILL"
    lp = getattr(row, "latest_price", None)
    return (int(lp) if lp else None), "ESTIMATE"


def _qty_of(row: OrderAuditLog) -> int:
    filled = int(getattr(row, "filled_quantity", 0) or 0)
    return filled if filled > 0 else int(getattr(row, "quantity", 0) or 0)


def _reason_of(row: OrderAuditLog) -> str | None:
    tr = getattr(row, "trade_reason", None)
    if tr:
        return str(tr)
    reasons = getattr(row, "reasons", None)
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])
    return None


def build_trade_lifecycle(
    db: Session,
    *,
    date_from: datetime,
    date_to:   datetime,
    mode:      str | None = None,
    include_open: bool = True,
) -> list[TradeRow]:
    """[date_from, date_to) 사이 broker 로 *접수된*(executed=True) 주문을 종목별
    FIFO 로 짝지어 거래 행을 만든다. 짝 없는 잔여 BUY 는 OPEN 행으로.

    read-only — DB write 0건. 정렬은 매수시각 기준.
    """
    stmt = (
        select(OrderAuditLog)
        .where(
            OrderAuditLog.created_at >= date_from,
            OrderAuditLog.created_at < date_to,
            OrderAuditLog.executed.is_(True),
        )
        .order_by(OrderAuditLog.created_at.asc(), OrderAuditLog.id.asc())
    )
    if mode:
        stmt = stmt.where(OrderAuditLog.mode == mode)

    rows = db.execute(stmt).scalars().all()

    # 종목별 미체결(open) BUY lot 큐. lot = dict(time, price, qty, basis, reason, strategy, audit_id)
    open_lots: dict[str, deque] = defaultdict(deque)
    trades: list[TradeRow] = []

    for r in rows:
        side = (r.side or "").upper()
        price, basis = _price_of(r)
        qty = _qty_of(r)
        t_iso = _utc_iso(r.created_at)

        if side == "BUY":
            open_lots[r.symbol].append({
                "time": t_iso, "price": price, "qty": qty, "basis": basis,
                "reason": _reason_of(r), "strategy": r.strategy, "audit_id": r.id,
            })
        elif side == "SELL":
            remaining = qty
            q = open_lots[r.symbol]
            while remaining > 0 and q:
                lot = q[0]
                matched = min(remaining, lot["qty"])
                bp, sp = lot["price"], price
                row_basis = "FILL" if (lot["basis"] == "FILL" and basis == "FILL") else "ESTIMATE"
                pnl = pct = None
                if bp is not None and sp is not None:
                    pnl = int((sp - bp) * matched)
                    pct = round((sp / bp - 1.0) * 100.0, 4) if bp else None
                hold_min = None
                if lot["time"] and t_iso:
                    hold_min = round(
                        (datetime.fromisoformat(t_iso) - datetime.fromisoformat(lot["time"])).total_seconds() / 60.0,
                        2,
                    )
                trades.append(TradeRow(
                    symbol=r.symbol, strategy=lot["strategy"], status="CLOSED",
                    quantity=matched, buy_time=lot["time"], buy_price=bp,
                    sell_time=t_iso, sell_price=sp, holding_minutes=hold_min,
                    gross_pnl_krw=pnl, gross_pnl_pct=pct, price_basis=row_basis,
                    buy_reason=lot["reason"], buy_audit_id=lot["audit_id"],
                    sell_audit_id=r.id,
                ))
                lot["qty"] -= matched
                remaining -= matched
                if lot["qty"] <= 0:
                    q.popleft()
            # 보유 없는 SELL(잔여 remaining) 은 trade 로 만들지 않음 (보유 청산만).

    if include_open:
        for symbol, q in open_lots.items():
            for lot in q:
                trades.append(TradeRow(
                    symbol=symbol, strategy=lot["strategy"], status="OPEN",
                    quantity=lot["qty"], buy_time=lot["time"], buy_price=lot["price"],
                    sell_time=None, sell_price=None, holding_minutes=None,
                    gross_pnl_krw=None, gross_pnl_pct=None, price_basis=lot["basis"],
                    buy_reason=lot["reason"], buy_audit_id=lot["audit_id"],
                ))

    trades.sort(key=lambda t: (t.buy_time or ""))
    return trades


def aggregate_by_strategy(rows: list[TradeRow]) -> dict[str, dict]:
    """전략별 집계 (CLOSED 거래만): 거래수 / 승 / 승률 / 평균손익 / 총손익."""
    agg: dict[str, dict] = {}
    by_strat: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        if r.status == "CLOSED" and r.gross_pnl_krw is not None:
            by_strat[r.strategy or "(unknown)"].append(r)
    for strat, trs in by_strat.items():
        n = len(trs)
        wins = sum(1 for t in trs if (t.gross_pnl_krw or 0) > 0)
        total = sum(int(t.gross_pnl_krw or 0) for t in trs)
        agg[strat] = {
            "trades":         n,
            "wins":           wins,
            "win_rate":       round(wins / n, 4) if n else 0.0,
            "avg_pnl_krw":    round(total / n, 1) if n else 0.0,
            "total_pnl_krw":  total,
        }
    return agg


def to_csv(rows: list[TradeRow]) -> str:
    """거래 표 → CSV 문자열. 민감정보(계좌/secret) 컬럼 0건 (화이트리스트만)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(CSV_COLUMNS), extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.to_dict().get(k) for k in CSV_COLUMNS})
    return buf.getvalue()


__all__ = [
    "TradeRow", "CSV_COLUMNS",
    "build_trade_lifecycle", "aggregate_by_strategy", "to_csv",
]
