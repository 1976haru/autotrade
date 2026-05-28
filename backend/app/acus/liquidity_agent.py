"""ACUS Stage 04 — LiquidityAgent (3번째 필터).

각 후보 종목 유동성 검증(최근 60거래일):
  - 일평균 거래대금 = mean(일별 sum(close*volume))
  - 호가 spread 추정 = median( (일중 high - 일중 low) / 종가 )  (분봉 기반 *근사*)
  - 거래량 안정성(CV) = stdev(일거래량) / mean(일거래량)

통과 기준:
  LIQUID   : 일평균 거래대금 ≥ 30억원 AND spread ≤ 0.5%
  ILLIQUID : 나머지

spread 는 분봉 high-low 기반 *상한 근사*(실 호가 spread 미제공) — 보수적으로 표기.
"""

from __future__ import annotations

import statistics
from typing import Any

from app.acus import types as T
from app.acus.bar_utils import daily_aggregates
from app.acus.deps import PipelineDeps, default_deps

MIN_AVG_TURNOVER_KRW = 3_000_000_000  # 30억원
MAX_SPREAD = 0.005                    # 0.5%
LOOKBACK_DAYS = 60


def evaluate_symbol(path: str, *, deps: PipelineDeps | None = None,
                    max_spread: float = MAX_SPREAD,
                    min_avg_turnover_krw: float = MIN_AVG_TURNOVER_KRW) -> dict[str, Any]:
    """단일 종목 유동성 평가. ``max_spread=0`` 이면 spread 검사 skip(turnover-only 모드)."""
    deps = deps or default_deps()
    try:
        bars, meta = deps.load_bars(path)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "liquidity_class": T.LIQUIDITY_UNKNOWN,
                "error": f"{type(exc).__name__}: {exc}"}

    symbol = (meta.get("symbols") or [None])[0] or path
    agg = daily_aggregates(bars)
    turnovers = agg["turnovers"][-LOOKBACK_DAYS:]
    ranges = agg["ranges"]
    volumes = agg["volumes"][-LOOKBACK_DAYS:]

    if not turnovers:
        return {"symbol": symbol, "path": path, "liquidity_class": T.LIQUIDITY_UNKNOWN,
                "reason": "no_daily_data", "error": None}

    avg_turnover = statistics.fmean(turnovers)
    spread_proxy = statistics.median(ranges) if ranges else None
    cv = (statistics.pstdev(volumes) / statistics.fmean(volumes)
          if len(volumes) >= 2 and statistics.fmean(volumes) > 0 else None)

    turnover_ok = avg_turnover >= min_avg_turnover_krw
    spread_skipped = (max_spread <= 0)
    spread_ok = spread_skipped or (spread_proxy is not None and spread_proxy <= max_spread)
    liquid = turnover_ok and spread_ok
    klass = T.LIQUID if liquid else T.ILLIQUID
    return {
        "symbol": symbol,
        "path": path,
        "liquidity_class": klass,
        "avg_daily_turnover_krw": round(avg_turnover, 0),
        "spread_proxy": round(spread_proxy, 6) if spread_proxy is not None else None,
        "spread_check_skipped": spread_skipped,
        "volume_cv": round(cv, 4) if cv is not None else None,
        "days_used": len(turnovers),
        "error": None,
    }


def run_liquidity_agent(
    candidate_meta: list[dict[str, Any]], *, deps: PipelineDeps | None = None,
    max_spread: float = MAX_SPREAD,
    min_avg_turnover_krw: float = MIN_AVG_TURNOVER_KRW,
) -> dict[str, Any]:
    """spec default: spread ≤ 0.5% AND turnover ≥ 30억. ``max_spread=0`` → turnover-only 진단 모드.

    ⚠ spread proxy 는 5분봉 (high-low)/close 의 median 으로 *상한 근사*(실 호가 spread
       아님). 운영자가 임계를 조정하려면 명시 옵션으로만 가능 — 자동 변경 0건.
    """
    deps = deps or default_deps()
    per: list[dict[str, Any]] = []
    for m in candidate_meta:
        path = m.get("path")
        if not path:
            continue
        res = evaluate_symbol(path, deps=deps, max_spread=max_spread,
                              min_avg_turnover_krw=min_avg_turnover_krw)
        if "symbol" not in res:
            res["symbol"] = m.get("symbol")
        per.append(res)
    return {
        "stage": "stage_04_liquidity_agent",
        "min_avg_turnover_krw": min_avg_turnover_krw,
        "max_spread": max_spread,
        "spread_check_active": max_spread > 0,
        "spread_proxy_note": ("5분봉 (high-low)/close median — 상한 근사, 실 호가 spread 아님"),
        "liquid": [p["symbol"] for p in per if p.get("liquidity_class") == T.LIQUID],
        "illiquid": [p["symbol"] for p in per if p.get("liquidity_class") == T.ILLIQUID],
        "unknown": [p["symbol"] for p in per if p.get("liquidity_class") == T.LIQUIDITY_UNKNOWN],
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
    }
