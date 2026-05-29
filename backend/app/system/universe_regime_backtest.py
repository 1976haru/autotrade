"""종목군 × 시장국면 다변화 백테스트 (CHECKLIST-05, 백테스트 전용).

대형주 10종목 편향을 풀기 위해 universe 를 5개 그룹으로, 시장을 5개 국면으로 나누어
4전략(ORB/Momentum/Gap/VWAP) + Agent Council(+Risk Filter) + 평균회귀 후보가 *어디에서
살아남는지* 재검증한다. 전략 실패를 전체 시장 실패로 단정하지 않는다.

엄격히 read-only / research-only:
- 기존 evaluator + run_agent_council + 평균회귀 후보(`app.research`) + intrabar 체결/비용/
  지표 helper 를 *그대로 재사용* — 새 전략/파라미터 최적화 0건.
- 체결은 그룹 간 공정 비교를 위해 *5분봉 fallback* 으로 통일(1분봉 가용성 편차 confound 제거).
- regime 라벨은 사후 attribution 전용(look-ahead, 진입 신호 미사용).
- broker 주문 / 단일 주문 라우터 / OrderExecutor / KIS 주문 API import 0건, 실주문 0건.
- auto_apply_allowed=False / applied_to_runtime=False / is_live_authorization=False /
  research_only=True / no_profit_guarantee=True 불변.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from app.agents.agent_council import (
    CouncilAction,
    evaluate_gap,
    evaluate_momentum,
    evaluate_orb,
    evaluate_vwap,
    run_agent_council,
)
from app.backtest.intrabar_execution import CostModel, simulate_intrabar_execution
from app.backtest.cost_model import DEFAULT_COST, cost_drag_bps
from app.backtest.strategy_council_backtest import (
    _build_input,
    _group_by_day,
    load_ohlcv_from_csv,
)
from app.backtest.point_in_time_regime import (
    PIT_REGIMES,
    classify_pit_regime,
    lookahead_flags,
    proxy_pit_daily_features,
    regime_direction,
)
from app.market_data.market_regime_diversification import build_regime_map, build_regime_manifest
from app.market_data.universe_diversification import (
    MARKET_PROXY_SYMBOL,
    UNIVERSE_GROUPS,
    build_universe_manifest,
    symbol_groups,
)
from app.research.mean_reversion_candidates import CANDIDATES as MR_CANDIDATES
from app.system.final_multi_strategy_backtest import (
    _grade,
    _kept_by_risk_filter,
    _metrics,
    _pf,
    _slippage_stress,
)
from app.system.intrabar_realdata_backtest import _SYM_RE
from app.system.strategy_edge_redesign import _time_bucket

_STRATS = {"ORB": evaluate_orb, "MOMENTUM": evaluate_momentum,
           "GAP": evaluate_gap, "VWAP": evaluate_vwap}
_STOP_PCT, _TARGET_PCT, _MAX_HOLD = 0.01, 0.015, 30
_COST5 = CostModel(slippage_bps=DEFAULT_COST.slippage_bps)
_ROUND_TRIP_BPS = cost_drag_bps()
_DATA_DIRS_DEFAULT = [Path("data/market/diversified_5m"),
                      Path("data/market/intraday_ohlcv/kis_6m")]


def _scan_dirs(dirs: list[Path]) -> dict[str, Path]:
    """여러 dir 의 *.csv → {symbol: path}. 앞 dir 우선(일관 기간)."""
    out: dict[str, Path] = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in sorted(d.glob("*.csv")):
            m = _SYM_RE.match(p.stem)
            if m and m.group(1) not in out:
                out[m.group(1)] = p
    return out


def _exit5m(entry, stop, target, et, day_bars5):
    return simulate_intrabar_execution(
        side="BUY", entry_time=et, entry_price=entry, stop_price=stop,
        target_price=target, max_hold_minutes=_MAX_HOLD, bars_5m=day_bars5,
        bars_1m=None, cost=_COST5)


def _mfe_mae_5m(entry, et, day_bars5):
    if entry <= 0 or et is None:
        return 0.0, 0.0
    from datetime import timedelta
    end = et + timedelta(minutes=_MAX_HOLD)
    win = [b for b in day_bars5 if et <= b.timestamp <= end]
    if not win:
        return 0.0, 0.0
    mfe = max((b.high - entry) / entry for b in win) * 1e4
    mae = min((b.low - entry) / entry for b in win) * 1e4
    return round(max(0.0, mfe), 1), round(min(0.0, mae), 1)


def _rec(name, sym, day, bar, ve, regime, day_bars5, *, exit_plan=None,
         pit_label="PIT_UNKNOWN", pit_conf=0.0, pit_features=None):
    entry = bar.close
    if entry <= 0:
        return None
    sp, tp = _STOP_PCT, _TARGET_PCT
    if exit_plan:
        sp = float(exit_plan.get("stop_loss_pct", sp * 100) or sp * 100) / 100.0
        tp = float(exit_plan.get("take_profit_pct", tp * 100) or tp * 100) / 100.0
    stop, target = entry * (1 - sp), entry * (1 + tp)
    et = bar.timestamp
    res = _exit5m(entry, stop, target, et, day_bars5)
    mfe, mae = _mfe_mae_5m(entry, et, day_bars5)
    return {"strategy": name, "symbol": sym, "day": day, "entry": entry,
            "net": res.net_pnl, "gross": res.gross_pnl, "slip5": res.slippage_paid,
            "hold": res.hold_minutes, "exit_reason": res.exit_reason,
            "stop_first": res.exit_reason in ("STOP_HIT", "AMBIGUOUS_STOP_FIRST"),
            "target_hit": res.exit_reason == "TARGET_HIT",
            "vol_exp": ve, "low_liq": ve < 0.8, "regime": regime,
            # posthoc(look-ahead) vs point-in-time(entry-known) regime 둘 다 carry.
            "posthoc_regime_label": regime,
            "point_in_time_regime_label": pit_label,
            "point_in_time_regime_confidence": pit_conf,
            "point_in_time_features_used": list((pit_features or {}).keys()),
            "time_bucket": _time_bucket(et), "mfe_bps": mfe, "mae_bps": mae,
            "net_edge_bps": tp * 1e4 - _ROUND_TRIP_BPS}


def _collect(symbol_paths: dict[str, Path], regime_map: dict[str, str], *,
             pit_proxy_by_date: dict[str, dict] | None = None,
             run_council: bool = True):
    pit_proxy_by_date = pit_proxy_by_date or {}
    """종목별 4전략/council/평균회귀 trade 수집 (5분봉 체결)."""
    strat: dict[str, list[dict]] = {k: [] for k in _STRATS}
    council: list[dict] = []
    mr: dict[str, list[dict]] = {k: [] for k in MR_CANDIDATES}
    by_symbol_group: dict[str, dict[str, list[dict]]] = {}

    meta: dict[str, dict] = {}
    for sym, path in symbol_paths.items():
        bars5 = load_ohlcv_from_csv(str(path), default_symbol=sym)
        days5 = _group_by_day(bars5)
        if days5:
            day_isos = [d[0].timestamp.date().isoformat() for d in days5]
            meta[sym] = {"first_day": min(day_isos), "last_day": max(day_isos),
                         "days": len(days5), "bars": len(bars5)}
        prev_close = None
        s_strat = {k: [] for k in _STRATS}
        s_council: list[dict] = []
        s_mr = {k: [] for k in MR_CANDIDATES}
        for day_bars in days5:
            day = day_bars[0].timestamp.date().isoformat()
            regime = regime_map.get(day, "UNKNOWN")
            gap = ((day_bars[0].open - prev_close) / prev_close) if prev_close else None
            avg_vol = (sum(b.volume for b in day_bars) / len(day_bars)) or 1.0
            done_s: set[str] = set()
            done_c: set[str] = set()
            council_done = False
            proxy_feats = pit_proxy_by_date.get(day, {})
            day_open = day_bars[0].open
            first_5m_ret = ((day_bars[0].close - day_open) / day_open) if day_open else None
            for i in range(len(day_bars)):
                mi = _build_input(day_bars, i, opening_range_bars=6,
                                  recent_closes_window=5, prev_close=prev_close)
                b = day_bars[i]
                ve = (b.volume / avg_vol) if avg_vol else 1.0
                # PIT regime: 진입 시점 정보만 (전일 proxy 모멘텀 + 당일 장초반 entry 이전).
                pit_feats = {
                    **proxy_feats, "opening_gap": gap, "first_5m_return": first_5m_ret,
                    "price_vs_vwap_so_far": (((b.close - mi.vwap) / mi.vwap)
                                             if mi.vwap else None),
                    "opening_range_width_so_far": (
                        ((mi.opening_range_high - mi.opening_range_low) / day_open)
                        if (mi.opening_range_high and mi.opening_range_low and day_open)
                        else None),
                }
                pit_label, pit_conf = classify_pit_regime(pit_feats)
                _pk = dict(pit_label=pit_label, pit_conf=pit_conf, pit_features=pit_feats)
                for nm, ev in _STRATS.items():
                    if nm not in done_s and ev(mi).signal == CouncilAction.BUY:
                        r = _rec(nm, sym, day, b, ve, regime, day_bars, **_pk)
                        if r:
                            s_strat[nm].append(r)
                            done_s.add(nm)
                for cn, cf in MR_CANDIDATES.items():
                    if cn not in done_c and cf(mi, ve=ve, gap=gap, day_bars=day_bars, i=i):
                        r = _rec(cn, sym, day, b, ve, regime, day_bars, **_pk)
                        if r:
                            s_mr[cn].append(r)
                            done_c.add(cn)
                if run_council and not council_done:
                    c = run_agent_council(mi)
                    if c.final_action == CouncilAction.BUY:
                        r = _rec("COUNCIL", sym, day, b, ve, regime, day_bars,
                                 exit_plan=c.exit_plan, **_pk)
                        if r:
                            s_council.append(r)
                            council_done = True
            prev_close = day_bars[-1].close
        for k in _STRATS:
            strat[k] += s_strat[k]
        council += s_council
        for k in MR_CANDIDATES:
            mr[k] += s_mr[k]
        by_symbol_group[sym] = {"strat": s_strat, "council": s_council, "mr": s_mr}

    return strat, council, mr, by_symbol_group, meta


def _coverage(meta: dict[str, dict]) -> dict[str, Any]:
    """그룹별 데이터 coverage / date range / bars (partial 진단용)."""
    cov: dict[str, Any] = {}
    all_first, all_last = [], []
    for g, members in UNIVERSE_GROUPS.items():
        codes = [c for c, _n in members if c in meta]
        firsts = [meta[c]["first_day"] for c in codes]
        lasts = [meta[c]["last_day"] for c in codes]
        bars = sum(meta[c]["bars"] for c in codes)
        days = max((meta[c]["days"] for c in codes), default=0)
        all_first += firsts
        all_last += lasts
        cov[g] = {
            "present": len(codes), "total": len(members),
            "available_symbols": codes,
            "date_range": [min(firsts), max(lasts)] if firsts else None,
            "max_trading_days": days, "total_bars": bars,
            "sufficient": len(codes) >= 5,
        }
    span = ([min(all_first), max(all_last)] if all_first else None)
    return {"by_group": cov, "overall_date_range": span}


def _strategy_block(trades: list[dict]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0, "net_pf": None}
    m = _metrics(trades, "net")
    n = m["trade_count"]
    rf = [t for t in trades if _kept_by_risk_filter(t)]
    return {
        "trade_count": n, "net_pf": m["profit_factor"],
        "gross_pf": _pf([t["gross"] for t in trades]),
        "return_pct": m["return_pct"], "mdd_pct": m["mdd_pct"],
        "expectancy_bps": m["expectancy_bps"], "win_rate": m["win_rate"],
        "avg_hold_minutes": m["avg_hold_minutes"],
        "stop_first_ratio": round(sum(1 for t in trades if t["stop_first"]) / n, 3),
        "target_hit_ratio": round(sum(1 for t in trades if t["target_hit"]) / n, 3),
        "avg_mfe_bps": round(sum(t["mfe_bps"] for t in trades) / n, 1),
        "avg_mae_bps": round(sum(t["mae_bps"] for t in trades) / n, 1),
        "risk_filter_pf": _metrics(rf, "net")["profit_factor"] if rf else None,
        "risk_filter_n": len(rf),
        "slippage_stress": _slippage_stress(trades),
        "grade": _grade(m),
    }


def run_universe_regime_backtest(*, data_dirs: list[Path] | None = None,
                                 symbols: Sequence[str] | None = None,
                                 run_council: bool = True,
                                 allow_partial: bool = False) -> dict[str, Any]:
    dirs = [Path(d) for d in data_dirs] if data_dirs else _DATA_DIRS_DEFAULT
    universe = build_universe_manifest(dirs)
    all_paths = _scan_dirs(dirs)
    if symbols:
        all_paths = {s: p for s, p in all_paths.items() if s in set(symbols)}

    # regime map (proxy). proxy 미수집 시 대형 평균으로 fallback.
    regime_map: dict[str, str] = {}
    proxy_path = all_paths.get(MARKET_PROXY_SYMBOL)
    proxy_used = MARKET_PROXY_SYMBOL
    if not proxy_path:
        # fallback: 첫 LARGE_CAP_CORE 종목을 proxy 로(사후 attribution 용).
        for code, _n in UNIVERSE_GROUPS["LARGE_CAP_CORE"]:
            if code in all_paths:
                proxy_path, proxy_used = all_paths[code], code
                break
    pit_proxy_by_date: dict[str, dict] = {}
    if proxy_path:
        pbars = load_ohlcv_from_csv(str(proxy_path), default_symbol=proxy_used)
        pday_list = _group_by_day(pbars)
        pdays = {d[0].timestamp.date().isoformat(): d for d in pday_list}
        regime_map = build_regime_map(pdays)
        # PIT: 전일까지의 proxy 모멘텀/변동성 (당일 close/range 미사용).
        p_sorted = sorted(pdays)
        closes = [pdays[dte][-1].close for dte in p_sorted]
        ranges = [((max(b.high for b in pdays[dte]) - min(b.low for b in pdays[dte]))
                   / pdays[dte][0].open) if pdays[dte][0].open else None for dte in p_sorted]
        for k, dte in enumerate(p_sorted):
            pit_proxy_by_date[dte] = proxy_pit_daily_features(closes, ranges, k)
    regime_manifest = build_regime_manifest(regime_map, proxy_used)

    sym2groups = symbol_groups()
    needed = [s for s in all_paths if s in sym2groups]
    if len(needed) < 5:
        return _empty(len(needed), list(all_paths), universe, regime_manifest)

    strat, council, mr, by_sym, meta = _collect(
        {s: all_paths[s] for s in needed}, regime_map,
        pit_proxy_by_date=pit_proxy_by_date, run_council=run_council)

    # 그룹별 집계.
    group_results: dict[str, Any] = {}
    for g, members in UNIVERSE_GROUPS.items():
        codes = {c for c, _n in members if c in by_sym}
        if not codes:
            group_results[g] = {"present": 0, "note": "데이터 없음 — NEED_MORE_DATA"}
            continue
        g_strat = {k: [t for c in codes for t in by_sym[c]["strat"][k]] for k in _STRATS}
        g_council = [t for c in codes for t in by_sym[c]["council"]]
        g_mr = {k: [t for c in codes for t in by_sym[c]["mr"][k]] for k in MR_CANDIDATES}
        per_strat = {k: _strategy_block(v) for k, v in g_strat.items()}
        council_block = _strategy_block(g_council)
        crf = [t for t in g_council if _kept_by_risk_filter(t)]
        pfs = {k: per_strat[k]["net_pf"] for k in _STRATS if per_strat[k].get("net_pf")}
        best = max(pfs, key=pfs.get) if pfs else None
        group_results[g] = {
            "present": len(codes), "symbols": sorted(codes),
            "four_strategy": per_strat,
            "council": council_block,
            "council_risk_filter_pf": _metrics(crf, "net")["profit_factor"] if crf else None,
            "best_single_strategy": best,
            "best_single_pf": pfs.get(best) if best else None,
            "mean_reversion": {k: _strategy_block(v) for k, v in g_mr.items()},
        }

    # 국면별 집계 (전 종목, 전략별).
    regime_results: dict[str, Any] = {}
    for rg in sorted(set(regime_map.values()) | {"UNKNOWN"}):
        block = {}
        for k in _STRATS:
            rt = [t for t in strat[k] if t["regime"] == rg]
            block[k] = _strategy_block(rt) if rt else {"trade_count": 0}
        ct = [t for t in council if t["regime"] == rg]
        block["COUNCIL"] = _strategy_block(ct) if ct else {"trade_count": 0}
        if any(block[k].get("trade_count") for k in block):
            regime_results[rg] = block

    # 국면별 집계 — point-in-time(진입 시점 정보만, look-ahead 없음).
    pit_regime_results: dict[str, Any] = {}
    for pl in PIT_REGIMES:
        block = {}
        for k in _STRATS:
            rt = [t for t in strat[k] if t["point_in_time_regime_label"] == pl]
            block[k] = _strategy_block(rt) if rt else {"trade_count": 0}
        ct = [t for t in council if t["point_in_time_regime_label"] == pl]
        block["COUNCIL"] = _strategy_block(ct) if ct else {"trade_count": 0}
        if any(block[k].get("trade_count") for k in block):
            pit_regime_results[pl] = block

    # posthoc vs PIT 일치율 (방향 매핑 기준).
    all_trades = council + [t for k in _STRATS for t in strat[k]]
    agree = sum(1 for t in all_trades
                if regime_direction(t["posthoc_regime_label"])
                == regime_direction(t["point_in_time_regime_label"]))
    agreement_rate = round(agree / len(all_trades), 3) if all_trades else None

    # STRONG_UPTREND 힌트: posthoc(look-ahead) vs PIT(entry-known) Council PF.
    posthoc_su = (regime_results.get("STRONG_UPTREND", {}).get("COUNCIL", {}) or {}).get("net_pf")
    pit_su = (pit_regime_results.get("PIT_STRONG_UPTREND", {}).get("COUNCIL", {}) or {}).get("net_pf")
    pit_su_n = (pit_regime_results.get("PIT_STRONG_UPTREND", {}).get("COUNCIL", {}) or {}).get("trade_count", 0)
    strong_uptrend_comparison = {
        "posthoc_council_pf": posthoc_su,
        "posthoc_is_lookahead": True,
        "posthoc_not_tradeable": True,
        "pit_council_pf": pit_su,
        "pit_council_trade_count": pit_su_n,
        "hint_survives_without_lookahead": bool(pit_su is not None and pit_su >= 1.0),
        "note": "posthoc STRONG_UPTREND 은 사후 라벨(매매 불가). PIT 에서 PF≥1 이어도 research "
                "hint 일 뿐 — paper rehearsal 후보 아님.",
    }

    verdict, conclusion, survivors, failures = _verdict(group_results, regime_results)

    coverage = _coverage(meta)
    sufficient_groups = [g for g, c in coverage["by_group"].items() if c["sufficient"]]
    missing_symbols = universe.get("failed_symbols", [])
    # 기간 비대칭(그룹별 max_trading_days 편차) 경고.
    spans = [c["max_trading_days"] for c in coverage["by_group"].values() if c["present"]]
    period_asym = bool(spans) and (max(spans) - min(spans) > 20)

    partial_block: dict[str, Any] = {}
    if allow_partial:
        # partial 진단: verdict 를 3종으로 제한.
        if len(sufficient_groups) < 3:
            verdict = "NEED_FULL_COLLECTION"
            conclusion = [f"데이터 충분 그룹 {sufficient_groups} (<3개) — 종목군 비교 불가. "
                          "background 수집 완료 후 전체 재검증 필요. 본 결과는 진단 전용."]
        elif survivors:
            verdict = "GROUP_SIGNAL_HINT_FOUND"
            conclusion = [f"일부 그룹×전략에서 PF≥1 *힌트*: {survivors} — *확정 엣지 아님*, "
                          "표본/기간 부족. 전체 수집 + OOS 재검증 필요."]
        else:
            verdict = "PARTIAL_DIAGNOSTIC_ONLY"
            conclusion = ["부분 데이터 진단 — 가용 그룹에서 뚜렷한 PF≥1 신호 없음. 확정 결론 "
                          "아님, 전체 수집 후 재검증 필요."]
        partial_block = {
            "partial_data": True,
            "warning": "PARTIAL_DATA_WARNING",
            "available_symbols_count": len(needed),
            "missing_symbols": missing_symbols,
            "group_coverage": coverage["by_group"],
            "date_range_by_group": {g: c["date_range"] for g, c in coverage["by_group"].items()},
            "bars_by_group": {g: c["total_bars"] for g, c in coverage["by_group"].items()},
            "period_asymmetry_warning": period_asym,
            "overall_date_range": coverage["overall_date_range"],
            "confidence": "PARTIAL",
            "sufficient_groups": sufficient_groups,
            "do_not_use_as_final": True,
        }

    return {
        "available": True, "mode": "universe_regime_backtest", "is_research_only": True,
        "partial_data": bool(allow_partial),
        "confidence": "PARTIAL" if allow_partial else "RESEARCH",
        "cost_model": {"round_trip_bps": _ROUND_TRIP_BPS, "execution": "5m_fallback_uniform"},
        "universe_manifest": universe,
        "regime_manifest": regime_manifest,
        "coverage": coverage,
        **partial_block,
        "group_results": group_results,
        "regime_results": regime_results,
        "point_in_time_regime_results": pit_regime_results,
        "regime_lookahead_flags": lookahead_flags(),
        "posthoc_vs_pit_agreement_rate": agreement_rate,
        "strong_uptrend_comparison": strong_uptrend_comparison,
        "survivors": survivors,
        "failures": failures,
        "verdict": verdict, "conclusion": conclusion,
        "key_questions": _key_questions(group_results, regime_results),
        "next_steps": [
            "데이터 없는 그룹(NEED_MORE_DATA)은 read-only collector 로 추가 수집 후 재검증",
            "PF≥1 그룹/국면이 있어도 자동 적용 금지 — 별도 PR + OOS + paper rehearsal 필요",
            "어떤 그룹/전략도 런타임 전략으로 등록/적용되지 않음",
        ],
        "auto_apply_allowed": False, "applied_to_runtime": False,
        "is_live_authorization": False, "is_order_signal": False,
        "no_profit_guarantee": True, "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. posthoc regime은 사후 "
                      "분석용이며 매매 규칙으로 사용할 수 없습니다. point-in-time regime은 진입 "
                      "시점 정보만 사용하지만, 아직 연구용 attribution입니다. 어떤 전략/종목군도 "
                      "런타임에 자동 적용되지 않습니다. 수익을 보장하지 않습니다.",
    }


def _best_group_strategy(group_results) -> list[tuple]:
    """그룹×(전략/council/MR) net PF≥1 인 살아남은 조합."""
    out = []
    for g, gr in group_results.items():
        if not gr.get("present"):
            continue
        for k, blk in gr.get("four_strategy", {}).items():
            if (blk.get("net_pf") or 0) >= 1.0:
                out.append((g, k, blk["net_pf"]))
        if (gr.get("council", {}).get("net_pf") or 0) >= 1.0:
            out.append((g, "COUNCIL", gr["council"]["net_pf"]))
        for k, blk in gr.get("mean_reversion", {}).items():
            if (blk.get("net_pf") or 0) >= 1.0:
                out.append((g, f"MR:{k}", blk["net_pf"]))
    return out


def _verdict(group_results, regime_results):
    survivors = _best_group_strategy(group_results)
    no_data = [g for g, gr in group_results.items() if not gr.get("present")]
    # Risk Filter 보편 효과: 모든 데이터 그룹에서 RF PF > council PF?
    rf_helps = []
    for g, gr in group_results.items():
        if not gr.get("present"):
            continue
        cpf = (gr.get("council", {}) or {}).get("net_pf") or 0
        rfpf = gr.get("council_risk_filter_pf") or 0
        if rfpf > cpf:
            rf_helps.append(g)
    failures = [g for g, gr in group_results.items()
                if gr.get("present") and not any(
                    (gr["four_strategy"][k].get("net_pf") or 0) >= 1.0 for k in gr["four_strategy"])
                and (gr.get("council", {}).get("net_pf") or 0) < 1.0]

    strong = [s for s in survivors if s[2] >= 1.2]
    data_groups = [g for g, gr in group_results.items() if gr.get("present")]
    if len(data_groups) < 3 and no_data:
        v = "NEED_MORE_DATA"
        c = [f"데이터 충분 그룹 {len(data_groups)}개 — {no_data} 미수집. 다변화 결론 내리기엔 "
             "표본 부족. read-only 수집 후 재검증 필요."]
    elif strong:
        regimes = [r for s in strong for r in [s]]  # noqa: F841
        v = "GROUP_SPECIFIC_EDGE_FOUND"
        c = [f"특정 그룹×전략에서 PF≥1.2: {strong} — 그룹 한정 후보(자동 적용 금지, OOS/paper "
             "rehearsal 필요). 대형주 실패가 전 시장 실패는 아님."]
    elif survivors:
        v = "GROUP_SPECIFIC_EDGE_FOUND"
        c = [f"일부 그룹×전략 PF≥1.0: {survivors} — 약한 그룹 한정 신호. 자동 적용 금지."]
    elif len(rf_helps) >= max(2, len(data_groups) - 1):
        v = "RISK_FILTER_UNIVERSAL_CANDIDATE"
        c = [f"전략 엣지는 없으나 Risk Filter 가 대부분 그룹({rf_helps})에서 council 손실을 줄임 "
             "— 보편적 손실 방어 후보. 단 PF<1 이면 실전성 아님."]
    else:
        v = "UNIVERSE_EDGE_NOT_FOUND"
        c = ["데이터가 있는 모든 종목군에서 4전략/Council/평균회귀 모두 비용 후 PF<1 — 종목군 "
             "다변화로도 엣지 회복 실패. 단 미수집 그룹은 별도."]
    return v, c, [f"{g}+{k}({pf:.3f})" for g, k, pf in survivors], failures


def _key_questions(group_results, regime_results):
    def gpf(g, k):
        gr = group_results.get(g, {})
        return (gr.get("four_strategy", {}).get(k, {}) or {}).get("net_pf")

    def rpf(rg, k):
        return (regime_results.get(rg, {}).get(k, {}) or {}).get("net_pf")

    return {
        "1_large_cap_only_fails": "LARGE_CAP_CORE best PF "
        f"{(group_results.get('LARGE_CAP_CORE', {}).get('best_single_pf'))}",
        "3_theme_orb_momentum": f"HIGH_VOL_THEME ORB={gpf('HIGH_VOL_THEME','ORB')} "
        f"MOMENTUM={gpf('HIGH_VOL_THEME','MOMENTUM')}",
        "5_strong_uptrend_trend_following": f"STRONG_UPTREND ORB={rpf('STRONG_UPTREND','ORB')} "
        f"MOMENTUM={rpf('STRONG_UPTREND','MOMENTUM')}",
        "6_sideways_mean_reversion": "SIDEWAYS 평균회귀는 group_results.mean_reversion 참조",
    }


def _empty(n, syms, universe, regime_manifest):
    return {"available": False, "verdict": "NEED_MORE_DATA", "is_research_only": True,
            "reason": "INSUFFICIENT_SYMBOLS", "symbol_count": n, "symbols": syms,
            "universe_manifest": universe, "regime_manifest": regime_manifest,
            "auto_apply_allowed": False, "applied_to_runtime": False,
            "is_live_authorization": False, "no_profit_guarantee": True,
            "contains_secret": False,
            "disclaimer": "연구용 백테스트 — universe 데이터 부족. 자동 적용 안 됨, 실전매매 권고 아님."}
