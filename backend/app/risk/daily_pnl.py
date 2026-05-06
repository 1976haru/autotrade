"""Daily realized PnL aggregation (145, MUST).

CLAUDE.md '손실 방어 + 감사 로그 우선' — RiskPolicy.max_daily_loss 검사가
실효성을 가지려면 누군가 daily_realized_pnl 카운터를 채워야 한다. 145 이전에는
어디에서도 갱신되지 않아 사실상 무효 상태였음.

route_order가 매 주문 평가 직전에 본 모듈의 함수를 호출해 RiskManager에
당일 realized PnL을 주입한다. 운영 부하가 걱정되는 수준이 아니라 매 주문마다
audit log 한 번 walk가 부담 없는 비용 (단타 일중 거래 = 수십~수백 row).

알고리즘 (per-symbol FIFO):
- `OrderAuditLog`의 executed=True 행을 id 순으로 walk.
- (symbol)별 BUY 잔량 deque로 추적 — strategy를 무시하는 이유는 `max_daily_loss`
  가 *계좌* 전체의 손실 한도라 strategy 경계와 무관하기 때문. 144의 scoreboard
  는 *strategy 단위* 평가라 (strategy, symbol)을 키로 쓰는 것과 의도가 다르다.
- BUY는 큐 뒤에 push, SELL은 앞에서 차감하며 PnL = (sell_price - buy_price) *
  매칭수량 누적. 매칭이 발생한 SELL 부분에 대해 그 SELL의 audit row가 *오늘*
  만들어졌으면 today's realized PnL에 합산.
- 어제 BUY → 오늘 SELL (overnight position 청산)도 정상 카운트 — 청산이
  오늘 일어났으니 오늘의 realized PnL에 들어간다.
- naked SELL / leftover BUY는 144와 동일하게 스킵/무시.

`today` 인자는 UTC date — DB에 저장된 `created_at`이 UTC라 일관 비교. 한국 시장
일자 경계와는 9시간 어긋나지만 MVP 단계에서는 'PnL이 어딘가에서 잘리고 다시
시작하는 한도' 역할만 충족하면 충분. 정확한 KST 일자 경계는 향후 확장.
"""

from collections import defaultdict, deque
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def compute_today_realized_pnl(db: Session, *, today: date | None = None) -> int:
    """오늘(UTC) 실현된 손익의 누적 합. 손실은 음수.

    `today=None`이면 현재 UTC 기준. 테스트는 명시적 인자로 결정성 유지.
    """
    if today is None:
        today = today_utc()

    rows = (
        db.query(OrderAuditLog)
          .filter(
              OrderAuditLog.executed.is_(True),
              OrderAuditLog.avg_fill_price.isnot(None),
              OrderAuditLog.filled_quantity > 0,
          )
          .order_by(OrderAuditLog.id)
          .all()
    )

    buy_queue: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    realized_today = 0

    for r in rows:
        qty   = r.filled_quantity
        price = r.avg_fill_price
        if r.side == "BUY":
            buy_queue[r.symbol].append((qty, price))
            continue
        if r.side != "SELL":
            continue

        remaining = qty
        sell_pnl = 0
        q = buy_queue[r.symbol]
        while remaining > 0 and q:
            buy_qty, buy_price = q[0]
            take = min(remaining, buy_qty)
            sell_pnl += (price - buy_price) * take
            remaining -= take
            if take == buy_qty:
                q.popleft()
            else:
                q[0] = (buy_qty - take, buy_price)

        # SELL이 *오늘* created됐을 때만 today's realized PnL에 합산.
        # created_at은 naive datetime일 수 있음 (sqlite) — UTC로 가정.
        ts = r.created_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts_date = ts.date()  # naive면 UTC로 가정 — _utcnow()가 만든 값.
        else:
            ts_date = ts.astimezone(timezone.utc).date()
        if ts_date == today:
            realized_today += sell_pnl

    return realized_today
