"""ACUS Stage 06 — RiskAgent (5번째 필터).

각 후보 종목 리스크 메트릭(최근 60거래일):
  - 일일 변동성 σ = stdev(일간 수익률)
  - 상한가/하한가 빈도 = |일간 수익률| ≥ 29% 일수 (KR ±30% 제한 근사)
  - 단봉 ±5%+ 빈도 = |분봉 수익률| ≥ 5% bar 수
  - MDD = 일봉 종가 자금곡선 최대낙폭(비율)

통과 기준:
  RISK_OK   : σ < 5% AND 상한가/하한가 ≤ 3회 AND MDD < 25%
  RISK_HIGH : 나머지
"""

from __future__ import annotations

import statistics
from typing import Any

from app.acus import types as T
from app.acus.bar_utils import daily_aggregates
from app.acus.deps import PipelineDeps, default_deps

MAX_DAILY_SIGMA = 0.05      # 5%
MAX_LIMIT_HITS = 3
MAX_MDD = 0.25              # 25%
LIMIT_RETURN = 0.29        # 상/하한가 근사
SPIKE_RETURN = 0.05        # 단봉 ±5%
LOOKBACK_DAYS = 60


def _daily_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            out.append((closes[i] - prev) / prev)
    return out


def _max_drawdown_fraction(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        if peak > 0:
            dd = (peak - c) / peak
            mdd = max(mdd, dd)
    return round(mdd, 6)


def evaluate_symbol(path: str, *, deps: PipelineDeps | None = None) -> dict[str, Any]:
    deps = deps or default_deps()
    try:
        bars, meta = deps.load_bars(path)
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "risk_class": T.RISK_UNKNOWN,
                "error": f"{type(exc).__name__}: {exc}"}

    symbol = (meta.get("symbols") or [None])[0] or path
    agg = daily_aggregates(bars)
    closes = agg["closes"][-(LOOKBACK_DAYS + 1):]
    rets = _daily_returns(closes)
    if len(rets) < 2:
        return {"symbol": symbol, "path": path, "risk_class": T.RISK_UNKNOWN,
                "reason": "insufficient_daily_returns", "error": None}

    sigma = statistics.pstdev(rets)
    limit_hits = sum(1 for r in rets if abs(r) >= LIMIT_RETURN)
    mdd = _max_drawdown_fraction(closes)

    # 단봉 ±5%+ 빈도 (분봉)
    spike = 0
    prev_close = None
    for b in bars:
        c = float(getattr(b, "close", 0.0))
        o = float(getattr(b, "open", 0.0))
        if o > 0 and abs((c - o) / o) >= SPIKE_RETURN:
            spike += 1
        prev_close = c  # noqa: F841

    ok = (sigma < MAX_DAILY_SIGMA and limit_hits <= MAX_LIMIT_HITS
          and mdd is not None and mdd < MAX_MDD)
    klass = T.RISK_OK if ok else T.RISK_HIGH
    return {
        "symbol": symbol,
        "path": path,
        "risk_class": klass,
        "daily_sigma": round(sigma, 6),
        "limit_hits": limit_hits,
        "single_bar_spikes": spike,
        "max_drawdown": mdd,
        "days_used": len(rets),
        "error": None,
    }


def run_risk_agent(
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
        "stage": "stage_06_risk_agent",
        "max_daily_sigma": MAX_DAILY_SIGMA,
        "max_limit_hits": MAX_LIMIT_HITS,
        "max_mdd": MAX_MDD,
        "risk_ok": [p["symbol"] for p in per if p.get("risk_class") == T.RISK_OK],
        "risk_high": [p["symbol"] for p in per if p.get("risk_class") == T.RISK_HIGH],
        "risk_unknown": [p["symbol"] for p in per if p.get("risk_class") == T.RISK_UNKNOWN],
        "per_symbol": per,
        "is_order_signal": False,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
    }
