"""P1: 성과 집계 (승률 · 손익비 · 순손익 · 기간 수익률) — *읽기 전용*.

집계 소스는 `order_audit_log` 의 *실체결* 만(D4/D6 원칙). paper_capital_state.json
등 비동기 파일 미참조. 주문 경로 / 봇 루프 / 리스크 / config 쓰기 0건.

청산 라운드트립:
  symbol 별 FIFO 매수 큐에 BUY 체결을 적재하고, SELL 체결마다 FIFO 로 매수 lot
  을 소진해 *SELL 1건 = 청산(round-trip) 1건* 으로 본다(부분 청산은 소진된 수량
  만큼만 1 라운드트립). 매수 평단은 소진된 lot 들의 가중평균.
  순손익 = (매도가−가중매수가)×수량 − 거래비용(왕복 ≈ 33bps, cost_tracker 상수).
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog
from app.reporting.cost_tracker import ROUND_TRIP_COST_BPS, compute_trade_cost

KST = timezone(timedelta(hours=9))
SMALL_SAMPLE_THRESHOLD = 10  # 청산 10건 미만이면 small_sample


@dataclass
class RoundTrip:
    symbol:        str
    quantity:      int
    buy_cost:      int    # 가중 매수 금액(소진 lot 합)
    sell_notional: int
    gross_pnl:     int    # 비용 차감 전
    cost:          int    # 거래비용(왕복)
    net_pnl:       int    # 비용 차감 후
    closed_at_kst: date   # 청산(SELL) KST 날짜
    entry_audit_ids: list[int] = None  # S4: 소진된 진입 BUY order_audit_log id(들)

    def __post_init__(self):
        if self.entry_audit_ids is None:
            self.entry_audit_ids = []


def _kst_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).date()


def compute_round_trips(db: Session, *, mode: str | None = None) -> list[RoundTrip]:
    """order_audit_log 실체결 → FIFO 청산 라운드트립 목록 (전 기간)."""
    q = (
        db.query(OrderAuditLog)
        .filter(
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.filled_quantity > 0,
            OrderAuditLog.avg_fill_price.isnot(None),
        )
    )
    if mode:
        q = q.filter(OrderAuditLog.mode == mode)
    rows = q.order_by(OrderAuditLog.id).all()

    queue: dict[str, deque[list[int]]] = defaultdict(deque)  # symbol -> [[qty, price], ...]
    trips: list[RoundTrip] = []
    for r in rows:
        qty = int(r.filled_quantity or 0)
        price = int(r.avg_fill_price or 0)
        if qty <= 0:
            continue
        side = str(r.side or "").upper()
        if side == "BUY":
            queue[r.symbol].append([qty, price, int(r.id)])  # [qty, price, order id]
            continue
        if side != "SELL":
            continue
        remaining = qty
        matched_qty = 0
        buy_cost = 0
        entry_ids: list[int] = []
        dq = queue[r.symbol]
        while remaining > 0 and dq:
            lot = dq[0]
            take = min(remaining, lot[0])
            buy_cost += lot[1] * take
            matched_qty += take
            remaining -= take
            if lot[2] not in entry_ids:
                entry_ids.append(lot[2])    # S4: 소진된 진입 BUY id 수집
            if take == lot[0]:
                dq.popleft()
            else:
                lot[0] -= take
        if matched_qty <= 0:
            continue  # naked SELL (대응 매수 없음) — 라운드트립 아님(보유 비대응)
        avg_buy_price = round(buy_cost / matched_qty)
        sell_notional = price * matched_qty
        gross = sell_notional - buy_cost
        cost = compute_trade_cost(
            buy_price=avg_buy_price, sell_price=price, quantity=matched_qty,
        ).total_cost_krw
        trips.append(RoundTrip(
            symbol=r.symbol, quantity=matched_qty, buy_cost=int(buy_cost),
            sell_notional=int(sell_notional), gross_pnl=int(gross),
            cost=int(cost), net_pnl=int(gross - cost), closed_at_kst=_kst_date(r.created_at),
            entry_audit_ids=entry_ids,
        ))
    return trips


def compute_performance(
    db: Session,
    *,
    start: date,
    end: date,
    mode: str | None = None,
    base_equity_krw: int | None = None,
) -> dict[str, Any]:
    """기간 [start, end] (KST, 양끝 포함) 의 청산 기준 성과 지표 + 표본 정직성 플래그.

    base_equity_krw 가 주어지면 기간 수익률(%)을 산출(없으면 None).
    """
    trips = [t for t in compute_round_trips(db, mode=mode) if start <= t.closed_at_kst <= end]
    n = len(trips)

    wins = [t for t in trips if t.net_pnl > 0]
    losses = [t for t in trips if t.net_pnl < 0]
    breakeven = [t for t in trips if t.net_pnl == 0]
    net_total = sum(t.net_pnl for t in trips)

    no_data = n == 0
    small_sample = 0 < n < SMALL_SAMPLE_THRESHOLD

    win_rate = (len(wins) / n) if n else None
    avg_win = (sum(t.net_pnl for t in wins) / len(wins)) if wins else 0.0
    avg_loss = (sum(-t.net_pnl for t in losses) / len(losses)) if losses else 0.0
    payoff = (avg_win / avg_loss) if (wins and losses and avg_loss > 0) else None

    period_return_pct = None
    if base_equity_krw and base_equity_krw > 0 and not no_data:
        period_return_pct = round(net_total / base_equity_krw * 100, 2)

    return {
        "closed_count":   n,
        "win_count":      len(wins),
        "loss_count":     len(losses),
        "breakeven_count": len(breakeven),
        "win_rate":       (round(win_rate, 4) if win_rate is not None else None),
        "payoff_ratio":   (round(payoff, 2) if payoff is not None else None),
        "net_pnl_krw":    int(net_total),
        "gross_pnl_krw":  int(sum(t.gross_pnl for t in trips)),
        "cost_krw":       int(sum(t.cost for t in trips)),
        "period_return_pct": period_return_pct,
        "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
        "period_start_kst": start.isoformat(),
        "period_end_kst":   end.isoformat(),
        # 표본 정직성 (D3 방식) — 프론트가 가짜 0% 대신 분기.
        "no_data":        no_data,
        "small_sample":   small_sample,
    }


# ── 기간 경계 (KST 영업일 기준, D5 원칙) ───────────────────────────────────────

def resolve_period(period: str, *, today: date, from_: date | None = None,
                   to: date | None = None) -> tuple[date, date]:
    """period → (start, end) KST date (양끝 포함). custom 은 from_/to 사용."""
    p = (period or "daily").strip().lower()
    if p == "daily":
        return today, today
    if p == "weekly":
        return today - timedelta(days=today.weekday()), today  # 이번 주 월요일~오늘
    if p == "monthly":
        return today.replace(day=1), today                     # 이번 달 1일~오늘
    if p == "custom":
        s = from_ or today
        e = to or today
        if e < s:
            s, e = e, s
        return s, e
    return today, today
