"""P-18: 가상 포트폴리오 상태 집계 (read-only, 표시 전용).

AI Paper / Paper 운용의 현재 자금 + 보유 상태를 사용자가 한눈에 볼 수 있도록
*기존 상태* 를 조합해 단일 snapshot 으로 만든다. 본 모듈은:

- CapitalState(현금/투자원금) + PaperCapitalConfig(한도) + position_engine
  (FIFO 보유 포지션) + loss_limits(오늘 매수 사용금액) 를 *read-only* 로 읽어
  종합한다.
- broker / OrderExecutor / route_order 호출 0건.
- DB 는 SELECT only (INSERT/UPDATE/DELETE 0건).
- 안전 flag 변경 0건.
- secret / API key / 계좌번호 carry 0건.
- `PortfolioStateSnapshot.is_live_authorization=False` /
  `is_order_signal=False` / `contains_secret=False` 불변.

**본 화면은 Paper / 가상 포트폴리오이며 실제 계좌 잔고가 아니다.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.auto_paper.capital_config import (
    get_paper_capital_config,
    resolve_daily_buy_limit,
    resolve_symbol_weight_limit_pct,
)
from app.auto_paper.capital_state import get_capital_state
from app.db.models import VirtualOrder
from app.risk.loss_limits import calculate_today_buy_used_amount
from app.virtual.position_engine import compute_open_positions

# 종목 비중 상태 임계 — 최대 비중의 80% 이상은 WARN, 초과는 EXCEEDED.
_WEIGHT_WARN_RATIO = 0.8


def _weight_status(weight_pct: float, max_pct: float) -> str:
    if max_pct <= 0:
        return "OK"
    if weight_pct > max_pct:
        return "EXCEEDED"
    if weight_pct >= max_pct * _WEIGHT_WARN_RATIO:
        return "WARN"
    return "OK"


@dataclass(frozen=True)
class PortfolioStateSnapshot:
    """가상 포트폴리오 상태 — UI 안전 payload. secret 0건."""

    starting_cash:             int
    current_cash:              int
    positions:                 list[dict[str, Any]]
    total_position_value:      int
    total_equity:              int
    total_unrealized_pnl:      int
    total_unrealized_pnl_pct:  float
    today_buy_used_amount:     int
    remaining_daily_buy_amount: int
    max_daily_buy_amount:      int
    max_positions:             int
    max_symbol_weight_pct:     float
    position_count:            int
    available_position_slots:  int

    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False
    contains_secret:       bool = False
    is_paper_only:         bool = True

    # diagnostics carry.
    realized_pnl:          int = 0
    invested_krw:          int = 0
    buy_count:             int = 0
    sell_count:            int = 0
    last_event_at:         str | None = None
    notes:                 list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("is_order_signal must be False")
        if self.auto_apply_allowed is not False:
            raise ValueError("auto_apply_allowed must be False")
        if self.is_live_authorization is not False:
            raise ValueError("is_live_authorization must be False")
        if self.contains_secret is not False:
            raise ValueError("contains_secret must be False")

    def to_dict(self) -> dict[str, Any]:
        return {
            "starting_cash":              int(self.starting_cash),
            "current_cash":               int(self.current_cash),
            "positions":                  list(self.positions),
            "total_position_value":       int(self.total_position_value),
            "total_equity":               int(self.total_equity),
            "total_unrealized_pnl":       int(self.total_unrealized_pnl),
            "total_unrealized_pnl_pct":   float(self.total_unrealized_pnl_pct),
            "today_buy_used_amount":      int(self.today_buy_used_amount),
            "remaining_daily_buy_amount": int(self.remaining_daily_buy_amount),
            "max_daily_buy_amount":       int(self.max_daily_buy_amount),
            "max_positions":              int(self.max_positions),
            "max_symbol_weight_pct":      float(self.max_symbol_weight_pct),
            "position_count":             int(self.position_count),
            "available_position_slots":   int(self.available_position_slots),
            "realized_pnl":               int(self.realized_pnl),
            "invested_krw":               int(self.invested_krw),
            "buy_count":                  int(self.buy_count),
            "sell_count":                 int(self.sell_count),
            "last_event_at":              self.last_event_at,
            "notes":                      list(self.notes),
            "is_order_signal":            False,
            "auto_apply_allowed":         False,
            "is_live_authorization":      False,
            "contains_secret":            False,
            "is_paper_only":              True,
        }


def _today_buy_used(db, *, now=None) -> int:
    """VirtualOrder 행을 read-only 로 읽어 오늘(확정 BUY) 사용금액 계산."""
    try:
        rows = db.query(VirtualOrder).all()
    except Exception:  # noqa: BLE001 — DB 미초기화 등.
        return 0
    entries: list[dict[str, Any]] = []
    for o in rows:
        created = getattr(o, "created_at", None)
        created_iso = created.isoformat() if created is not None and hasattr(
            created, "isoformat") else created
        price = (getattr(o, "avg_fill_price", None)
                 or getattr(o, "requested_price", None)
                 or getattr(o, "limit_price", None) or 0)
        qty = getattr(o, "filled_quantity", 0) or getattr(o, "quantity", 0) or 0
        entries.append({
            "side":       getattr(o, "side", None),
            "status":     getattr(o, "status", None),
            "created_at": created_iso,
            "price":      price,
            "quantity":   qty,
        })
    return calculate_today_buy_used_amount(entries, now=now)


def build_portfolio_state(
    db,
    *,
    last_prices: dict[str, int] | None = None,
    max_daily_buy_amount: int | None = None,
    max_symbol_weight_pct: float | None = None,
    max_positions: int | None = None,
    now=None,
) -> PortfolioStateSnapshot:
    """현재 가상 포트폴리오 상태 snapshot 을 조합 (read-only).

    Args:
        db: SQLAlchemy session (SELECT only).
        last_prices: symbol → 현재가(KRW). 미지정 시 평균단가를 mark 로 사용
            (unrealized=0). position_engine 과 동일 규약.
        max_daily_buy_amount / max_symbol_weight_pct / max_positions: 한도 override
            (미지정 시 capital_config resolve 기본값).
    """
    snap = get_capital_state().snapshot()
    cfg = get_paper_capital_config()

    starting_cash = int(snap.initial_cash_krw)
    current_cash = int(snap.available_cash_krw)

    if max_symbol_weight_pct is None:
        max_symbol_weight_pct = resolve_symbol_weight_limit_pct()[0]
    if max_daily_buy_amount is None:
        max_daily_buy_amount = resolve_daily_buy_limit()[0]
    if max_positions is None:
        max_positions = int(cfg.max_concurrent_positions)

    raw = compute_open_positions(db, last_prices=last_prices, now=now)

    positions: list[dict[str, Any]] = []
    total_position_value = 0
    total_cost_basis = 0
    total_unrealized = 0
    for p in raw:
        if int(p.quantity) <= 0:
            continue
        mark = int(p.last_price) if (p.last_price and p.last_price > 0) else int(p.avg_price)
        market_value = mark * int(p.quantity)
        cost_basis = int(p.avg_price) * int(p.quantity)
        total_position_value += market_value
        total_cost_basis += cost_basis
        total_unrealized += int(p.unrealized_pnl)
        positions.append({
            "symbol":             p.symbol,
            "strategy":           p.strategy,
            "quantity":           int(p.quantity),
            "average_price":      int(p.avg_price),
            "current_price":      mark,
            "market_value":       market_value,
            "cost_basis":         cost_basis,
            "unrealized_pnl":     int(p.unrealized_pnl),
            "unrealized_pnl_pct": float(p.unrealized_pct),
            "realized_pnl":       int(p.realized_pnl),
            # weight 는 total_equity 확정 후 채움.
            "portfolio_weight_pct":  0.0,
            "max_symbol_weight_pct": float(max_symbol_weight_pct),
            "symbol_weight_status":  "OK",
        })

    total_equity = current_cash + total_position_value
    for pos in positions:
        weight = (pos["market_value"] / total_equity) if total_equity > 0 else 0.0
        pos["portfolio_weight_pct"] = float(weight)
        pos["symbol_weight_status"] = _weight_status(weight, float(max_symbol_weight_pct))

    total_unrealized_pct = (
        total_unrealized / total_cost_basis if total_cost_basis > 0 else 0.0
    )

    today_used = _today_buy_used(db, now=now)
    remaining_daily = max(0, int(max_daily_buy_amount) - int(today_used))
    position_count = len(positions)
    available_slots = max(0, int(max_positions) - position_count)

    return PortfolioStateSnapshot(
        starting_cash=starting_cash,
        current_cash=current_cash,
        positions=positions,
        total_position_value=total_position_value,
        total_equity=total_equity,
        total_unrealized_pnl=total_unrealized,
        total_unrealized_pnl_pct=float(total_unrealized_pct),
        today_buy_used_amount=int(today_used),
        remaining_daily_buy_amount=remaining_daily,
        max_daily_buy_amount=int(max_daily_buy_amount),
        max_positions=int(max_positions),
        max_symbol_weight_pct=float(max_symbol_weight_pct),
        position_count=position_count,
        available_position_slots=available_slots,
        realized_pnl=int(snap.realized_pnl_krw),
        invested_krw=int(snap.invested_krw),
        buy_count=int(snap.buy_count),
        sell_count=int(snap.sell_count),
        last_event_at=snap.last_event_at,
    )


__all__ = [
    "PortfolioStateSnapshot",
    "build_portfolio_state",
]
