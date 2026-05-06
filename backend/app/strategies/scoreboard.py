"""Strategy scoreboard (137 + 144 + 147).

전략별 누적 성과 — *전체 DB*의 BacktestRun + OrderAuditLog (LIVE 체결분) 합산.

137 phase는 BacktestRun만 집계했고, 144에서 OrderAuditLog (executed=True +
strategy 채워진 행)을 FIFO 페어매칭으로 realized PnL까지 산출해 같이 surface한다.

응답 shape (per strategy) — 144에서 live_* 필드 추가:

    {
        "strategy":   str,

        # backtest aggregate (137)
        "runs":       int,
        "total_pnl":  int,            # backtest 누적 손익
        "avg_pnl":    int,
        "best_pnl":   int,
        "worst_pnl":  int,
        "wins":       int,
        "losses":     int,
        "win_rate":   float,

        # live aggregate (144) — LIVE 체결의 BUY/SELL FIFO 페어매칭
        "live_trades":   int,
        "live_pnl":      int,
        "live_wins":     int,
        "live_losses":   int,
        "live_win_rate": float,
    }

정렬 — `total_pnl + live_pnl` desc. 운영자가 backtest와 live 결과를 한 번에 비교
한다는 가정. backtest만 있는 전략과 live만 있는 전략이 섞여 있어도 같은 표에
나오도록 합산 정렬한다.
"""

from collections import defaultdict, deque
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import BacktestRun, OrderAuditLog


def _parse_iso(s: str | None) -> datetime | None:
    """trades_json의 entry_ts/exit_ts는 isoformat 문자열로 직렬화돼 있다."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _trade_metrics(trades: list[dict]) -> dict:
    """단일 BacktestRun의 trades_json에서 147 metric 산출.

    - hold_time_seconds: 거래의 (exit_ts - entry_ts) 평균 (초). 파싱 실패 시 0.
    - profit_factor_components: gross_win / gross_loss 누적용 (PF는 strategy 단위
      합산 후 계산하므로 여기서는 분자/분모만 반환).
    - max_consecutive_loss: 연속 손실 거래 수의 최대값.
    """
    gross_win  = 0
    gross_loss = 0
    hold_secs_total = 0.0
    hold_count = 0
    cur_consec = 0
    max_consec = 0

    for t in trades:
        pnl = t.get("pnl", 0)
        if pnl > 0:
            gross_win += pnl
            cur_consec = 0
        else:
            # 본전 0도 손실 측 — wins/losses의 분류와 일치.
            gross_loss += -pnl
            cur_consec += 1
            if cur_consec > max_consec:
                max_consec = cur_consec

        entry_ts = _parse_iso(t.get("entry_ts"))
        exit_ts  = _parse_iso(t.get("exit_ts"))
        if entry_ts is not None and exit_ts is not None and exit_ts > entry_ts:
            hold_secs_total += (exit_ts - entry_ts).total_seconds()
            hold_count += 1

    return {
        "gross_win":          gross_win,
        "gross_loss":         gross_loss,
        "hold_secs_total":    hold_secs_total,
        "hold_count":         hold_count,
        "max_consecutive_loss": max_consec,
    }


def _backtest_aggregate(db: Session) -> dict[str, dict]:
    """BacktestRun 기반 strategy별 집계 (기존 137 동작 + 147 metric).

    147 추가:
    - gross_win / gross_loss 누적 → strategy 단위의 profit_factor 산출 가능.
    - hold_secs_total / hold_count → avg_hold_time_seconds 산출.
    - max_consecutive_loss → 모든 run의 trades를 연속체로 보고 max — run마다
      다시 시작하면 진정한 strategy 수준의 worst 연속 손실을 놓치므로 단순화로
      run별 max의 max를 가져간다 (운영 의미: "어떤 run에서든 N회 연속 패가
      있었다"는 워닝).
    """
    rows = db.query(BacktestRun).all()
    by_strategy: dict[str, dict] = {}

    for r in rows:
        s = r.strategy or "(unknown)"
        cur = by_strategy.setdefault(s, {
            "runs":       0,
            "total_pnl":  0,
            "best_pnl":   None,
            "worst_pnl":  None,
            "wins":       0,
            "losses":     0,
            "gross_win":  0,
            "gross_loss": 0,
            "hold_secs_total": 0.0,
            "hold_count":      0,
            "max_consecutive_loss": 0,
        })
        cur["runs"]      += 1
        pnl              = r.total_pnl or 0
        cur["total_pnl"] += pnl
        cur["wins"]      += r.win_count or 0
        cur["losses"]    += r.loss_count or 0
        cur["best_pnl"]  = pnl if cur["best_pnl"]  is None else max(cur["best_pnl"],  pnl)
        cur["worst_pnl"] = pnl if cur["worst_pnl"] is None else min(cur["worst_pnl"], pnl)

        m = _trade_metrics(r.trades_json or [])
        cur["gross_win"]        += m["gross_win"]
        cur["gross_loss"]       += m["gross_loss"]
        cur["hold_secs_total"]  += m["hold_secs_total"]
        cur["hold_count"]       += m["hold_count"]
        if m["max_consecutive_loss"] > cur["max_consecutive_loss"]:
            cur["max_consecutive_loss"] = m["max_consecutive_loss"]
    return by_strategy


def _audit_decision_aggregate(db: Session) -> dict[str, dict]:
    """OrderAuditLog의 decision 분포 — strategy 단위의 approval/rejection rate 산출.

    NEEDS_APPROVAL은 '아직 결정 안 남' 상태라 결정율 계산의 분모에 넣지 않는다.
    (분모 = APPROVED + REJECTED, 분자 = 각 카운트)
    strategy=NULL 행은 어디에도 귀속할 수 없으므로 스킵.
    """
    rows = (
        db.query(OrderAuditLog.strategy, OrderAuditLog.decision)
          .filter(OrderAuditLog.strategy.isnot(None))
          .all()
    )
    out: dict[str, dict] = defaultdict(lambda: {
        "approved": 0, "rejected": 0, "needs_approval": 0,
    })
    for strategy, decision in rows:
        cur = out[strategy]
        if decision == "APPROVED":
            cur["approved"] += 1
        elif decision == "REJECTED":
            cur["rejected"] += 1
        elif decision == "NEEDS_APPROVAL":
            cur["needs_approval"] += 1
    return dict(out)


def compute_live_strategy_pnl(db: Session) -> dict[str, dict]:
    """LIVE 체결 audit row를 strategy/symbol별 FIFO 페어매칭하여 realized PnL 산출.

    조건:
    - `executed = True` (broker가 실제로 체결했음을 audit가 기록)
    - `strategy is not None` (어느 전략 소속인지 식별)
    - `avg_fill_price` 존재 (체결가 미상이면 PnL 계산 불가 — 스킵)
    - `filled_quantity > 0`

    페어매칭 알고리즘:
    - (strategy, symbol)별로 BUY 큐를 deque로 관리.
    - BUY 행은 (qty, price)를 큐 뒤에 push.
    - SELL 행은 큐 앞에서 BUY 잔량을 차감하면서 PnL = (sell_price - buy_price) * 부분수량 누적.
    - 매칭된 SELL의 누적 PnL이 해당 SELL의 "trade" 1건 (양수면 win, 음수면 loss).
    - 미체결 SELL(잔량 BUY 없는데 SELL) 부분은 PnL 0으로 처리하고 trade 카운트에 영향 X (operator
      noise — 실거래에서 발생 시 Strategy Scoreboard에서 "이상 신호"로 별도 추적될 수 있다).
    - leftover BUY는 unrealized이므로 집계 X.

    반환: { strategy: { trades, pnl, wins, losses } } — strategy="(unknown)"는 strategy=None
    행은 처음부터 제외했으므로 발생하지 않는다.
    """
    rows = (
        db.query(OrderAuditLog)
          .filter(
              OrderAuditLog.executed.is_(True),
              OrderAuditLog.strategy.isnot(None),
              OrderAuditLog.avg_fill_price.isnot(None),
              OrderAuditLog.filled_quantity > 0,
          )
          .order_by(OrderAuditLog.id)
          .all()
    )

    # strategy/symbol별 BUY 잔량 큐. (qty, price) 페어를 FIFO로 관리.
    buy_queue: dict[tuple[str, str], deque[tuple[int, int]]] = defaultdict(deque)
    out: dict[str, dict] = defaultdict(lambda: {
        "trades": 0, "pnl": 0, "wins": 0, "losses": 0,
    })

    for r in rows:
        key = (r.strategy, r.symbol)
        qty = r.filled_quantity
        price = r.avg_fill_price
        if r.side == "BUY":
            buy_queue[key].append((qty, price))
            continue

        if r.side != "SELL":
            # 알 수 없는 side — 보수적으로 스킵.
            continue

        remaining = qty
        sell_pnl = 0
        matched_any = False
        q = buy_queue[key]
        while remaining > 0 and q:
            buy_qty, buy_price = q[0]
            take = min(remaining, buy_qty)
            sell_pnl += (price - buy_price) * take
            remaining -= take
            matched_any = True
            if take == buy_qty:
                q.popleft()
            else:
                q[0] = (buy_qty - take, buy_price)

        if matched_any:
            cur = out[r.strategy]
            cur["trades"] += 1
            cur["pnl"]    += sell_pnl
            if sell_pnl > 0:
                cur["wins"] += 1
            else:
                cur["losses"] += 1
        # remaining > 0 — naked SELL 부분은 무시 (집계에 영향 X).

    return dict(out)


def compute_strategy_scoreboard(db: Session) -> list[dict]:
    backtest = _backtest_aggregate(db)
    live     = compute_live_strategy_pnl(db)
    audit    = _audit_decision_aggregate(db)

    all_strategies = set(backtest) | set(live) | set(audit)
    out: list[dict] = []
    for s in all_strategies:
        bt = backtest.get(s, {
            "runs": 0, "total_pnl": 0,
            "best_pnl": None, "worst_pnl": None,
            "wins": 0, "losses": 0,
            "gross_win": 0, "gross_loss": 0,
            "hold_secs_total": 0.0, "hold_count": 0,
            "max_consecutive_loss": 0,
        })
        lv = live.get(s, {"trades": 0, "pnl": 0, "wins": 0, "losses": 0})
        au = audit.get(s, {"approved": 0, "rejected": 0, "needs_approval": 0})

        runs = bt["runs"]
        bt_trades = bt["wins"] + bt["losses"]
        lv_trades = lv["wins"] + lv["losses"]

        # 147: profit_factor — gross_win / gross_loss. 손실이 0이면 +∞ 위험이라 None.
        if bt["gross_loss"] > 0:
            profit_factor = bt["gross_win"] / bt["gross_loss"]
        else:
            profit_factor = None

        # 147: expectancy = avg_pnl_per_trade. (wins+losses=0이면 0).
        if bt_trades > 0:
            expectancy = (bt["gross_win"] - bt["gross_loss"]) / bt_trades
        else:
            expectancy = 0.0

        # 147: avg_hold_time_seconds — 파싱된 trade 수 분모. 0이면 0.
        if bt["hold_count"] > 0:
            avg_hold = bt["hold_secs_total"] / bt["hold_count"]
        else:
            avg_hold = 0.0

        # 147: rejection / approval rate — denominator는 APPROVED + REJECTED.
        decided = au["approved"] + au["rejected"]
        if decided > 0:
            approval_rate  = au["approved"] / decided
            rejection_rate = au["rejected"] / decided
        else:
            approval_rate  = 0.0
            rejection_rate = 0.0

        out.append({
            "strategy":   s,
            "runs":       runs,
            "total_pnl":  bt["total_pnl"],
            "avg_pnl":    int(round(bt["total_pnl"] / runs)) if runs else 0,
            "best_pnl":   bt["best_pnl"]  or 0,
            "worst_pnl":  bt["worst_pnl"] or 0,
            "wins":       bt["wins"],
            "losses":     bt["losses"],
            "win_rate":   (bt["wins"] / bt_trades) if bt_trades > 0 else 0.0,
            # 144: live aggregates
            "live_trades":   lv["trades"],
            "live_pnl":      lv["pnl"],
            "live_wins":     lv["wins"],
            "live_losses":   lv["losses"],
            "live_win_rate": (lv["wins"] / lv_trades) if lv_trades > 0 else 0.0,
            # 147: extended metrics
            "expectancy":              expectancy,
            "profit_factor":           profit_factor,
            "avg_hold_time_seconds":   avg_hold,
            "max_consecutive_loss":    bt["max_consecutive_loss"],
            "approved_orders":         au["approved"],
            "rejected_orders":         au["rejected"],
            "pending_orders":          au["needs_approval"],
            "approval_rate":           approval_rate,
            "rejection_rate":          rejection_rate,
        })
    out.sort(key=lambda x: x["total_pnl"] + x["live_pnl"], reverse=True)
    return out
