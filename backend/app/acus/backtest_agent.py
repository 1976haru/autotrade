"""ACUS Stage 02 — BacktestAgent (1번째 필터).

Walk-Forward Universe Backtest: Train 40거래일 / Validate 20거래일 분리, 4전략 +
Agent Council 적용(기존 ``run_strategy_council_backtest`` 재사용), 거래비용 100%
반영(왕복 26bps — ``types.ROUND_TRIP_COST_FRACTION``).

종목 분류:
  ROBUST     : Train PF ≥ 1.2 AND Validate PF ≥ 1.0
  DECAYED    : Train PF ≥ 1.2 AND Validate PF < 0.8 (과적합)
  CONSISTENT : Train PF 1.0~1.2 AND Validate PF 0.9~1.1
  REJECTED   : 나머지

PF 임계는 *gross* council profit_factor 기준(spec 그대로)이되, 거래비용 반영
``cost_adjusted_avg_return`` 을 함께 산출해 비용 후 음전 시 ``cost_fragile`` 플래그를
세운다(메모리상 '비용이 단일 전략 엣지를 잠식' 사례 — 정직하게 표기).
"""

from __future__ import annotations

from typing import Any

from app.acus import types as T
from app.acus.bar_utils import split_train_validate
from app.acus.deps import PipelineDeps, default_deps

TRAIN_DAYS = 40
VALIDATE_DAYS = 20
MIN_TRADES = 5  # train/validate 각각 최소 BUY 신호 수(미만 → INSUFFICIENT)


def _cost_adjusted_return(avg_return: float | None) -> float | None:
    if avg_return is None:
        return None
    return round(avg_return - T.ROUND_TRIP_COST_FRACTION, 6)


def _classify(train_pf: float | None, validate_pf: float | None,
              train_trades: int, validate_trades: int) -> str:
    if train_pf is None or validate_pf is None:
        return T.BT_INSUFFICIENT
    if train_trades < MIN_TRADES or validate_trades < MIN_TRADES:
        return T.BT_INSUFFICIENT
    if train_pf >= 1.2 and validate_pf >= 1.0:
        return T.BT_ROBUST
    if train_pf >= 1.2 and validate_pf < 0.8:
        return T.BT_DECAYED
    if 1.0 <= train_pf <= 1.2 and 0.9 <= validate_pf <= 1.1:
        return T.BT_CONSISTENT
    return T.BT_REJECTED


def evaluate_symbol(path: str, *, deps: PipelineDeps | None = None) -> dict[str, Any]:
    deps = deps or default_deps()
    try:
        bars, meta = deps.load_bars(path)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "backtest_class": T.BT_INSUFFICIENT,
                "error": f"{type(exc).__name__}: {exc}"}

    symbol = (meta.get("symbols") or [None])[0] or path
    train, validate, train_dates, validate_dates = split_train_validate(
        bars, train_days=TRAIN_DAYS, validate_days=VALIDATE_DAYS)

    def _bt(sub: list[Any]) -> dict[str, Any]:
        if not sub:
            return {"profit_factor": None, "expectancy": None, "average_return": None,
                    "trades": 0, "win_rate": None, "max_drawdown": None}
        try:
            return deps.council_backtest(sub)
        except Exception as exc:  # noqa: BLE001 — 종목 단위 격리
            return {"profit_factor": None, "expectancy": None, "average_return": None,
                    "trades": 0, "error": f"{type(exc).__name__}: {exc}"}

    tr = _bt(train)
    va = _bt(validate)
    klass = _classify(tr.get("profit_factor"), va.get("profit_factor"),
                      int(tr.get("trades") or 0), int(va.get("trades") or 0))

    tr_cost_adj = _cost_adjusted_return(tr.get("average_return"))
    va_cost_adj = _cost_adjusted_return(va.get("average_return"))
    cost_fragile = (
        (tr_cost_adj is not None and tr_cost_adj <= 0)
        or (va_cost_adj is not None and va_cost_adj <= 0)
    )
    return {
        "symbol": symbol,
        "path": path,
        "backtest_class": klass,
        "train_dates": len(train_dates),
        "validate_dates": len(validate_dates),
        "train_profit_factor": tr.get("profit_factor"),
        "validate_profit_factor": va.get("profit_factor"),
        "train_expectancy": tr.get("expectancy"),
        "validate_expectancy": va.get("expectancy"),
        "train_trades": int(tr.get("trades") or 0),
        "validate_trades": int(va.get("trades") or 0),
        "train_avg_return": tr.get("average_return"),
        "validate_avg_return": va.get("average_return"),
        "cost_adjusted_train_return": tr_cost_adj,
        "cost_adjusted_validate_return": va_cost_adj,
        "cost_fragile": bool(cost_fragile),
        "error": tr.get("error") or va.get("error"),
    }


def run_backtest_agent(
    ready_symbols_meta: list[dict[str, Any]], *, deps: PipelineDeps | None = None
) -> dict[str, Any]:
    """Stage 02 — backtest 준비된 전 종목 분류. 단일 종목 오류는 격리."""
    deps = deps or default_deps()
    per: list[dict[str, Any]] = []
    for m in ready_symbols_meta:
        path = m.get("path")
        if not path:
            continue
        res = evaluate_symbol(path, deps=deps)
        if "symbol" not in res:
            res["symbol"] = m.get("symbol")
        per.append(res)

    def _syms(klass: str) -> list[str]:
        return [p["symbol"] for p in per if p.get("backtest_class") == klass]

    candidates = [p["symbol"] for p in per
                  if p.get("backtest_class") in (T.BT_ROBUST, T.BT_CONSISTENT)]
    return {
        "stage": "stage_02_backtest_agent",
        "train_days": TRAIN_DAYS,
        "validate_days": VALIDATE_DAYS,
        "round_trip_cost_fraction": T.ROUND_TRIP_COST_FRACTION,
        "robust": _syms(T.BT_ROBUST),
        "decayed": _syms(T.BT_DECAYED),
        "consistent": _syms(T.BT_CONSISTENT),
        "rejected": _syms(T.BT_REJECTED),
        "insufficient": _syms(T.BT_INSUFFICIENT),
        "candidates": candidates,
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
    }
