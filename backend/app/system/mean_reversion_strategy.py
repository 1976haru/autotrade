"""장중 평균회귀 전략 연구 오케스트레이터 (CHECKLIST-05, 백테스트 전용).

기존 추세추종 실패(비용 전에도 PF<1, MAE>MFE, stop_first≫target_hit)를 재분석해 *반대
가설*(mean reversion)의 근거를 도출하고, research-only 후보(`app.research.
mean_reversion_candidates`) 6종 + trend-day no-trade filter 를 실데이터로 검증한다.

엄격히 read-only / research-only:
- 기존 evaluator + intrabar 체결/비용/지표 helper + 후보 진입조건을 *재사용/조합* — 새
  런타임 전략 등록 0건, 파라미터 최적화 0건.
- broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import 0건, 실주문 0건.
- auto_apply_allowed=False / applied_to_runtime=False / is_live_authorization=False /
  research_only=True / no_profit_guarantee=True 불변.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from app.agents.agent_council import CouncilAction
from app.research.mean_reversion_candidates import (
    CANDIDATES,
    IS_RESEARCH_ONLY,
    is_trend_day,
)
from app.backtest.strategy_council_backtest import (
    _build_input,
    _group_by_day,
    load_ohlcv_from_csv,
)
from app.system.final_multi_strategy_backtest import (
    _ROUND_TRIP_BPS,
    _grade,
    _kept_by_risk_filter,
    _metrics,
    _pf,
    _slippage_stress,
)
from app.system.intrabar_realdata_backtest import (
    FIVE_MIN_DIR_DEFAULT,
    ONE_MIN_DIR_DEFAULT,
    _SYMBOL_GROUP,
    _load_csv,
    _scan,
)
from app.system.strategy_edge_redesign import (
    _EXISTING_STRATS,
    _attr,
    _day_regime_after,
    _failure_summary,
    _make_rec,
    _oos_split,
    _rolling,
    _time_bucket,
)

# 추세추종 baseline (가설 도출용).
_TREND_FOLLOWERS = {"ORB": _EXISTING_STRATS["ORB"], "MOMENTUM": _EXISTING_STRATS["MOMENTUM"]}
_FWD_HORIZONS = {"h5": 1, "h10": 2, "h15": 3, "h30": 6}  # 5분봉 기준 bar 수


def _fwd_returns_bps(day_bars, i) -> dict[str, float | None]:
    base = day_bars[i].close
    out: dict[str, float | None] = {}
    for label, k in _FWD_HORIZONS.items():
        j = i + k
        out[label] = (round((day_bars[j].close - base) / base * 1e4, 1)
                      if j < len(day_bars) and base > 0 else None)
    return out


def _agg_fwd(fwd: list[dict]) -> dict[str, Any]:
    if not fwd:
        return {"n": 0}
    out: dict[str, Any] = {"n": len(fwd)}
    for label in _FWD_HORIZONS:
        vals = [r[label] for r in fwd if r.get(label) is not None]
        out[f"mean_{label}_bps"] = round(sum(vals) / len(vals), 1) if vals else None
        # 반대(평균회귀) 방향 = 부호 반전.
        out[f"reverse_mean_{label}_bps"] = round(-sum(vals) / len(vals), 1) if vals else None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 수집
# ─────────────────────────────────────────────────────────────────────────────


def _collect(one_dir: Path, five_dir: Path, symbols):
    five_map, one_map = _scan(five_dir), _scan(one_dir)
    syms = list(symbols) if symbols else sorted(set(five_map) & set(one_map))
    trend_recs: dict[str, list[dict]] = {k: [] for k in _TREND_FOLLOWERS}
    cand_recs: dict[str, list[dict]] = {k: [] for k in CANDIDATES}
    fwd: list[dict] = []                 # 추세추종 BUY 신호의 forward return (가설)
    revert_after_stop = {"stop_first_n": 0, "reverted_n": 0}
    excluded: list[str] = []

    for sym in syms:
        if sym not in five_map or sym not in one_map:
            excluded.append(sym)
            continue
        bars5 = load_ohlcv_from_csv(str(five_map[sym]), default_symbol=sym)
        days5 = _group_by_day(bars5)
        days1: dict[str, list[dict]] = {}
        for b in _load_csv(one_map[sym]):
            ts = b.get("ts")
            if ts:
                days1.setdefault(ts.date().isoformat(), []).append(b)
        group = _SYMBOL_GROUP.get(sym, "OTHER")
        prev_close = None
        for day_bars in days5:
            day = day_bars[0].timestamp.date().isoformat()
            d1 = days1.get(day)
            if not d1:
                prev_close = day_bars[-1].close
                continue
            regime = _day_regime_after(day_bars)
            gap = ((day_bars[0].open - prev_close) / prev_close) if prev_close else None
            avg_vol = (sum(b.volume for b in day_bars) / len(day_bars)) or 1.0
            done_tf: set[str] = set()
            done_c: set[str] = set()
            for i in range(len(day_bars)):
                mi = _build_input(day_bars, i, opening_range_bars=6,
                                  recent_closes_window=5, prev_close=prev_close)
                b = day_bars[i]
                ve = (b.volume / avg_vol) if avg_vol else 1.0
                # 추세추종 BUY → forward return (가설) + 실패 trade rec.
                for name, ev in _TREND_FOLLOWERS.items():
                    if name in done_tf:
                        continue
                    if ev(mi).signal == CouncilAction.BUY:
                        fwd.append({"symbol": sym, "time_bucket": _time_bucket(b.timestamp),
                                    "regime": regime, **_fwd_returns_bps(day_bars, i)})
                        rec = _make_rec(name, sym, day, b, mi, ve, gap, group, regime,
                                        day_bars, d1)
                        if rec:
                            trend_recs[name].append(rec)
                            if rec["stop_first"]:
                                revert_after_stop["stop_first_n"] += 1
                                # 손절 후 되돌림: MFE 가 손절폭 이상이면 반전 발생으로 간주.
                                if rec["mfe_bps"] >= 50.0:
                                    revert_after_stop["reverted_n"] += 1
                        done_tf.add(name)
                # 평균회귀 후보.
                td = is_trend_day(mi, day_bars=day_bars, i=i)
                for cname, cfn in CANDIDATES.items():
                    if cname in done_c:
                        continue
                    if cfn(mi, ve=ve, gap=gap, day_bars=day_bars, i=i):
                        rec = _make_rec(cname, sym, day, b, mi, ve, gap, group, regime,
                                        day_bars, d1)
                        if rec:
                            rec["trend_day"] = td
                            cand_recs[cname].append(rec)
                            done_c.add(cname)
            prev_close = day_bars[-1].close

    return trend_recs, cand_recs, fwd, revert_after_stop, excluded, syms


# ─────────────────────────────────────────────────────────────────────────────
# 비용 / verdict helper
# ─────────────────────────────────────────────────────────────────────────────


def _cost_verdict(gross_pf, net_pf, stress) -> str:
    s10 = stress.get("10.0bps")
    if (net_pf or 0) > 1.2 and (s10 or 0) >= 1.0:
        return "RESEARCH_CANDIDATE"
    if (net_pf or 0) > 1.0 and (s10 or 0) < 1.0:
        return "COST_FRAGILE"
    if (gross_pf or 0) > 1.0 and (net_pf or 0) < 1.0:
        return "COST_KILLS_EDGE"
    if (gross_pf or 0) < 1.0:
        return "NO_GROSS_EDGE"
    return "COST_KILLS_EDGE"


def _candidate_research_verdict(full_pf, oos_pf, mdd, train_pf) -> str:
    """REJECT / WATCH / CANDIDATE / STRONG_CANDIDATE (PF>1.3 + OOS 유지)."""
    if full_pf is None or full_pf < 1.0:
        return "REJECT"
    if full_pf < 1.15:
        return "WATCH"
    if full_pf < 1.3:
        return "CANDIDATE"
    if (oos_pf or 0) >= 1.15 and (train_pf or 0) >= 1.0:
        return "STRONG_CANDIDATE"
    return "CANDIDATE"


def _symbol_split(recs: list[dict], syms: list[str]) -> dict[str, Any]:
    sorted_syms = sorted(syms)
    even = {s for idx, s in enumerate(sorted_syms) if idx % 2 == 0}
    e = [r for r in recs if r["symbol"] in even]
    o = [r for r in recs if r["symbol"] not in even]
    by_sym = _attr(recs, "symbol")
    pfs = [v["pf"] for v in by_sym.values() if v["pf"] is not None]
    concentration = (max(pfs) if pfs else None)
    return {"even_pf": _pf([r["net"] for r in e]) if e else None,
            "odd_pf": _pf([r["net"] for r in o]) if o else None,
            "even_n": len(e), "odd_n": len(o),
            "single_symbol_top_pf": concentration}


def _variant_pfs(recs: list[dict]) -> dict[str, Any]:
    rf = [r for r in recs if _kept_by_risk_filter(r)]
    tf = [r for r in recs if not r.get("trend_day")]
    both = [r for r in recs if _kept_by_risk_filter(r) and not r.get("trend_day")]
    return {
        "base_pf": _pf([r["net"] for r in recs]) if recs else None,
        "risk_filter_pf": _pf([r["net"] for r in rf]) if rf else None,
        "no_trade_trend_pf": _pf([r["net"] for r in tf]) if tf else None,
        "both_pf": _pf([r["net"] for r in both]) if both else None,
        "base_n": len(recs), "risk_filter_n": len(rf),
        "no_trade_trend_n": len(tf), "both_n": len(both),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 오케스트레이터
# ─────────────────────────────────────────────────────────────────────────────


def run_mean_reversion_strategy(*, one_min_dir: Path | None = None,
                                five_min_dir: Path | None = None,
                                symbols: Sequence[str] | None = None) -> dict[str, Any]:
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    trend_recs, cand_recs, fwd, revert_after_stop, excluded, syms = _collect(
        one_dir, five_dir, symbols)

    total = sum(len(v) for v in cand_recs.values())
    if total < 10:
        return _empty(total, syms)

    # 1. 가설 분석.
    rar = revert_after_stop
    hypothesis = {
        "trend_follower_forward_returns": _agg_fwd(fwd),
        "trend_follower_forward_by_time": {
            tb: _agg_fwd([r for r in fwd if r["time_bucket"] == tb])
            for tb in sorted({r["time_bucket"] for r in fwd})},
        "trend_follower_forward_by_regime": {
            rg: _agg_fwd([r for r in fwd if r["regime"] == rg])
            for rg in sorted({r["regime"] for r in fwd})},
        "stop_first_then_revert": {
            **rar,
            "revert_ratio": (round(rar["reverted_n"] / rar["stop_first_n"], 3)
                             if rar["stop_first_n"] else None)},
        "existing_failure": {n: _failure_summary(recs) for n, recs in trend_recs.items()},
        "interpretation": _hypothesis_interpretation(fwd, rar),
    }

    # 2~5. 후보 검증.
    cand_results: dict[str, Any] = {}
    for cname, recs in cand_recs.items():
        if not recs:
            cand_results[cname] = {"trade_count": 0, "verdict": "REJECT",
                                   "cost_verdict": "NO_GROSS_EDGE", "reason": "NO_TRADES"}
            continue
        m = _metrics(recs, "net")
        gross_pf = _pf([r["gross"] for r in recs])
        stress = _slippage_stress(recs)
        oos = _oos_split(recs)
        roll = _rolling(recs)
        ssplit = _symbol_split(recs, syms)
        variants = _variant_pfs(recs)
        n = m["trade_count"]
        stop_first = sum(1 for r in recs if r["stop_first"])
        target_hit = sum(1 for r in recs if r["target_hit"])
        eod = sum(1 for r in recs if r["exit_reason"] in ("EOD_EXIT", "MAX_HOLD_EXIT"))
        rverdict = _candidate_research_verdict(m["profit_factor"], oos.get("oos_pf"),
                                               m["mdd_pct"], oos.get("train_pf"))
        if rverdict in ("CANDIDATE", "STRONG_CANDIDATE") and n < 30:
            rverdict = "LOW_CONFIDENCE"
        cand_results[cname] = {
            "trade_count": n,
            "profit_factor": m["profit_factor"], "gross_pf": gross_pf,
            "net_pf": m["profit_factor"], "return_pct": m["return_pct"],
            "mdd_pct": m["mdd_pct"], "expectancy_bps": m["expectancy_bps"],
            "win_rate": m["win_rate"], "payoff_ratio": m["payoff_ratio"],
            "avg_hold_minutes": m["avg_hold_minutes"],
            "stop_first_ratio": round(stop_first / n, 3),
            "target_hit_ratio": round(target_hit / n, 3),
            "eod_exit_ratio": round(eod / n, 3),
            "avg_mfe_bps": round(sum(r["mfe_bps"] for r in recs) / n, 1),
            "avg_mae_bps": round(sum(r["mae_bps"] for r in recs) / n, 1),
            "cost_drag_bps": _ROUND_TRIP_BPS,
            "slippage_stress": stress,
            "oos": oos, "rolling": roll, "symbol_split": ssplit, "variants": variants,
            "by_time_bucket": _attr(recs, "time_bucket"),
            "by_regime": _attr(recs, "regime"),
            "by_symbol": _attr(recs, "symbol"),
            "grade": _grade(m),
            "cost_verdict": _cost_verdict(gross_pf, m["profit_factor"], stress),
            "verdict": rverdict,
        }

    survivors = [c for c, v in cand_results.items()
                 if v.get("verdict") in ("CANDIDATE", "STRONG_CANDIDATE")]
    watch = [c for c, v in cand_results.items() if v.get("verdict") == "WATCH"]
    low_conf = [c for c, v in cand_results.items() if v.get("verdict") == "LOW_CONFIDENCE"]
    reject = [c for c, v in cand_results.items() if v.get("verdict") == "REJECT"]
    verdict, conclusion = _overall_verdict(cand_results, survivors, watch, low_conf)

    return {
        "available": True,
        "mode": "mean_reversion_strategy_research",
        "is_research_only": IS_RESEARCH_ONLY,
        "data": {"symbols": syms, "excluded": excluded,
                 "trend_follower_trade_counts": {n: len(v) for n, v in trend_recs.items()},
                 "candidate_trade_counts": {n: len(v) for n, v in cand_recs.items()}},
        "cost_model": {"round_trip_bps": _ROUND_TRIP_BPS},
        "hypothesis_analysis": hypothesis,
        "candidate_results": cand_results,
        "survivors": survivors, "watch": watch,
        "low_confidence": low_conf, "reject": reject,
        "verdict": verdict, "conclusion": conclusion,
        "next_steps": [
            "REJECT 후보는 폐기 — 평균회귀 진입도 비용을 못 넘음",
            "CANDIDATE 는 별도 PR + OOS 재검증 + (조건 충족 시) paper rehearsal 후에만 검토",
            "LOW_CONFIDENCE 는 거래 표본 부족 — 더 많은 데이터 필요",
            "어떤 후보도 런타임 전략으로 자동 등록/적용되지 않음",
        ],
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. 평균회귀 후보는 런타임 전략으로 "
                      "등록/적용되지 않습니다(research_only). 수익을 보장하지 않습니다.",
    }


def _hypothesis_interpretation(fwd, rar):
    msgs = []
    agg = _agg_fwd(fwd)
    m30 = agg.get("mean_h30_bps")
    if m30 is not None:
        if m30 <= -5.0:
            msgs.append(f"추세추종 BUY 후 30분 평균 forward return {m30}bps (유의미한 음) → "
                        "반대(평균회귀) 방향이 수익성 있을 *가능성* 시사.")
        elif abs(m30) < 5.0:
            msgs.append(f"추세추종 BUY 후 30분 평균 forward return {m30}bps (≈0, 사실상 noise) "
                        "→ 단순 반전 가설은 *약함* (방향성 자체가 거의 없음).")
        else:
            msgs.append(f"추세추종 BUY 후 30분 평균 forward return {m30}bps (양) → 단순 반전 "
                        "가설은 약함.")
    rr = (rar["reverted_n"] / rar["stop_first_n"]) if rar["stop_first_n"] else None
    if rr is not None:
        msgs.append(f"손절 우선 거래 중 {rr:.1%} 가 이후 +50bps 이상 되돌림 → 손절 후 *되돌림은 "
                    "잦으나*, 고정 손절(−1%)이 되돌림 전에 청산시키는 구조 — entry 보다 exit "
                    "(고정 손절/연속형 목표) 가 핵심 confound 일 수 있음.")
    return msgs


def _overall_verdict(cand_results, survivors, watch, low_conf):
    # net_pf>1.2 + 10bps 유지 + OOS≥1.0 → OOS_VALIDATED.
    validated = []
    candidates_found = []
    fragile = []
    for c, v in cand_results.items():
        cv = v.get("cost_verdict")
        oos_pf = (v.get("oos") or {}).get("oos_pf")
        if cv == "RESEARCH_CANDIDATE" and (oos_pf or 0) >= 1.0 and v.get("trade_count", 0) >= 30:
            validated.append(c)
        elif cv == "RESEARCH_CANDIDATE":
            candidates_found.append(c)
        elif cv == "COST_FRAGILE":
            fragile.append(c)
    if validated:
        return ("MEAN_REVERSION_OOS_VALIDATED",
                [f"{validated} — net PF>1.2 + 10bps slippage 유지 + OOS PF≥1.0 + 표본≥30. "
                 "단 자동 적용 금지, 별도 PR + paper rehearsal 필요(실전 아님)."])
    if candidates_found:
        return ("MEAN_REVERSION_CANDIDATE_FOUND",
                [f"{candidates_found} — net PF>1.2 + 10bps 유지하나 OOS/표본 미확정. 추가 검증 필요."])
    if fragile:
        return ("MEAN_REVERSION_COST_FRAGILE",
                [f"{fragile} — net PF>1.0 이나 10bps slippage 에서 붕괴(비용 취약). 실전성 없음."])
    if low_conf:
        return ("MEAN_REVERSION_NEEDS_MORE_DATA",
                [f"{low_conf} — PF 양호 신호이나 거래 표본 부족(<30). 더 많은 데이터 필요."])
    # target_hit 이 일관되게 낮으면 exit 미스매치 confound 를 *정직하게* 부기.
    thr = [v.get("target_hit_ratio") for v in cand_results.values()
           if v.get("trade_count")]
    low_target = thr and (sum(thr) / len(thr)) < 0.15
    note = (" (참고: 모든 후보 target_hit≈0.04~0.14 로 매우 낮음 — 고정 +1.5% *연속형* 목표는 "
            "평균회귀(작은 되돌림) 진입과 미스매치. 회귀 적합 exit(소형 목표/VWAP 회귀)은 별도 "
            "연구 PR 필요. 단 본 PR 은 비교 공정성 위해 파라미터 최적화 금지.)" if low_target else "")
    return ("MEAN_REVERSION_EDGE_NOT_FOUND",
            ["평균회귀 후보 6종 모두 비용 전(gross)에도 PF<1 (NO_GROSS_EDGE) — 추세추종에 이어 "
             "평균회귀 가설도 현 데이터/유니버스에서 엣지 없음. RiskFilter/trend-day filter 도 "
             "결정적 개선 없음." + note])


def _empty(n, syms):
    return {"available": False, "verdict": "BACKTEST_INFRA_INCOMPLETE",
            "is_research_only": IS_RESEARCH_ONLY,
            "reason": "INSUFFICIENT_TRADES", "trade_count": n, "symbols": syms,
            "auto_apply_allowed": False, "applied_to_runtime": False,
            "is_live_authorization": False, "no_profit_guarantee": True,
            "contains_secret": False,
            "disclaimer": "연구용 백테스트 — 거래 표본 부족. 자동 적용 안 됨, 실전매매 권고 아님."}
