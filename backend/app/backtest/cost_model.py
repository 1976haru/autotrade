"""거래비용 모델 — 백테스트 전용 (CHECKLIST-04).

고정 비용 (변경/최적화 금지):
  - 매수 수수료 0.015% (1.5bps) / 매도 수수료 0.015% (1.5bps)
  - 거래세 0.18% (18bps) — 매도 시에만
  - slippage 0.05% (5bps) — 한쪽(side) 기준
  - stress: 7 / 10 / 15 bps

본 모듈은 broker / 주문 라우터 / 주문 실행기 / KIS API import 0건 — 순수 비용 계산.
intrabar_execution.CostModel 과 동일 파라미터(단일 진실은 본 파일의 상수).
"""

from __future__ import annotations

from dataclasses import dataclass

COMMISSION_BPS = 1.5      # per side
TAX_BPS = 18.0           # sell leg only
SLIPPAGE_BPS = 5.0       # per side
STRESS_SLIPPAGES_BPS: tuple[float, ...] = (5.0, 7.0, 10.0, 15.0)


@dataclass(frozen=True)
class BacktestCostModel:
    commission_bps: float = COMMISSION_BPS
    tax_bps: float = TAX_BPS
    slippage_bps: float = SLIPPAGE_BPS

    def round_trip_cost_bps(self) -> float:
        """왕복 비용(bps) — 매수/매도 수수료 + 매도세 + 양쪽 슬리피지."""
        return self.commission_bps * 2 + self.tax_bps + self.slippage_bps * 2

    def with_slippage(self, slippage_bps: float) -> "BacktestCostModel":
        return BacktestCostModel(self.commission_bps, self.tax_bps, float(slippage_bps))


DEFAULT_COST = BacktestCostModel()


def cost_drag_bps(model: BacktestCostModel | None = None) -> float:
    """1거래 왕복 비용 drag(bps)."""
    return (model or DEFAULT_COST).round_trip_cost_bps()


def apply_round_trip_cost(side: str, entry: float, exit_px: float,
                          model: BacktestCostModel | None = None) -> dict[str, float]:
    """per-share gross/net + 비용 분해 (BUY/SELL). intrabar_execution 과 동일 식."""
    m = model or DEFAULT_COST
    s = side.upper()
    gross = (exit_px - entry) if s == "BUY" else (entry - exit_px)
    slippage = (entry + exit_px) * (m.slippage_bps / 1e4)
    commission = (entry + exit_px) * (m.commission_bps / 1e4)
    sell_leg = exit_px if s == "BUY" else entry
    tax = sell_leg * (m.tax_bps / 1e4)
    net = gross - slippage - commission - tax
    return {"gross": gross, "slippage": slippage, "commission": commission,
            "tax": tax, "net": net}
