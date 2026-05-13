"""Paper Gate collector — read-only DB → PaperGateInput 빌더 (#72).

CLAUDE.md 절대 원칙:
- 본 모듈은 *조회만* 한다 — INSERT/UPDATE/DELETE 0건 (정적 grep 가드).
- broker / OrderExecutor / route_order / 외부 HTTP / AI SDK import 0건.
- `ENABLE_LIVE_TRADING` 등 안전 플래그 mutate 0건.

수집 대상 (read-only SELECT):
- OrderAuditLog        : trade_count / wins / losses / mdd / rejection / audit drift
- EmergencyStopEvent   : 손실한도 위반 (reason_code=daily_loss_breach) — 후속 PR
- AgentDecisionLog     : ai_low_confidence_burst 등 CAUTION 보강 — 후속 PR

본 collector는 *최선의 추정* 메트릭만 채운다. paper_vs_backtest_pf_drift /
hourly_loss_top_share 같은 고급 메트릭은 운영자가 별도로 채울 수 있다 — 본
모듈은 누락 시 None을 그대로 carry해 PaperGate evaluator가 보수적으로 처리.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OrderAuditLog
from app.governance.paper_gate import PaperGateInput


# ---------- helpers ----------


def _utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _is_paper_mode(mode: str | None) -> bool:
    if not mode:
        return False
    return mode.upper() == "PAPER"


def _is_filled(row: OrderAuditLog) -> bool:
    """체결된 주문 여부.

    audit row가 broker로 실제 전송되어 체결된 경우만 trade로 카운트. REJECTED /
    NEEDS_APPROVAL / 미체결 APPROVED는 trade가 아니다.
    """
    if str(row.decision).upper() != "APPROVED":
        return False
    if not row.executed:
        return False
    return int(row.filled_quantity or 0) > 0


def _row_pnl(row: OrderAuditLog) -> int:
    """매우 단순한 손익 추정 — `avg_fill_price - latest_price` 같은 정보가
    충분하지 않으므로 본 collector는 손익을 *계산하지 않는다*. 호출자가
    별도 trade ledger / virtual_order 결과로 채워야 한다.

    return 0 — placeholder. Paper Gate evaluator는 expectancy / winning /
    losing 합을 외부 입력으로 받는다 (운영자가 별도 도구로 산출).
    """
    return 0


# ---------- main ----------


def build_paper_gate_input(
    db: Session,
    *,
    strategy: str | None,
    period_start: datetime,
    period_end: datetime,
    initial_cash: int = 10_000_000,
    expectancy: float | None = None,
    winning_pnl_sum: int | None = None,
    losing_pnl_sum: int | None = None,
    max_drawdown_value: int | None = None,
    loss_limit_violations: int | None = None,
    audit_missing_count: int | None = None,
    stale_or_duplicate_violations: int | None = None,
    best_day_pnl_share: float | None = None,
    hourly_loss_top_share: float | None = None,
    paper_vs_backtest_pf_drift: float | None = None,
    fill_polling_consistent: bool = True,
    client_order_id_idempotent: bool = True,
) -> PaperGateInput:
    """OrderAuditLog 에서 paper 운영 기간 row를 SELECT, 메트릭 일부를 계산.

    수익 메트릭(expectancy / winning / losing / mdd)은 *별도 trade ledger* 가
    필요하므로 옵션 인자로 받는다. 호출자가 미제공 시 보수적 default(0) 사용 —
    이 경우 PaperGate 평가가 FAIL이 되는 것이 의도된 동작 (표본/지표 부족).
    """
    period_start = _utc(period_start)
    period_end   = _utc(period_end)

    stmt = select(OrderAuditLog).where(
        OrderAuditLog.created_at >= period_start,
        OrderAuditLog.created_at <= period_end,
    )
    if strategy:
        stmt = stmt.where(OrderAuditLog.strategy == strategy)

    rows = list(db.execute(stmt).scalars())
    paper_rows = [r for r in rows if _is_paper_mode(r.mode)]

    # 표본 / 카운트.
    filled = [r for r in paper_rows if _is_filled(r)]
    rejected = [r for r in paper_rows if str(r.decision).upper() == "REJECTED"]
    total    = len(paper_rows)

    # active_days — 체결된 row의 created_at date 집합.
    active_days = len({_utc(r.created_at).date() for r in filled})

    # rejection_rate — paper 전체 대비.
    rejection_rate = (len(rejected) / total) if total else 0.0

    # audit_missing — collector 입장에서는 0 (DB row 자체를 카운트하기 때문).
    # 운영자가 broker 로그 / paper exchange 응답과 reconcile 후 별도로 명시.
    audit_missing = audit_missing_count if audit_missing_count is not None else 0

    return PaperGateInput(
        strategy_name=strategy or "(all paper strategies)",
        period_start=period_start,
        period_end=period_end,
        trade_count=len(filled),
        active_days=active_days,
        winning_pnl_sum=int(winning_pnl_sum or 0),
        losing_pnl_sum=int(losing_pnl_sum or 0),
        expectancy=float(expectancy or 0.0),
        max_drawdown_value=int(max_drawdown_value or 0),
        initial_cash=int(initial_cash),
        loss_limit_violations=int(loss_limit_violations or 0),
        audit_missing_count=int(audit_missing),
        stale_or_duplicate_violations=int(stale_or_duplicate_violations or 0),
        rejection_rate=float(rejection_rate),
        best_day_pnl_share=best_day_pnl_share,
        hourly_loss_top_share=hourly_loss_top_share,
        paper_vs_backtest_pf_drift=paper_vs_backtest_pf_drift,
        fill_polling_consistent=bool(fill_polling_consistent),
        client_order_id_idempotent=bool(client_order_id_idempotent),
    )


def list_paper_strategies(
    db: Session,
    *,
    period_start: datetime,
    period_end: datetime,
) -> list[str]:
    """Paper 모드에서 활동한 strategy 이름 목록. None은 '(unnamed)' 처리.

    호출자가 strategy 별로 paper gate 평가를 반복 수행할 때 사용.
    """
    period_start = _utc(period_start)
    period_end   = _utc(period_end)
    stmt = select(OrderAuditLog).where(
        OrderAuditLog.created_at >= period_start,
        OrderAuditLog.created_at <= period_end,
    )
    rows: Iterable[OrderAuditLog] = db.execute(stmt).scalars()
    names = set()
    for r in rows:
        if not _is_paper_mode(r.mode):
            continue
        names.add(r.strategy or "(unnamed)")
    return sorted(names)
