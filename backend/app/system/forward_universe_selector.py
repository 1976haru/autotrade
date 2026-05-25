"""KIS-INTRADAY-FORWARD-UNIVERSE-REBUILD-01 — point-in-time universe selector (read-only).

FORWARD-VALIDATION-01 의 FORWARD_WEAK 원인은 universe(GO+TUNE top10)를 *6개월 전체*
in-sample 으로 골랐기 때문이다(rolling symbol split decay +10pp). 본 모듈은 각 rebalance
시점에서 **그 이전 lookback 구간 데이터만** 으로 종목 점수를 계산하고, 다음 test 구간에
*그때 선정된 종목만* 고정 적용한다. **미래 구간 성과는 점수에 사용하지 않는다(look-ahead 금지).**

score 산식은 사전 고정(아래 weights), test 수익률에 맞춰 재최적화하지 않는다.
Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta, timezone
from typing import Any, Sequence

_KST = timezone(timedelta(hours=9))
ROUNDTRIP_COST_FRAC = (2 * 1.5 + 18.0 + 2 * 5.0) / 10_000.0   # ≈ 0.0031 (왕복 비용 비율)
MIN_TRADES = 5

# 고정 가중치 (검증 전 정의 — 재최적화 금지).
SCORE_WEIGHTS = {
    "cost_adj_expectancy": 0.30,
    "profit_factor": 0.25,
    "win_rate": 0.20,
    "liquidity": 0.15,
    "time_bucket_edge": 0.10,
}

SELECTOR_VARIANTS = (
    "STATIC_BASELINE_ALL", "STATIC_IN_SAMPLE_TOP10", "FORWARD_SCORE_TOP5",
    "FORWARD_SCORE_TOP10", "FORWARD_SCORE_TOP15", "FORWARD_SCORE_TOP20",
    "FORWARD_EXCLUDE_BOTTOM_30", "FORWARD_EXCLUDE_BOTTOM_40", "FORWARD_STABLE_UNIVERSE",
    "FORWARD_ROTATING_UNIVERSE", "FORWARD_CONSERVATIVE_UNIVERSE", "FORWARD_AGENT_VETO_UNIVERSE",
)
_LOOKAHEAD_SELECTORS = frozenset({"STATIC_IN_SAMPLE_TOP10"})


def _date_of(b) -> Any:
    return b.timestamp.astimezone(_KST).date()


def trading_days(bars: Sequence[Any]) -> list[Any]:
    return sorted({_date_of(b) for b in bars})


def rebalance_dates(days: list[Any], freq: str) -> list[Any]:
    """rebalance 시작일 리스트. weekly=5거래일, biweekly=10, monthly=월 첫 거래일."""
    if not days:
        return []
    if freq == "monthly":
        seen, out = set(), []
        for d in days:
            mk = (d.year, d.month)
            if mk not in seen:
                seen.add(mk)
                out.append(d)
        return out
    step = 5 if freq == "weekly" else 10
    return [days[i] for i in range(0, len(days), step)]


def _zscore(vals: dict[str, float]) -> dict[str, float]:
    xs = [v for v in vals.values()]
    if len(xs) < 2:
        return {k: 0.0 for k in vals}
    m, sd = statistics.mean(xs), statistics.pstdev(xs)
    if sd == 0:
        return {k: 0.0 for k in vals}
    return {k: (v - m) / sd for k, v in vals.items()}


def compute_symbol_scores(
    lookback_bars: Sequence[Any], lookback_signals: dict[tuple[str, str], dict[str, Any]],
    *, agent_veto_counts: dict[str, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """lookback 구간만 사용해 종목별 점수 산출 (미래 미참조).

    components: cost_adj_expectancy(신호 fwd-비용), profit_factor/win_rate(lookback 체결),
    liquidity(평균 거래량), time_bucket_edge(버킷 최고 fwd). penalty: 저거래/음의 cost-edge.
    """
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2

    syms = sorted({b.symbol for b in lookback_bars})
    if not syms:
        return {}
    # lookback 체결 통계 (baseline 1회).
    by_sym = result_to_dict(run_sim_v2(lookback_bars, lookback_signals,
                                       SimV2Config(agent_mode="AGENT_ENTRY_DECIDER"))).get("by_symbol", {})
    # 신호 기반 통계.
    fwd_by_sym: dict[str, list[float]] = defaultdict(list)
    bucket_by_sym: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    sigcount: dict[str, int] = defaultdict(int)
    for (sym, _ts), v in lookback_signals.items():
        if v["council_action"] == "BUY" or v["single_buys"]:
            fwd_by_sym[sym].append(v["fwd_eod_return"])
            bucket_by_sym[sym][v["time_bucket"]].append(v["fwd_eod_return"])
            sigcount[sym] += 1
    vol_by_sym: dict[str, list[float]] = defaultdict(list)
    days_by_sym: dict[str, set] = defaultdict(set)
    for b in lookback_bars:
        vol_by_sym[b.symbol].append(float(getattr(b, "volume", 0) or 0))
        days_by_sym[b.symbol].add(_date_of(b))

    raw_cae, raw_pf, raw_wr, raw_liq, raw_tbe = {}, {}, {}, {}, {}
    meta: dict[str, dict[str, Any]] = {}
    for s in syms:
        fwd = fwd_by_sym.get(s, [])
        cae = (statistics.mean(fwd) - ROUNDTRIP_COST_FRAC) if fwd else -1.0
        ex = by_sym.get(s, {})
        pf = ex.get("profit_factor")
        wr = ex.get("win_rate")
        trades = ex.get("trades", 0) or 0
        liq = statistics.mean(vol_by_sym.get(s, [0])) if vol_by_sym.get(s) else 0.0
        tbe = max((statistics.mean(v) for v in bucket_by_sym.get(s, {}).values() if v), default=0.0)
        cost_edge_ratio = (sum(1 for x in fwd if x >= 1.5 * ROUNDTRIP_COST_FRAC) / len(fwd)) if fwd else 0.0
        raw_cae[s] = cae
        raw_pf[s] = pf if pf is not None else 0.0
        raw_wr[s] = wr if wr is not None else 0.0
        raw_liq[s] = liq
        raw_tbe[s] = tbe
        meta[s] = {"trades": trades, "lookback_net_pnl": ex.get("net_pnl"),
                   "signal_count": sigcount.get(s, 0), "cost_adj_expectancy": round(cae, 6),
                   "profit_factor": pf, "win_rate": wr, "liquidity": round(liq, 1),
                   "time_bucket_edge": round(tbe, 6), "cost_edge_ratio": round(cost_edge_ratio, 3),
                   "lookback_days": len(days_by_sym.get(s, set()))}

    z = {"cost_adj_expectancy": _zscore(raw_cae), "profit_factor": _zscore(raw_pf),
         "win_rate": _zscore(raw_wr), "liquidity": _zscore(raw_liq),
         "time_bucket_edge": _zscore(raw_tbe)}
    avc = agent_veto_counts or {}
    out: dict[str, dict[str, Any]] = {}
    for s in syms:
        score = sum(SCORE_WEIGHTS[k] * z[k][s] for k in SCORE_WEIGHTS)
        penalties = []
        if meta[s]["trades"] < MIN_TRADES:
            score -= 1.0
            penalties.append("low_trades")
        if raw_cae[s] <= 0:
            score -= 0.5
            penalties.append("neg_cost_edge")
        if (raw_wr[s] or 0) < 0.4 and meta[s]["trades"] >= MIN_TRADES:
            score -= 0.3
            penalties.append("low_win_rate")
        if meta[s]["signal_count"] > 0 and meta[s]["lookback_days"] > 0:
            noise = meta[s]["signal_count"] / max(1, meta[s]["lookback_days"])
            if noise > 30:
                score -= 0.2
                penalties.append("signal_noise")
        if avc.get(s, 0) >= 3:
            score -= 0.3
            penalties.append("agent_veto_repeat")
        out[s] = {"score": round(score, 4), "penalties": penalties, **meta[s]}
    return out


def select_universe(
    selector: str, scores: dict[str, dict[str, Any]], *, size: int = 10,
    in_sample_top10: frozenset[str] | None = None,
    prev_selected: frozenset[str] | None = None,
) -> tuple[frozenset[str] | None, list[str], dict[str, Any]]:
    """selector 별 universe 선정 → (universe, excluded, meta). universe None=전체(ALL)."""
    ranked = sorted(scores, key=lambda s: scores[s]["score"], reverse=True)
    look_ahead = selector in _LOOKAHEAD_SELECTORS

    if selector == "STATIC_BASELINE_ALL":
        return None, [], {"look_ahead": False}
    if selector == "STATIC_IN_SAMPLE_TOP10":
        uni = in_sample_top10 or frozenset(ranked[:10])
        return uni, [s for s in scores if s not in uni], {"look_ahead": True}
    if selector.startswith("FORWARD_SCORE_TOP"):
        n = int(selector.rsplit("TOP", 1)[1])
        pos = [s for s in ranked if scores[s]["score"] > -0.4][:n]
        return frozenset(pos), [s for s in scores if s not in set(pos)], {"look_ahead": False}
    if selector.startswith("FORWARD_EXCLUDE_BOTTOM_"):
        pct = int(selector.rsplit("_", 1)[1]) / 100.0
        cut = int(len(ranked) * (1 - pct))
        keep = ranked[:max(1, cut)]
        return frozenset(keep), ranked[max(1, cut):], {"look_ahead": False}
    if selector == "FORWARD_STABLE_UNIVERSE":
        top = set(ranked[:size])
        if prev_selected is not None:
            stable = top & set(prev_selected)
            keep = list(stable) or ranked[:size]
        else:
            keep = ranked[:size]
        return frozenset(keep), [s for s in scores if s not in set(keep)], {"look_ahead": False}
    if selector == "FORWARD_ROTATING_UNIVERSE":
        keep = ranked[:size]
        return frozenset(keep), ranked[size:], {"look_ahead": False}
    if selector == "FORWARD_CONSERVATIVE_UNIVERSE":
        keep = [s for s in ranked[:size * 2]
                if "low_trades" not in scores[s]["penalties"]
                and "low_win_rate" not in scores[s]["penalties"]
                and scores[s]["cost_adj_expectancy"] > 0][:size]
        keep = keep or ranked[:size]
        return frozenset(keep), [s for s in scores if s not in set(keep)], {"look_ahead": False}
    if selector == "FORWARD_AGENT_VETO_UNIVERSE":
        keep = [s for s in ranked if "agent_veto_repeat" not in scores[s]["penalties"]][:size]
        return frozenset(keep), [s for s in scores if s not in set(keep)], {"look_ahead": False}
    return frozenset(ranked[:size]), ranked[size:], {"look_ahead": look_ahead}
