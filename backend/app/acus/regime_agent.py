"""ACUS Stage 03 — RegimeAgent (2번째 필터).

각 후보(ROBUST/CONSISTENT) 종목에 PIT(point-in-time) regime 라벨을 적용하고, regime
별로 council 백테스트 PF 를 산출한다. **진입 시점에 알 수 있는 정보만 사용**(전일까지
모멘텀/변동성) — ``point_in_time_regime`` 의 ``proxy_pit_daily_features`` /
``classify_pit_regime`` 재사용. FORBIDDEN_FEATURES 는 절대 사용하지 않는다(테스트 lock).

종목별 regime 적합도:
  REGIME_ROBUST : 1개 이상 PIT regime 에서 PF ≥ 1.1
  REGIME_WEAK   : 모든 PIT regime 에서 PF < 1.0 (또는 1.0~1.1 만 — robust 미달)
  REGIME_UNKNOWN: 표본 부족(어느 regime 도 평가 불가)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.acus import types as T
from app.acus.bar_utils import bar_date, daily_aggregates, filter_bars_by_dates
from app.acus.deps import PipelineDeps, default_deps
from app.backtest.point_in_time_regime import (
    classify_pit_regime,
    proxy_pit_daily_features,
)

REGIME_ROBUST_PF = 1.1
REGIME_MIN_TRADES = 5     # regime 당 최소 BUY 신호
MIN_DAYS_FOR_PIT = 6      # 5일 모멘텀 + 당일 = 최소 6거래일 필요


def label_daily_regimes(bars: list[Any]) -> dict[date, str]:
    """각 거래일 → PIT regime 라벨 (전일까지 정보만)."""
    agg = daily_aggregates(bars)
    closes = agg["closes"]
    ranges = agg["ranges"]
    # 일자 순서대로 idx 매핑
    dates = sorted({d for b in bars if (d := bar_date(b)) is not None})
    out: dict[date, str] = {}
    for idx, d in enumerate(dates):
        if idx < 1:
            out[d] = "PIT_UNKNOWN"
            continue
        feats = proxy_pit_daily_features(closes, ranges, idx)
        regime, _conf = classify_pit_regime(feats)
        out[d] = regime
    return out


def evaluate_symbol(path: str, *, deps: PipelineDeps | None = None) -> dict[str, Any]:
    deps = deps or default_deps()
    try:
        bars, meta = deps.load_bars(path)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "regime_class": T.REGIME_UNKNOWN,
                "error": f"{type(exc).__name__}: {exc}"}

    symbol = (meta.get("symbols") or [None])[0] or path
    day_regime = label_daily_regimes(bars)

    # regime → 해당 일자 집합
    regime_days: dict[str, set[date]] = {}
    for d, r in day_regime.items():
        if r == "PIT_UNKNOWN":
            continue
        regime_days.setdefault(r, set()).add(d)

    per_regime: dict[str, dict[str, Any]] = {}
    best_pf: float | None = None
    for regime, days in sorted(regime_days.items()):
        sub = filter_bars_by_dates(bars, days)
        if len(days) < 1 or not sub:
            continue
        try:
            bt = deps.council_backtest(sub)
        except Exception as exc:  # noqa: BLE001
            per_regime[regime] = {"error": f"{type(exc).__name__}: {exc}"}
            continue
        pf = bt.get("profit_factor")
        trades = int(bt.get("trades") or 0)
        per_regime[regime] = {
            "days": len(days),
            "trades": trades,
            "profit_factor": pf,
            "expectancy": bt.get("expectancy"),
        }
        if trades >= REGIME_MIN_TRADES and pf is not None:
            best_pf = pf if best_pf is None else max(best_pf, pf)

    evaluable = [v for v in per_regime.values()
                 if int(v.get("trades") or 0) >= REGIME_MIN_TRADES and v.get("profit_factor") is not None]
    if len(day_regime) < MIN_DAYS_FOR_PIT or not evaluable:
        klass = T.REGIME_UNKNOWN
    elif best_pf is not None and best_pf >= REGIME_ROBUST_PF:
        klass = T.REGIME_ROBUST
    else:
        klass = T.REGIME_WEAK

    return {
        "symbol": symbol,
        "path": path,
        "regime_class": klass,
        "best_regime_pf": best_pf,
        "regimes_evaluated": len(evaluable),
        "per_regime": per_regime,
        "error": None,
    }


def run_regime_agent(
    candidate_meta: list[dict[str, Any]], *, deps: PipelineDeps | None = None
) -> dict[str, Any]:
    deps = deps or default_deps()
    per: list[dict[str, Any]] = []
    for m in candidate_meta:
        path = m.get("path")
        if not path:
            continue
        res = evaluate_symbol(path, deps=deps)
        if "symbol" not in res:
            res["symbol"] = m.get("symbol")
        per.append(res)
    return {
        "stage": "stage_03_regime_agent",
        "robust_pf_threshold": REGIME_ROBUST_PF,
        "regime_robust": [p["symbol"] for p in per if p.get("regime_class") == T.REGIME_ROBUST],
        "regime_weak": [p["symbol"] for p in per if p.get("regime_class") == T.REGIME_WEAK],
        "regime_unknown": [p["symbol"] for p in per if p.get("regime_class") == T.REGIME_UNKNOWN],
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
    }
