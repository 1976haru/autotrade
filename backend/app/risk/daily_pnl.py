"""Daily realized PnL aggregation (145 + 166, MUST).

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

166: 일자 경계 = KST. 한국 시장(09:00–15:30 KST) 운영 가정. UTC 자정(=09:00 KST
장 시작)에 카운터가 리셋되는 이전 동작은 "장 시작에 한도 0으로 시작"이 아니라
실제로 "장 직전(00:00 UTC = 08:59:59 KST 포함)까지의 청산이 오늘 PnL"이 되어
의미와 맞지 않음. KST date를 기본으로 사용하면 자연스럽게 KST 자정(00:00 KST =
15:00 UTC, 장 종료 후)에 리셋되어 운영자 직관과 일치.
"""

from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog


KST = timezone(timedelta(hours=9))


def today_utc() -> date:
    """현재 UTC date — backwards compat. 신규 호출자는 today_kst() 권장."""
    return datetime.now(timezone.utc).date()


def today_kst() -> date:
    """현재 KST date. 한국 시장 일자 경계와 일치 — max_daily_loss 카운터가
    KST 자정(=15:00 UTC, 장 종료 후)에 리셋되도록."""
    return datetime.now(KST).date()


def compute_today_realized_pnl(
    db:    Session,
    *,
    today: date | None = None,
    tz:    timezone   = KST,
) -> int:
    """오늘 실현된 손익의 누적 합. 손실은 음수.

    `today=None`이면 `tz` (기본 KST) 기준 현재 date. `tz=timezone.utc`로
    명시하면 145 이전 동작과 호환 — 테스트는 명시적 인자로 결정성 유지.
    """
    if today is None:
        today = datetime.now(tz).date()

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

        # SELL이 *오늘* (지정된 timezone date) created됐을 때만 today's
        # realized PnL에 합산. created_at은 sqlite에서 naive datetime일 수
        # 있음 — _utcnow()로 만든 값이라 UTC로 가정 후 tz로 변환.
        ts = r.created_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_date = ts.astimezone(tz).date()
        if ts_date == today:
            realized_today += sell_pnl

    return realized_today
