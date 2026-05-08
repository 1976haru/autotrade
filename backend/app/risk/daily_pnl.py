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


def count_orders_today_kst(
    db:    Session,
    *,
    today: date | None = None,
) -> int:
    """오늘(KST) 만들어진 OrderAuditLog 행 개수. 183 max_orders_per_day 가드용.

    decision 무관 — REJECTED / NEEDS_APPROVAL / APPROVED 모두 카운트. 운영자가
    시스템 폭주를 인지하는 게 핵심이라 거부 카운트도 포함.
    """
    if today is None:
        today = today_kst()

    rows = db.query(OrderAuditLog.created_at).all()
    count = 0
    for (ts,) in rows:
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.astimezone(KST).date() == today:
            count += 1
    return count


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


# ----------------------------------------------------------------------
# #36: Weekly + consecutive loss aggregations
# ----------------------------------------------------------------------
#
# 본 모듈의 일일 PnL 알고리즘과 동일 — symbol별 FIFO BUY queue로 SELL과
# 매칭. 차이는 "오늘" 필터를 "이번 주" / "마지막 trailing N건" 으로 바꾼다.
# 본 함수들은 읽기 전용 — DB write 없음, broker 호출 없음.
#
# **실시간 손익 계산 주의** (docs/loss_limit_policy.md §6):
# - realized PnL만 계산 — unrealized(평가손익) 미포함.
# - virtual / paper / live 분리는 audit row의 mode 컬럼으로 구분 가능하지만
#   본 함수는 모드 무관 — 호출자가 필요 시 mode 필터를 outer query로 적용.
# - 수수료 / 세금은 미반영 — 단순 (sell_price - buy_price) × qty. 실제 LIVE에선
#   broker statement와 reconciliation 필수 (#212 참고).


def week_start_kst(today: date | None = None) -> date:
    """이번 주 월요일 00:00 KST의 date. 운영자/문서 직관과 일치 (한국 거래주 = 월~금)."""
    if today is None:
        today = today_kst()
    # weekday(): Monday=0 ... Sunday=6
    return today - timedelta(days=today.weekday())


def compute_weekly_realized_pnl_kst(
    db:    Session,
    *,
    today: date | None = None,
) -> int:
    """이번 주(월요일 00:00 KST 시작) 누적 realized PnL. 손실은 음수.

    `compute_today_realized_pnl`과 동일한 FIFO 매칭, "오늘" 필터를 "이번 주"로
    교체. SELL이 이번 주(KST date 기준)에 created됐을 때만 합산.
    """
    if today is None:
        today = today_kst()
    week_start = week_start_kst(today)

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
    realized_week = 0

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

        ts = r.created_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_date = ts.astimezone(KST).date()
        if week_start <= ts_date <= today:
            realized_week += sell_pnl

    return realized_week


def count_consecutive_losing_trades(
    db:        Session,
    *,
    lookback:  int = 50,
) -> int:
    """가장 최근의 SELL 매칭(=closed trade)부터 연속해서 손실인 거래 수.

    예: 최근 SELL부터 역순으로 (lose, lose, win, lose, lose) → 2.
    이익(>=0)이 등장하면 거기서 멈춘다.

    `lookback`이 너무 작으면 의미 있는 카운트가 안 나올 수 있어 default 50.
    수수료/세금 미반영 — pnl == 0인 break-even은 손실로 분류하지 않음 (>0 not
    required, exact 0은 win 쪽으로 묶이지 않고 별도, 단순 < 0 검사).

    SELL이 BUY와 매칭되지 않은 naked SELL은 무시 (matched_qty=0이면 skip).
    부분 매칭(예: SELL 10주 중 7주만 매칭)은 매칭된 부분의 PnL로만 평가.
    """
    if lookback <= 0:
        return 0

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

    # 1패스: 모든 closed trade의 PnL을 시간순으로 모은다.
    buy_queue: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    closed_trade_pnls: list[int] = []

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
        matched   = 0
        q = buy_queue[r.symbol]
        while remaining > 0 and q:
            buy_qty, buy_price = q[0]
            take = min(remaining, buy_qty)
            sell_pnl += (price - buy_price) * take
            matched  += take
            remaining -= take
            if take == buy_qty:
                q.popleft()
            else:
                q[0] = (buy_qty - take, buy_price)
        if matched > 0:
            closed_trade_pnls.append(sell_pnl)

    # 2패스: 뒤에서부터 trailing — pnl < 0인 동안 카운트.
    tail = closed_trade_pnls[-lookback:]
    count = 0
    for pnl in reversed(tail):
        if pnl < 0:
            count += 1
        else:
            break
    return count
