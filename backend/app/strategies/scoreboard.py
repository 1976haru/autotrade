"""Strategy scoreboard (137, MUST).

전략별 누적 성과 — *전체 DB*의 BacktestRun 집계. 117의 frontend
BacktestStrategyMiniTable은 view-time filtered 데이터 기준이라 view마다
달라지지만, 본 모듈은 운영자가 신뢰할 수 있는 누적 진실을 산출한다.

LIVE 주문의 strategy 정보는 현재 OrderAuditLog에 컬럼이 없어 따로
집계되지 않는다 — 향후 OrderAuditLog.strategy 컬럼 추가 후 본 모듈에서
같이 합산하도록 확장 (TODO 137-followup).

반환 shape (per strategy):
    {
        "strategy":   str,
        "runs":       int,            # backtest runs 수
        "total_pnl":  int,
        "avg_pnl":    int,
        "best_pnl":   int,
        "worst_pnl":  int,
        "wins":       int,
        "losses":     int,
        "win_rate":   float,          # 0..1
    }

전체 정렬 — total_pnl desc.
"""

from sqlalchemy.orm import Session

from app.db.models import BacktestRun


def compute_strategy_scoreboard(db: Session) -> list[dict]:
    rows = db.query(BacktestRun).all()
    by_strategy: dict[str, dict] = {}

    for r in rows:
        s = r.strategy or "(unknown)"
        cur = by_strategy.setdefault(s, {
            "strategy":   s,
            "runs":       0,
            "total_pnl":  0,
            "best_pnl":   None,
            "worst_pnl":  None,
            "wins":       0,
            "losses":     0,
        })
        cur["runs"]      += 1
        pnl              = r.total_pnl or 0
        cur["total_pnl"] += pnl
        cur["wins"]      += r.win_count or 0
        cur["losses"]    += r.loss_count or 0
        cur["best_pnl"]  = pnl if cur["best_pnl"]  is None else max(cur["best_pnl"],  pnl)
        cur["worst_pnl"] = pnl if cur["worst_pnl"] is None else min(cur["worst_pnl"], pnl)

    out = []
    for cur in by_strategy.values():
        runs = cur["runs"]
        trades = cur["wins"] + cur["losses"]
        out.append({
            "strategy":   cur["strategy"],
            "runs":       runs,
            "total_pnl":  cur["total_pnl"],
            "avg_pnl":    int(round(cur["total_pnl"] / runs)) if runs else 0,
            "best_pnl":   cur["best_pnl"]  or 0,
            "worst_pnl":  cur["worst_pnl"] or 0,
            "wins":       cur["wins"],
            "losses":     cur["losses"],
            "win_rate":   (cur["wins"] / trades) if trades > 0 else 0.0,
        })
    out.sort(key=lambda x: x["total_pnl"], reverse=True)
    return out
