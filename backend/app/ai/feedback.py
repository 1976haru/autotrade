"""AI agent feedback loop (163, MUST).

지능형 에이전트의 self-correction. AI 발신 주문의 historical PnL을 FIFO
페어매칭(144)으로 산출하고, win_rate 기반으로 다음 제안의 confidence를
보정한다.

설계 결정:
- 표본 부족 (`< MIN_SAMPLE_TRADES`)이면 보정 안 함 — 1.0 factor.
- win_rate 구간별 factor (보수적 곱셈):
  - < 0.4: 0.5  (절반으로 깎음)
  - < 0.5: 0.7
  - < 0.6: 1.0  (no change)
  - < 0.7: 1.1
  - >= 0.7: 1.2
- adjusted_confidence는 [0, 100] clamp.
- read-only 함수 — 호출자가 결정.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


MIN_SAMPLE_TRADES = 10  # 표본이 이보다 적으면 보정 안 함.


@dataclass(frozen=True)
class HistoricalAccuracy:
    """AI 에이전트의 (strategy 단위) 과거 성과 요약."""
    strategy:                     str
    trades_realized:              int   # FIFO 페어매칭으로 청산된 거래 수
    wins:                         int
    losses:                       int
    win_rate:                     float
    realized_pnl:                 int   # 누적 실현 손익
    recommended_confidence_factor: float


def _factor_from_win_rate(win_rate: float) -> float:
    """win_rate 구간별 confidence 곱셈 factor."""
    if win_rate < 0.4:
        return 0.5
    if win_rate < 0.5:
        return 0.7
    if win_rate < 0.6:
        return 1.0
    if win_rate < 0.7:
        return 1.1
    return 1.2


def _clamp_confidence(value: int) -> int:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def compute_historical_accuracy(
    db:             Session,
    *,
    strategy:       str,
    lookback_days:  int = 30,
    now:            datetime | None = None,
) -> HistoricalAccuracy:
    """AI 에이전트(strategy 단위)의 과거 성과 + 권장 confidence factor.

    144의 compute_live_strategy_pnl과 같은 FIFO 페어매칭 알고리즘을 strategy
    단위로 적용. requested_by_ai=True 행만 본다 (AI 외 경로 거래 제외).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=lookback_days)) if lookback_days > 0 else None

    stmt = (
        select(OrderAuditLog)
        .where(
            OrderAuditLog.requested_by_ai.is_(True),
            OrderAuditLog.strategy == strategy,
            OrderAuditLog.executed.is_(True),
            OrderAuditLog.avg_fill_price.isnot(None),
            OrderAuditLog.filled_quantity > 0,
        )
        .order_by(OrderAuditLog.id)
    )
    if cutoff is not None:
        stmt = stmt.where(OrderAuditLog.created_at > cutoff)
    rows = db.execute(stmt).scalars().all()

    # symbol별 FIFO 페어매칭. 같은 symbol에 여러 BUY/SELL 사이클이 있어도
    # 누적해서 wins/losses를 카운트.
    buy_queue: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    realized = 0
    wins   = 0
    losses = 0

    for r in rows:
        qty   = r.filled_quantity
        price = r.avg_fill_price
        if r.side == "BUY":
            buy_queue[r.symbol].append((qty, price))
            continue
        if r.side != "SELL":
            continue

        remaining = qty
        sell_pnl  = 0
        matched_any = False
        q = buy_queue[r.symbol]
        while remaining > 0 and q:
            buy_qty, buy_price = q[0]
            take = min(remaining, buy_qty)
            sell_pnl  += (price - buy_price) * take
            remaining -= take
            matched_any = True
            if take == buy_qty:
                q.popleft()
            else:
                q[0] = (buy_qty - take, buy_price)

        if matched_any:
            realized += sell_pnl
            if sell_pnl > 0:
                wins += 1
            else:
                losses += 1

    trades = wins + losses
    win_rate = wins / trades if trades > 0 else 0.0

    # 표본 부족 → 보정 안 함 (operator 의도 없이 confidence 깎이지 않도록).
    if trades < MIN_SAMPLE_TRADES:
        factor = 1.0
    else:
        factor = _factor_from_win_rate(win_rate)

    return HistoricalAccuracy(
        strategy=strategy,
        trades_realized=trades,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        realized_pnl=realized,
        recommended_confidence_factor=factor,
    )


def adjust_confidence(raw: int, factor: float) -> int:
    """raw confidence × factor를 [0, 100] clamp한 정수."""
    if raw <= 0:
        return 0
    return _clamp_confidence(int(round(raw * factor)))
