"""Virtual Position Engine (150, MUST).

VirtualOrder의 체결 데이터에서 가상 포지션 상태를 산출. FIFO 페어매칭으로
realized PnL을 산출하고, 잔여 포지션의 unrealized PnL과 보유시간을 계산한다.

본 엔진은 *read-only 분석* 모듈 — VirtualOrder 행을 직접 수정하지 않는다.
호출자가 stop_loss/take_profit 평가 결과를 보고 새 SELL VirtualOrder를
만들기로 결정할 때만 별도 흐름으로 진행한다 (RiskManager → PermissionGate
경유 — 이 엔진은 그 흐름을 우회하지 않는다).

종료 사유 (close reason) — 포지션이 닫힐 때 산출되는 메타데이터:
- 'sell_signal'    : 일반 청산 (SELL fill로 close)
- 'stop_loss'      : 손절 임계 도달
- 'take_profit'    : 익절 임계 도달
- 'time_exit'      : 보유시간 한도 초과
- 'unknown'        : 사유 없이 닫힌 경우 (fallback)

본 엔진은 사유를 분류만 하고, 실제 SELL 주문은 caller가 만든다.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import VirtualOrder
from app.virtual.order_ledger import (
    STATUS_FILLED,
    STATUS_PARTIALLY_FILLED,
)


# 체결 단계의 status — 이들 status에서 fill 정보를 신뢰할 수 있다.
_FILL_STATUSES = (STATUS_FILLED, STATUS_PARTIALLY_FILLED)


@dataclass(frozen=True)
class OpenLot:
    """FIFO 매매에서 잔여 BUY 단위. 부분 청산되면 quantity가 줄어든다."""
    symbol:      str
    strategy:    str | None
    quantity:    int
    avg_price:   int
    opened_at:   datetime  # 첫 BUY가 들어온 시점 (보유시간 기준점)


@dataclass(frozen=True)
class PositionSummary:
    symbol:           str
    strategy:         str | None
    quantity:         int
    avg_price:        int
    last_price:       int             # 호출자가 주입한 mark price
    unrealized_pnl:   int
    unrealized_pct:   float
    hold_seconds:     float           # 첫 진입부터 now까지
    realized_pnl:     int             # 같은 (symbol, strategy)에서 누적된 실현 PnL


@dataclass(frozen=True)
class CloseEvaluation:
    """stop_loss / take_profit / time_exit 평가 결과. should_close=True이면
    호출자가 SELL 주문을 RiskManager 경유로 만들 것."""
    should_close: bool
    reason:       str   # close reason (위 enum 중 하나)


def _aggregate_fills(rows: list[VirtualOrder]) -> tuple[
    dict[tuple[str, str | None], deque[OpenLot]],
    dict[tuple[str, str | None], int],
]:
    """체결된 행들을 walk하면서 (symbol, strategy) 키별 open lots와 realized PnL.

    realized PnL: SELL fill이 BUY lot을 부분/전량 차감하면서 누적.
    open_lots: leftover BUY 잔량.
    """
    open_lots: dict[tuple[str, str | None], deque[OpenLot]] = defaultdict(deque)
    realized:  dict[tuple[str, str | None], int]            = defaultdict(int)

    # filled_at 또는 created_at 순으로 안정 정렬.
    sorted_rows = sorted(
        rows,
        key=lambda r: (r.filled_at or r.created_at, r.id),
    )
    for r in sorted_rows:
        if r.status not in _FILL_STATUSES:
            continue
        if r.avg_fill_price is None or r.filled_quantity <= 0:
            continue
        key = (r.symbol, r.strategy)
        if r.side == "BUY":
            open_lots[key].append(OpenLot(
                symbol=r.symbol,
                strategy=r.strategy,
                quantity=r.filled_quantity,
                avg_price=r.avg_fill_price,
                opened_at=r.filled_at or r.created_at,
            ))
        elif r.side == "SELL":
            remaining = r.filled_quantity
            q = open_lots[key]
            while remaining > 0 and q:
                lot = q[0]
                take = min(remaining, lot.quantity)
                realized[key] += (r.avg_fill_price - lot.avg_price) * take
                remaining -= take
                if take == lot.quantity:
                    q.popleft()
                else:
                    q[0] = OpenLot(
                        symbol=lot.symbol, strategy=lot.strategy,
                        quantity=lot.quantity - take,
                        avg_price=lot.avg_price, opened_at=lot.opened_at,
                    )
            # remaining > 0: naked SELL (open lot 없음). 무시 (ledger 정합성은
            # caller 책임 — 가상 환경에서 발생하면 운영자 분석에 audit log 활용).
    return open_lots, realized


def _summarize_lots(
    open_lots: dict[tuple[str, str | None], deque[OpenLot]],
    realized:  dict[tuple[str, str | None], int],
    *,
    last_prices: dict[str, int],  # symbol → mark price
    now:         datetime,
) -> list[PositionSummary]:
    """open lots를 (symbol, strategy)별 단일 PositionSummary로 합산.

    한 키에 여러 lot이 있으면 weighted avg + 가장 오래된 opened_at 기준.
    """
    out: list[PositionSummary] = []
    for key, lots in open_lots.items():
        if not lots:
            continue
        symbol, strategy = key
        total_qty = sum(l.quantity for l in lots)
        if total_qty <= 0:
            continue
        weighted_total = sum(l.quantity * l.avg_price for l in lots)
        avg_price = int(round(weighted_total / total_qty))
        opened_at = min(l.opened_at for l in lots)
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)

        last_price = last_prices.get(symbol, avg_price)
        unrealized = (last_price - avg_price) * total_qty
        unrealized_pct = (last_price - avg_price) / avg_price if avg_price else 0.0
        hold_secs = max(0.0, (now - opened_at).total_seconds())

        out.append(PositionSummary(
            symbol=symbol, strategy=strategy,
            quantity=total_qty, avg_price=avg_price,
            last_price=last_price,
            unrealized_pnl=unrealized,
            unrealized_pct=unrealized_pct,
            hold_seconds=hold_secs,
            realized_pnl=realized.get(key, 0),
        ))
    return out


def compute_open_positions(
    db:           Session,
    *,
    last_prices:  dict[str, int] | None = None,
    now:          datetime | None = None,
) -> list[PositionSummary]:
    """가상 보유 포지션 요약 — VirtualOrder의 체결 행을 FIFO 페어매칭."""
    if last_prices is None:
        last_prices = {}
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = db.query(VirtualOrder).all()
    open_lots, realized = _aggregate_fills(rows)
    return _summarize_lots(open_lots, realized,
                           last_prices=last_prices, now=now)


def evaluate_close(
    pos:                PositionSummary,
    *,
    stop_loss_pct:      float | None = None,
    take_profit_pct:    float | None = None,
    max_hold_seconds:   float | None = None,
) -> CloseEvaluation:
    """포지션이 청산 임계에 도달했는지 평가. None인 임계는 검사하지 않음.

    - stop_loss_pct는 양수로 표기 (예: 2.0 = -2%). unrealized_pct ≤ -threshold.
    - take_profit_pct는 양수 (예: 5.0 = +5%). unrealized_pct ≥ +threshold.
    - max_hold_seconds: hold_seconds 초과 시 시간 청산.
    """
    if stop_loss_pct is not None and stop_loss_pct > 0:
        if pos.unrealized_pct <= -(stop_loss_pct / 100):
            return CloseEvaluation(should_close=True, reason="stop_loss")
    if take_profit_pct is not None and take_profit_pct > 0:
        if pos.unrealized_pct >= (take_profit_pct / 100):
            return CloseEvaluation(should_close=True, reason="take_profit")
    if max_hold_seconds is not None and max_hold_seconds > 0:
        if pos.hold_seconds >= max_hold_seconds:
            return CloseEvaluation(should_close=True, reason="time_exit")
    return CloseEvaluation(should_close=False, reason="unknown")
