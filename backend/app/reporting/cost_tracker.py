"""거래 비용 추적기 (STEP 4, 2026-06-03) — 거래별 비용(거래세+수수료+슬리피지)
계산 + 일별 집계 + '비용/수익 비율'. read-only, 새 주문 0건.

단타(분 단위 회전)는 *거래 빈도가 올라갈수록* 왕복 거래비용이 수익을 잠식한다
(비용벽). 운영자가 그 비용을 *직접* 보게 한다.

비용 구성 (한국 주식, 2026 기준 추정):
  - 거래세(증권거래세): 매도 0.20% (20bps) — **매도 시에만**.
  - 위탁수수료: 매수/매도 각 ~0.015% (1.5bps) — 증권사/약정마다 다름(추정).
  - 슬리피지: 한쪽 ~0.05% (5bps) 추정 — 호가/체결 품질에 따라 변동.
  → **왕복 ≈ 33bps** (1.5×2 + 20 + 5×2). entry 100만원·exit 100만원이면 ≈ 3,300원.

★ 본 비용은 *추정* 이다 — 실제 KIS 모의/실계좌 수수료율은 약정에 따라 다르다.
  운영자가 `*_BPS` 를 자기 약정에 맞게 조정해 더 정확히 볼 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.reporting.trade_lifecycle import TradeRow


# 비용률 (bps = 0.01%). 운영자 약정에 맞게 조정 가능.
BUY_COMMISSION_BPS:  float = 1.5    # 매수 위탁수수료
SELL_COMMISSION_BPS: float = 1.5    # 매도 위탁수수료
SELL_TAX_BPS:        float = 20.0   # 증권거래세 0.20% — 매도만
SLIPPAGE_BPS_PER_SIDE: float = 5.0  # 슬리피지(한쪽) 추정

ROUND_TRIP_COST_BPS: float = (
    BUY_COMMISSION_BPS + SELL_COMMISSION_BPS + SELL_TAX_BPS + SLIPPAGE_BPS_PER_SIDE * 2
)  # = 33.0


@dataclass
class TradeCost:
    buy_commission:  int
    sell_commission: int
    sell_tax:        int
    slippage:        int
    total_cost_krw:  int

    def to_dict(self) -> dict:
        return {
            "buy_commission":  self.buy_commission,
            "sell_commission": self.sell_commission,
            "sell_tax":        self.sell_tax,
            "slippage":        self.slippage,
            "total_cost_krw":  self.total_cost_krw,
        }


def compute_trade_cost(
    *,
    buy_price:  int | float | None,
    sell_price: int | float | None,
    quantity:   int,
) -> TradeCost:
    """한 거래(매수+매도)의 추정 비용. 매도가 없으면(OPEN) 매수측 비용만.

    슬리피지는 체결된 leg 마다 한쪽씩 — 매수 1, 매도 1 (있을 때).
    """
    q = max(0, int(quantity or 0))
    buy_notional = (float(buy_price) * q) if buy_price else 0.0
    sell_notional = (float(sell_price) * q) if sell_price else 0.0

    buy_commission = round(buy_notional * BUY_COMMISSION_BPS / 1e4)
    slippage = round(buy_notional * SLIPPAGE_BPS_PER_SIDE / 1e4)
    sell_commission = 0
    sell_tax = 0
    if sell_notional > 0:
        sell_commission = round(sell_notional * SELL_COMMISSION_BPS / 1e4)
        sell_tax = round(sell_notional * SELL_TAX_BPS / 1e4)
        slippage += round(sell_notional * SLIPPAGE_BPS_PER_SIDE / 1e4)

    total = int(buy_commission + sell_commission + sell_tax + slippage)
    return TradeCost(int(buy_commission), int(sell_commission), int(sell_tax),
                     int(slippage), total)


def augment_trades_with_costs(rows: list[TradeRow]) -> list[dict]:
    """각 거래 행에 비용 + 비용후 순손익(net)을 붙인 dict 리스트.

    CLOSED: net_pnl = gross_pnl - total_cost. OPEN: 매수측 비용만, net None.
    """
    out: list[dict] = []
    for r in rows:
        cost = compute_trade_cost(buy_price=r.buy_price, sell_price=r.sell_price,
                                  quantity=r.quantity)
        d = r.to_dict()
        d["cost"] = cost.to_dict()
        d["total_cost_krw"] = cost.total_cost_krw
        if r.status == "CLOSED" and r.gross_pnl_krw is not None:
            d["net_pnl_krw"] = int(r.gross_pnl_krw - cost.total_cost_krw)
        else:
            d["net_pnl_krw"] = None
        out.append(d)
    return out


def daily_cost_summary(rows: list[TradeRow]) -> dict:
    """일별(또는 범위) 비용 집계 — 거래수 / 총비용 / 비용전 손익 / ★비용후 손익 /
    비용·수익 비율."""
    closed = [r for r in rows if r.status == "CLOSED" and r.gross_pnl_krw is not None]
    n = len(closed)
    gross = 0
    total_cost = 0
    for r in closed:
        gross += int(r.gross_pnl_krw or 0)
        total_cost += compute_trade_cost(buy_price=r.buy_price, sell_price=r.sell_price,
                                         quantity=r.quantity).total_cost_krw
    net = gross - total_cost

    # 비용/수익 비율: 비용전 손익이 양(+)일 때만 의미 — 비용이 총수익의 몇 %를 먹나.
    cost_to_gross_profit_pct = None
    if gross > 0:
        cost_to_gross_profit_pct = round(total_cost / gross * 100.0, 2)

    return {
        "closed_trades":            n,
        "total_cost_krw":           int(total_cost),
        "gross_pnl_krw":            int(gross),       # 비용 전
        "net_pnl_krw":              int(net),         # ★ 비용 후 실제 손익
        "cost_to_gross_profit_pct": cost_to_gross_profit_pct,
        "round_trip_cost_bps":      ROUND_TRIP_COST_BPS,
        "notice": (
            "비용(거래세 0.20% + 수수료 + 추정 슬리피지, 왕복 ≈ 33bps)은 추정값이며 "
            "실제 약정에 따라 다릅니다. cost_to_gross_profit_pct 가 100%에 가까울수록 "
            "거래비용이 수익을 잠식하고 있다는 뜻입니다."
        ),
    }


def estimate_cost_for_frequency(
    *,
    avg_trade_notional_krw: int | float,
    round_trips_per_day:    int,
    trading_days:           int = 1,
) -> dict:
    """거래 빈도↑ 시 예상 비용 — 단타 비용벽을 미리 보여준다.

    1 왕복(매수+매도) 비용 ≈ notional × 33bps. 빈도·일수를 곱해 추정.
    """
    rt_cost = float(avg_trade_notional_krw) * ROUND_TRIP_COST_BPS / 1e4
    per_day = rt_cost * max(0, int(round_trips_per_day))
    total = per_day * max(1, int(trading_days))
    return {
        "avg_trade_notional_krw": int(avg_trade_notional_krw),
        "round_trips_per_day":    int(round_trips_per_day),
        "trading_days":           int(trading_days),
        "round_trip_cost_krw":    round(rt_cost),
        "estimated_cost_per_day_krw": round(per_day),
        "estimated_cost_total_krw":   round(total),
        "round_trip_cost_bps":    ROUND_TRIP_COST_BPS,
    }


__all__ = [
    "TradeCost", "ROUND_TRIP_COST_BPS",
    "BUY_COMMISSION_BPS", "SELL_COMMISSION_BPS", "SELL_TAX_BPS", "SLIPPAGE_BPS_PER_SIDE",
    "compute_trade_cost", "augment_trades_with_costs", "daily_cost_summary",
    "estimate_cost_for_frequency",
]
