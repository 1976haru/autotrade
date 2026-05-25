"""KIS-INTRADAY-FORWARD-UNIVERSE-REBUILD-01 — forward universe 검증 오케스트레이터 (read-only).

각 rebalance 시점에서 lookback 구간만으로 종목을 선정(`forward_universe_selector`)하고 다음
test 구간에 고정 적용해 chain. selector variants / lookback·rebalance grid / Agent·손실방어
조합을 비교하고 forward universe verdict + EXE 재빌드 권고를 산출한다.

Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
**EXE 빌드 0건** (권고 라벨만). look-ahead selector(STATIC_IN_SAMPLE_TOP10)는 참고용 표시만,
최종 후보에서 제외.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.system import forward_universe_selector as fus

_KST = timezone(timedelta(hours=9))

UNIVERSE_FAIL = "UNIVERSE_FAIL"
UNIVERSE_WEAK = "UNIVERSE_WEAK"
UNIVERSE_WATCH = "UNIVERSE_WATCH"
UNIVERSE_PAPER_CANDIDATE = "UNIVERSE_PAPER_CANDIDATE"
_UV_RANK = {UNIVERSE_FAIL: 0, UNIVERSE_WEAK: 1, UNIVERSE_WATCH: 2, UNIVERSE_PAPER_CANDIDATE: 3}

_EXE_REC = {
    UNIVERSE_FAIL: "EXE 재빌드 보류 (forward universe 실패) — 검증 계속",
    UNIVERSE_WEAK: "EXE 재빌드 보류 또는 관찰용 UI만 (자동매매/모의주문 비활성)",
    UNIVERSE_WATCH: "EXE 관찰용 재빌드 가능, 자동매매/모의주문 비활성",
    UNIVERSE_PAPER_CANDIDATE: "EXE 재빌드 후 dry-run 중심 KIS 모의 리허설 가능 (실전 금지)",
}

# 기본 고정 룰 (검증 전 정의).
DEFAULT_RULE = {"agent_mode": "AGENT_RISK_VETO_ONLY", "selection_mode": "composite_rank",
                "daily_loss_stop_pct": 1.5, "allowed_strategies": ("GAP", "ORB", "VWAP")}


@dataclass(frozen=True)
class ForwardUniverseReport:
    generated_at: str
    data_source: str
    rule_locked_before_test: bool
    score_formula: dict[str, Any]
    selector_results: tuple[dict[str, Any], ...]
    lookback_rebalance_grid: tuple[dict[str, Any], ...]
    agent_combo: tuple[dict[str, Any], ...]
    defense_combo: tuple[dict[str, Any], ...]
    static_vs_forward: dict[str, Any]
    repeated_selected: tuple[str, ...]
    repeated_excluded: tuple[str, ...]
    missed_opportunity: tuple[dict[str, Any], ...]
    best_selector: dict[str, Any]
    top_by_return: tuple[dict[str, Any], ...]
    most_stable: tuple[dict[str, Any], ...]
    overfit_warning: dict[str, Any]
    final_universe_verdict: str
    paper_rehearsal_recommendation: str
    exe_rebuild_recommendation: str
    next_steps: tuple[str, ...]
    conclusions: tuple[str, ...]
    # 안전 불변값.
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    live_trading_recommendation: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    exe_build_executed: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    safety_disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. forward universe 가 통과해도 "
        "실전매매는 금지이며, Paper rehearsal 은 KIS 모의매매 후보일 뿐입니다. 수익을 보장하지 "
        "않습니다. 본 작업은 EXE 빌드를 수행하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed
                or self.exe_build_executed or self.live_trading_recommendation):
            raise ValueError("unsafe invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_universe_verdict not in _UV_RANK:
            raise ValueError(f"invalid verdict: {self.final_universe_verdict}")


def _date_of(b) -> Any:
    return b.timestamp.astimezone(_KST).date()


def _slice(bars, signals, date_set):
    sb = [b for b in bars if _date_of(b) in date_set]
    keep = {(b.symbol, b.timestamp.isoformat()) for b in sb}
    return sb, {k: v for k, v in signals.items() if k in keep}


def _chain(returns: list[float]) -> dict[str, Any]:
    eq = 10_000_000.0
    series = [eq]
    for r in returns:
        eq *= (1 + (r or 0) / 100.0)
        series.append(eq)
    peak, mdd = series[0], 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    pos = sum(1 for r in returns if (r or 0) > 0)
    return {"forward_return_pct": round((series[-1] - 1e7) / 1e7 * 100, 3),
            "forward_mdd_pct": round(mdd * 100, 2), "periods": len(returns),
            "positive_periods": pos,
            "positive_ratio": round(pos / len(returns), 3) if returns else None,
            "worst_period_pct": round(min(returns), 3) if returns else None}


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    u = a | b
    return len(a & b) / len(u) if u else 1.0


def run_universe_backtest(
    bars, signals, *, selector: str, lookback_days: int, rebalance_freq: str,
    universe_size: int, rule: dict[str, Any], in_sample_top10: frozenset[str] | None,
    label: str,
) -> dict[str, Any]:
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2

    days = fus.trading_days(bars)
    rdates = fus.rebalance_dates(days, rebalance_freq)
    period_returns: list[float] = []
    per_period: list[dict[str, Any]] = []
    universes: list[frozenset] = []
    total_trades = 0
    prev_sel: frozenset | None = None
    veto_counts: dict[str, int] = defaultdict(int)

    for i, rd in enumerate(rdates):
        # lookback = rd 이전 trading day 중 마지막 lookback_days.
        prior = [d for d in days if d < rd]
        if len(prior) < lookback_days:
            continue
        lb_dates = set(prior[-lookback_days:])
        nxt = rdates[i + 1] if i + 1 < len(rdates) else None
        test_dates = {d for d in days if rd <= d and (nxt is None or d < nxt)}
        if not test_dates:
            continue
        lb_bars, lb_sig = _slice(bars, signals, lb_dates)
        scores = fus.compute_symbol_scores(lb_bars, lb_sig, agent_veto_counts=dict(veto_counts))
        uni, excl, smeta = fus.select_universe(
            selector, scores, size=universe_size, in_sample_top10=in_sample_top10,
            prev_selected=prev_sel)
        tb, tsig = _slice(bars, signals, test_dates)
        kw = dict(agent_mode=rule["agent_mode"], selection_mode=rule.get("selection_mode", "composite_rank"),
                  universe=uni, grade_map={}, strategy_pf={}, bucket_edge={})
        for k in ("daily_loss_stop_pct", "equity_dd_stop_pct", "worst_symbol_cooldown"):
            if k in rule:
                kw[k] = rule[k]
        if rule.get("allowed_strategies"):
            kw["allowed_strategies"] = frozenset(rule["allowed_strategies"])
        if rule.get("apply_position_sizing"):
            kw["apply_position_sizing"] = True
        r = result_to_dict(run_sim_v2(tb, tsig, SimV2Config(**kw)))
        period_returns.append(r["total_return_pct"])
        total_trades += r["trade_count"]
        sel_set = uni if uni is not None else frozenset(scores)
        universes.append(sel_set)
        per_period.append({"rebalance_date": str(rd), "selected": sorted(sel_set)[:25],
                           "selected_count": len(sel_set), "excluded_count": len(excl),
                           "return_pct": r["total_return_pct"], "trades": r["trade_count"],
                           "look_ahead": smeta.get("look_ahead", False)})
        prev_sel = sel_set

    chained = _chain(period_returns)
    # selection turnover / stability.
    turns = [1 - _jaccard(universes[i], universes[i - 1]) for i in range(1, len(universes))]
    stab = [_jaccard(universes[i], universes[i - 1]) for i in range(1, len(universes))]
    return {
        "_label": label, "selector": selector, "lookback_days": lookback_days,
        "rebalance_freq": rebalance_freq, "universe_size": universe_size,
        "look_ahead_warning": selector in fus._LOOKAHEAD_SELECTORS,
        **chained, "total_trades": total_trades,
        "low_confidence": total_trades < 100,
        "selection_turnover": round(statistics.mean(turns), 3) if turns else None,
        "universe_stability": round(statistics.mean(stab), 3) if stab else None,
        "per_period": per_period, "universes": [sorted(u)[:25] for u in universes],
    }


def _verdict(res: dict[str, Any]) -> str:
    fr = res.get("forward_return_pct")
    mdd = res.get("forward_mdd_pct")
    trades = res.get("total_trades", 0)
    posr = res.get("positive_ratio") or 0
    if fr is None:
        return UNIVERSE_FAIL
    if fr < 0 or trades < 30:
        return UNIVERSE_FAIL
    if fr >= 5 and (mdd or 99) <= 15 and trades >= 100 and posr >= 0.55:
        return UNIVERSE_PAPER_CANDIDATE
    if fr >= 2 and (mdd or 99) <= 20 and trades >= 60:
        return UNIVERSE_WATCH
    return UNIVERSE_WEAK


def _in_sample_top10(bars, signals) -> frozenset[str]:
    """비교용(look-ahead) 전체기간 top10 — 최종 후보엔 미사용."""
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
    by_sym = result_to_dict(run_sim_v2(bars, signals, SimV2Config())).get("by_symbol", {})
    ranked = sorted([(s, v["net_pnl"]) for s, v in by_sym.items()], key=lambda x: -(x[1] or 0))
    return frozenset(s for s, _ in ranked[:10])


def run_forward_universe(
    input_dir: str | Path, *, symbols: list[str] | None = None, generated_at: str | None = None,
) -> ForwardUniverseReport:
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    from app.system.wf_6m_signal_extract import extract_signal_map

    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        want = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower().split("_")[0] in want]
    bars: list[Any] = []
    for f in files:
        bars += load_ohlcv_from_csv(str(f))
    signals = extract_signal_map(bars).signals
    is_top10 = _in_sample_top10(bars, signals)

    # 1. selector variants (lookback=40, monthly, size=10, DEFAULT_RULE).
    selector_results = []
    for sel in fus.SELECTOR_VARIANTS:
        r = run_universe_backtest(bars, signals, selector=sel, lookback_days=40,
                                  rebalance_freq="monthly", universe_size=10,
                                  rule=DEFAULT_RULE, in_sample_top10=is_top10, label=f"SEL_{sel}")
        r["verdict"] = _verdict(r) if not r["look_ahead_warning"] else "LOOK_AHEAD_REF"
        selector_results.append(r)

    fwd_only = [r for r in selector_results if not r["look_ahead_warning"]]
    best_sel_name = max(fwd_only, key=lambda r: (r["forward_return_pct"] or -1e9))["selector"] \
        if fwd_only else "FORWARD_SCORE_TOP10"

    # 2. lookback/rebalance grid (best selector).
    grid = []
    for lb in (20, 40, 60):
        for freq in ("weekly", "monthly"):
            r = run_universe_backtest(bars, signals, selector=best_sel_name, lookback_days=lb,
                                      rebalance_freq=freq, universe_size=10, rule=DEFAULT_RULE,
                                      in_sample_top10=is_top10, label=f"GRID_{best_sel_name}_{lb}d_{freq}")
            r["verdict"] = _verdict(r)
            grid.append(r)

    # 3. Agent 모드 (best selector, monthly, 40d).
    agent_combo = []
    for amode, sizing in (("AGENT_OFF", False), ("AGENT_RISK_VETO_ONLY", False),
                          ("AGENT_POSITION_SIZER_ONLY", False),
                          ("AGENT_RISK_VETO_ONLY", True), ("AGENT_REVIEW_ONLY", False)):
        rule = {**DEFAULT_RULE, "agent_mode": amode}
        if sizing:
            rule["apply_position_sizing"] = True
        lbl = f"AGENT_{amode}{'+SIZER' if sizing else ''}"
        r = run_universe_backtest(bars, signals, selector=best_sel_name, lookback_days=40,
                                  rebalance_freq="monthly", universe_size=10, rule=rule,
                                  in_sample_top10=is_top10, label=lbl)
        r["verdict"] = _verdict(r)
        agent_combo.append(r)

    # 4. 손실방어 (best selector).
    defense_combo = []
    for dname, drule in (("none", {}), ("daily_1.5", {"daily_loss_stop_pct": 1.5}),
                         ("equity_dd_10", {"equity_dd_stop_pct": 10.0}),
                         ("daily_1.5+symcool", {"daily_loss_stop_pct": 1.5, "worst_symbol_cooldown": True})):
        rule = {**DEFAULT_RULE, **drule}
        r = run_universe_backtest(bars, signals, selector=best_sel_name, lookback_days=40,
                                  rebalance_freq="monthly", universe_size=10, rule=rule,
                                  in_sample_top10=is_top10, label=f"DEF_{dname}")
        r["verdict"] = _verdict(r)
        defense_combo.append(r)

    # static vs forward.
    static_all = next((r for r in selector_results if r["selector"] == "STATIC_BASELINE_ALL"), {})
    static_is = next((r for r in selector_results if r["selector"] == "STATIC_IN_SAMPLE_TOP10"), {})
    best_fwd = max(fwd_only, key=lambda r: (r["forward_return_pct"] or -1e9)) if fwd_only else {}
    static_vs_forward = {
        "static_all_return": static_all.get("forward_return_pct"),
        "static_in_sample_top10_return": static_is.get("forward_return_pct"),
        "static_in_sample_look_ahead": True,
        "best_forward_selector": best_fwd.get("selector"),
        "best_forward_return": best_fwd.get("forward_return_pct"),
        "note": ("STATIC_IN_SAMPLE_TOP10 은 미래 전체를 보고 고른 편향 결과(참고용). FORWARD_* 만 "
                 "실제 후보. forward selector 가 static all 보다 나아야 universe 선별이 의미 있음."),
    }

    # 반복 선택/제외 + missed opportunity (best forward selector 기준).
    rep_sel, rep_excl, missed = (), (), ()
    if best_fwd:
        sel_freq: dict[str, int] = defaultdict(int)
        for p in best_fwd.get("per_period", []):
            for s in p["selected"]:
                sel_freq[s] += 1
        nper = max(1, len(best_fwd.get("per_period", [])))
        rep_sel = tuple(s for s, c in sorted(sel_freq.items(), key=lambda x: -x[1]) if c >= nper * 0.6)[:15]
        all_syms = {b.symbol for b in bars}
        rep_excl = tuple(sorted(all_syms - set(sel_freq)))[:15]
        # missed: 선택 안 됐는데 전체기간 수익 좋은 종목 (분석용 — 선택엔 미사용).
        from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
        by_sym = result_to_dict(run_sim_v2(bars, signals, SimV2Config())).get("by_symbol", {})
        missed = tuple({"symbol": s, "full_period_net_pnl": by_sym[s]["net_pnl"]}
                       for s in sorted(by_sym, key=lambda x: -(by_sym[x]["net_pnl"] or 0))
                       if s not in set(sel_freq) and (by_sym[s]["net_pnl"] or 0) > 0)[:10]

    # verdict (best forward selector).
    final_verdict = _verdict(best_fwd) if best_fwd else UNIVERSE_FAIL
    top_by_return = tuple(sorted(fwd_only, key=lambda r: (r["forward_return_pct"] or -1e9), reverse=True)[:10])
    stable_pool = [r for r in fwd_only if (r["forward_return_pct"] or -1) >= 0]
    most_stable = tuple(sorted(stable_pool, key=lambda r: (r["forward_mdd_pct"] if r["forward_mdd_pct"] is not None else 1e9))[:10])

    overfit = {
        "warning": final_verdict in (UNIVERSE_FAIL, UNIVERSE_WEAK),
        "best_forward_return": best_fwd.get("forward_return_pct"),
        "static_in_sample_top10_return": static_is.get("forward_return_pct"),
        "note": ("forward selector 가 static in-sample top10 보다 낮거나 음(-)이면 universe 선별이 "
                 "여전히 forward 에서 작동하지 않는 것. look-ahead selector 는 후보 제외."),
    }
    paper_rec = ("KIS 모의매매 dry-run 리허설 후보 (forward universe 통과, 단 실전 금지)"
                 if final_verdict == UNIVERSE_PAPER_CANDIDATE else
                 "Paper 리허설 아직 불가 — forward universe 보강 필요")
    conclusions = [
        f"best forward selector: {best_fwd.get('selector')} · forward {best_fwd.get('forward_return_pct')}% · "
        f"MDD {best_fwd.get('forward_mdd_pct')}% · 거래 {best_fwd.get('total_trades')} · {final_verdict}.",
        ("point-in-time universe 선별이 forward 에서도 양(+)을 유지하면 in-sample 편향이 일부 해소된 것."
         if final_verdict in (UNIVERSE_WATCH, UNIVERSE_PAPER_CANDIDATE) else
         "forward universe 도 약함/음(-) — 종목 선별이 forward 에서 안정적 엣지를 못 만듦(추가 단순화 필요)."),
        "Agent 는 RISK_VETO_ONLY 결합 결과를 우선 확인 (이전 분석에서 OFF 는 OOS 악화).",
    ]
    next_steps = [
        "forward universe verdict 가 WATCH 이상일 때만 관찰용 EXE 재빌드 검토 (자동매매 비활성).",
        "selector stability 높고 forward 양(+)인 후보를 추가 기간/추가 종목으로 재검증.",
        "WEAK/FAIL 이면 score 산식 단순화 + 비용/회전 축소 후 재검증 (재최적화 금지).",
    ]

    return ForwardUniverseReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        rule_locked_before_test=True,
        score_formula={"weights": fus.SCORE_WEIGHTS, "roundtrip_cost_frac": fus.ROUNDTRIP_COST_FRAC,
                       "min_trades": fus.MIN_TRADES,
                       "penalties": ["low_trades", "neg_cost_edge", "low_win_rate",
                                     "signal_noise", "agent_veto_repeat"]},
        selector_results=tuple(_slim_sel(r) for r in selector_results),
        lookback_rebalance_grid=tuple(_slim_sel(r) for r in grid),
        agent_combo=tuple(_slim_sel(r) for r in agent_combo),
        defense_combo=tuple(_slim_sel(r) for r in defense_combo),
        static_vs_forward=static_vs_forward, repeated_selected=rep_sel,
        repeated_excluded=rep_excl, missed_opportunity=missed,
        best_selector=_slim_sel(best_fwd) if best_fwd else {},
        top_by_return=tuple(_slim_sel(r) for r in top_by_return),
        most_stable=tuple(_slim_sel(r) for r in most_stable), overfit_warning=overfit,
        final_universe_verdict=final_verdict, paper_rehearsal_recommendation=paper_rec,
        exe_rebuild_recommendation=_EXE_REC[final_verdict], next_steps=tuple(next_steps),
        conclusions=tuple(conclusions),
        _extra={"best_per_period": best_fwd.get("per_period", []) if best_fwd else []})


def _slim_sel(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r.get(k) for k in (
        "_label", "selector", "lookback_days", "rebalance_freq", "universe_size", "verdict",
        "forward_return_pct", "forward_mdd_pct", "positive_ratio", "worst_period_pct",
        "total_trades", "selection_turnover", "universe_stability", "low_confidence",
        "look_ahead_warning")}


def to_dict(r: ForwardUniverseReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "rule_locked_before_test": r.rule_locked_before_test, "score_formula": r.score_formula,
        "selector_results": list(r.selector_results),
        "lookback_rebalance_grid": list(r.lookback_rebalance_grid),
        "agent_combo": list(r.agent_combo), "defense_combo": list(r.defense_combo),
        "static_vs_forward": r.static_vs_forward, "repeated_selected": list(r.repeated_selected),
        "repeated_excluded": list(r.repeated_excluded),
        "missed_opportunity": list(r.missed_opportunity), "best_selector": r.best_selector,
        "top_by_return": list(r.top_by_return), "most_stable": list(r.most_stable),
        "overfit_warning": r.overfit_warning, "final_universe_verdict": r.final_universe_verdict,
        "paper_rehearsal_recommendation": r.paper_rehearsal_recommendation,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation,
        "next_steps": list(r.next_steps), "conclusions": list(r.conclusions),
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization,
        "live_trading_recommendation": r.live_trading_recommendation,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "exe_build_executed": r.exe_build_executed, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "safety_disclaimer": r.safety_disclaimer,
    }


def render_markdown(r: ForwardUniverseReport) -> str:
    def _t(rows, n=12):
        out = ["| label | verdict | forward% | MDD% | trades | pos | turnover | stable | LA |",
               "|---|---|---|---|---|---|---|---|---|"]
        for e in rows[:n]:
            out.append(f"| {e['_label']} | {e.get('verdict')} | {e['forward_return_pct']} | "
                       f"{e['forward_mdd_pct']} | {e['total_trades']} | {e['positive_ratio']} | "
                       f"{e['selection_turnover']} | {e['universe_stability']} | "
                       f"{'Y' if e['look_ahead_warning'] else ''} |")
        return out
    b = r.best_selector
    lines = [
        "# WF-6M Forward Universe Rebuild — point-in-time 종목 선별 검증",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        f"> rule_locked_before_test = {r.rule_locked_before_test} · look-ahead selector 는 참고용(후보 제외)",
        "",
        f"## 최종 forward universe verdict: **{r.final_universe_verdict}**",
        f"- best forward selector: **{b.get('selector')}** · forward {b.get('forward_return_pct')}% · "
        f"MDD {b.get('forward_mdd_pct')}% · 거래 {b.get('total_trades')} · stability {b.get('universe_stability')}",
        f"- EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        f"- Paper 리허설: {r.paper_rehearsal_recommendation}",
        f"- 실전매매 권고: **{r.live_trading_recommendation}** (항상 false)",
        "",
        "## Score 산식 (고정)",
        f"- weights: {r.score_formula['weights']} · 왕복비용 {r.score_formula['roundtrip_cost_frac']:.4f} · "
        f"penalties {r.score_formula['penalties']}",
        "",
        "## Selector variants (lookback 40d, monthly, size 10, RISK_VETO+composite+daily1.5)",
        *_t(list(r.selector_results), 12),
        "",
        "## Lookback / Rebalance grid (best selector)",
        *_t(list(r.lookback_rebalance_grid), 6),
        "",
        "## Agent 결합 (best selector)",
        *_t(list(r.agent_combo), 5),
        "",
        "## 손실방어 결합 (best selector)",
        *_t(list(r.defense_combo), 4),
        "",
        "## Static vs Forward",
        f"- static ALL {r.static_vs_forward.get('static_all_return')}% · "
        f"static in-sample top10(편향) {r.static_vs_forward.get('static_in_sample_top10_return')}% · "
        f"best forward {r.static_vs_forward.get('best_forward_return')}%",
        "",
        f"## 반복 선택 종목: {list(r.repeated_selected)}",
        f"## 반복 제외 종목: {list(r.repeated_excluded)}",
        f"## missed opportunity(분석용): {[m['symbol'] for m in r.missed_opportunity]}",
        "",
        f"## overfit/look-ahead 경고: {r.overfit_warning.get('warning')} — {r.overfit_warning.get('note')}",
        "",
        "## 다음 단계",
        *[f"- {s}" for s in r.next_steps],
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "",
        f"> {r.safety_disclaimer}",
    ]
    return "\n".join(lines) + "\n"
