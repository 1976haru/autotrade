"""KIS-INTRADAY-50-6M-STRATEGY-AGENT-DECOMPOSITION-01 — 매매기법 vs Agent 분해 (read-only).

baseline(-19%) 대비: ① 매매기법 only ② 전략 단독/조합 ③ Agent 7역할 ④ 신호선택 ⑤ 비용
⑥ universe ⑦ 시간/보유/청산 ⑧ 손실장 방어 ⑨ rolling OOS ⑩ 통합조합 TOP 을 한 리포트로.

엔진: `wf_6m_signal_extract`(캐시) + `wf_6m_sim_v2`. verdict/grade 재사용. Paper/Backtest only.
broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건. **EXE 빌드 수행 0건** —
EXE 재빌드 *권고 라벨*만 산출.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.system import wf_6m_rebuild_experiments as rb

_SLIM_KEYS = ("_label", "verdict", "total_return_pct", "profit_factor", "max_drawdown_pct",
              "expectancy", "win_rate", "trade_count", "avg_trades_per_day",
              "worst_day_pnl", "worst_consecutive_losses", "avg_hold_minutes",
              "walk_forward_score", "oos_positive", "turnover", "cost_paid_total",
              "tax_paid_total", "slippage_paid_total", "low_confidence")

# EXE 재빌드 권고 (verdict → 라벨).
_EXE_REC = {
    rb.BLOCKED: "EXE 재빌드 보류 (분석 불가/안전 위반)",
    rb.STILL_NOT_RECOMMENDED: "EXE 재빌드 보류 (개선 미달 — 실전/모의 의미 없음)",
    rb.WATCHLIST_ONLY: "EXE 빌드 가능하나 자동매매 기능은 비활성/관찰용만",
    rb.PAPER_REHEARSAL_CANDIDATE: "EXE 재빌드 후 *모의매매 리허설* 가능 (실전 금지)",
    rb.RESEARCH_PROMISING: "EXE 재빌드 후 모의 리허설 가능 (실전 금지, 추가 OOS 검증 권장)",
}


@dataclass(frozen=True)
class DecompositionReport:
    generated_at: str
    data_source: str
    baseline: dict[str, Any]
    strategy_only: tuple[dict[str, Any], ...]
    agent_roles: tuple[dict[str, Any], ...]
    agent_off_comparison: dict[str, Any]
    signal_selection: tuple[dict[str, Any], ...]
    five_slot_analysis: dict[str, Any]
    cost_sensitivity: dict[str, Any]
    universe_filter: tuple[dict[str, Any], ...]
    time_hold_exit: dict[str, Any]
    loss_defense: tuple[dict[str, Any], ...]
    walk_forward_oos: dict[str, Any]
    integrated_combos: tuple[dict[str, Any], ...]
    top_by_return: tuple[dict[str, Any], ...]
    top_by_stability: tuple[dict[str, Any], ...]
    top_by_walk_forward: tuple[dict[str, Any], ...]
    top_low_live_risk: tuple[dict[str, Any], ...]
    final_verdict: str
    paper_rehearsal_candidate: bool
    exe_rebuild_recommendation: str
    conclusions: tuple[str, ...]
    grade_map: dict[str, str]
    # 안전 불변값.
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    exe_build_executed: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 어떤 조합도 자동 적용/실전 "
        "전환되지 않습니다. 'Paper rehearsal candidate'는 *모의 리허설 후보*일 뿐이며 수익을 "
        "보장하지 않습니다. 본 작업은 EXE 빌드를 수행하지 않습니다(권고 라벨만)."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed
                or self.exe_build_executed):
            raise ValueError("unsafe apply/exe invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in rb._TIER_RANK:
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def _bucket_edge(signals: dict) -> dict[str, float]:
    agg: dict[str, list[float]] = defaultdict(list)
    for s in signals.values():
        agg[s["time_bucket"]].append(s["fwd_eod_return"])
    return {k: round(statistics.mean(v), 6) for k, v in agg.items() if v}


def build_decomposition(
    input_dir: str | Path, *, symbols: list[str] | None = None,
    grade_map: dict[str, str] | None = None, generated_at: str | None = None,
) -> DecompositionReport:
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    from app.system.wf_6m_signal_extract import extract_signal_map
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2

    gen = generated_at or datetime.now(timezone.utc).isoformat()
    d = Path(input_dir)
    files = sorted(d.glob("*.csv")) if d.is_dir() else []
    if symbols:
        want = {s.lower() for s in symbols}
        files = [f for f in files if f.stem.lower().split("_")[0] in want]
    bars: list[Any] = []
    for f in files:
        bars += load_ohlcv_from_csv(str(f))

    sm = extract_signal_map(bars)
    signals = sm.signals
    bedge = _bucket_edge(signals)
    strat_pf = sm.strategy_fwd_pf
    if grade_map is None:
        from app.system.wf_6m_root_cause_analysis import _load_grade_map
        grade_map = _load_grade_map(input_dir, symbols)

    go_tune = frozenset(s for s, g in grade_map.items() if g in ("GO", "TUNE"))
    go_only = frozenset(s for s, g in grade_map.items() if g == "GO")
    ranked = sorted(go_tune, key=lambda s: ({"GO": 0, "TUNE": 1}.get(grade_map.get(s), 2), s))
    top5, top10, top20 = frozenset(ranked[:5]), frozenset(ranked[:10]), frozenset(ranked[:20])

    all_runs: list[dict[str, Any]] = []

    def _cfg(**kw) -> SimV2Config:
        base = dict(grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge)
        base.update(kw)
        return SimV2Config(**base)

    def _run(label: str, **kw) -> dict[str, Any]:
        full = result_to_dict(run_sim_v2(bars, signals, _cfg(**kw), label=label))
        full["_label"] = label
        mr = full.get("monthly_returns", {})
        full["_min_month"] = min(mr.values()) if mr else None
        wm_def = full["_min_month"] is not None and full["_min_month"] > -10.0
        agent_reduced = "ENTRY_DECIDER" not in label and "ENTRY_SELECTOR" not in label
        full["verdict"] = rb.verdict_for(full, worst_month_defended=wm_def,
                                         agent_hurt_reduced=agent_reduced)
        slim = {k: full.get(k) for k in _SLIM_KEYS}
        slim["monthly_returns"] = mr
        all_runs.append(slim)
        return slim

    # 1. baseline.
    base = _run("BASELINE_earliest_council_ALL", selection_mode="earliest_first",
                agent_mode="AGENT_ENTRY_DECIDER")

    # 2-3. 매매기법 only (Agent OFF, 전략 단독/조합).
    combos = {
        "GAP_only": {"GAP"}, "ORB_only": {"ORB"}, "VWAP_only": {"VWAP"},
        "MOMENTUM_only": {"MOMENTUM"}, "GAP+ORB": {"GAP", "ORB"},
        "GAP+VWAP": {"GAP", "VWAP"}, "ORB+VWAP": {"ORB", "VWAP"},
        "GAP+ORB+VWAP": {"GAP", "ORB", "VWAP"},
        "ALL_strategies_no_agent": {"GAP", "ORB", "VWAP", "MOMENTUM"},
    }
    strategy_only = tuple(
        _run(f"STRAT_{name}", agent_mode="AGENT_OFF", allowed_strategies=frozenset(s))
        for name, s in combos.items())

    # 4. Agent 7역할 (동일 universe=ALL, earliest, 비용).
    role_cfgs = {
        "AGENT_OFF": {"agent_mode": "AGENT_OFF"},
        "AGENT_ENTRY_SELECTOR": {"agent_mode": "AGENT_ENTRY_SELECTOR"},
        "AGENT_RISK_VETO_ONLY": {"agent_mode": "AGENT_RISK_VETO_ONLY"},
        "AGENT_POSITION_SIZER_ONLY": {"agent_mode": "AGENT_POSITION_SIZER_ONLY"},
        "AGENT_EXIT_ADVISOR_ONLY": {"agent_mode": "AGENT_EXIT_ADVISOR_ONLY", "trailing_stop_pct": 2.0,
                                    "time_stop_min": 60},
        "AGENT_REGIME_FILTER_ONLY": {"agent_mode": "AGENT_REGIME_FILTER_ONLY",
                                     "regime_block_down_day": True},
        "AGENT_REVIEW_ONLY": {"agent_mode": "AGENT_REVIEW_ONLY"},
    }
    agent_roles = tuple(_run(f"ROLE_{n}", **c) for n, c in role_cfgs.items())
    agent_off = next(r for r in agent_roles if r["_label"] == "ROLE_AGENT_OFF")
    agent_off_comparison = {
        "agent_off_return_pct": agent_off["total_return_pct"],
        "agent_off_mdd": agent_off["max_drawdown_pct"],
        "by_role": [{"role": r["_label"], "return_pct": r["total_return_pct"],
                     "mdd": r["max_drawdown_pct"], "pf": r["profit_factor"],
                     "worst_consec": r["worst_consecutive_losses"],
                     "vs_off_return_pp": round((r["total_return_pct"] or 0) - (agent_off["total_return_pct"] or 0), 2),
                     "vs_off_mdd_pp": round((r["max_drawdown_pct"] or 0) - (agent_off["max_drawdown_pct"] or 0), 2)}
                    for r in agent_roles],
    }

    # 6. 신호선택 (universe=GO+TUNE).
    sel_modes = ("earliest_first", "symbol_grade_rank", "strategy_pf_rank",
                 "expected_move_after_cost_rank", "time_bucket_edge_rank", "composite_rank")
    signal_selection = tuple(
        _run(f"SEL_{m}", universe=go_tune, selection_mode=m) for m in sel_modes)

    # 5-slot 분석 (signal_map 통계).
    council_buys = {k: v for k, v in signals.items() if v["council_action"] == "BUY"}
    by_ts: dict[str, int] = defaultdict(int)
    for (s, ts) in council_buys:
        by_ts[ts] += 1
    over5 = [v["fwd_eod_return"] for (s, ts), v in council_buys.items() if by_ts[ts] > 5]
    five_slot_analysis = {
        "council_buy_signals": len(council_buys),
        "baseline_filled_trades": base["trade_count"],
        "fill_ratio": round(base["trade_count"] / max(1, len(council_buys)), 4),
        "timestamps_over_5_candidates": sum(1 for c in by_ts.values() if c > 5),
        "mean_fwd_all": round(statistics.mean([v["fwd_eod_return"] for v in council_buys.values()]), 6)
        if council_buys else None,
        "mean_fwd_over5": round(statistics.mean(over5), 6) if over5 else None,
        "note": "신호 평균 fwd 가 양(+)이고 5후보 초과 시점이 많으면 선착순 5슬롯이 좋은 신호를 놓침.",
    }

    # 7. 비용 민감도.
    def _cost(label, **kw):
        r = result_to_dict(run_sim_v2(bars, signals, _cfg(**kw), label=label))
        return {"return_pct": r["total_return_pct"], "pf": r["profit_factor"],
                "trades": r["trade_count"], "cost_paid": r["cost_paid_total"],
                "tax_paid": r["tax_paid_total"], "slippage_paid": r["slippage_paid_total"]}
    rt = round((2 * 1.5 + 18.0 + 2 * 5.0) / 100.0, 4)
    cost_sensitivity = {
        "baseline": {"return_pct": base["total_return_pct"], "pf": base["profit_factor"],
                     "cost_paid": base["cost_paid_total"], "tax_paid": base["tax_paid_total"],
                     "slippage_paid": base["slippage_paid_total"]},
        "slippage_0bps": _cost("c_slip0", slippage_bps=0.0),
        "slippage_2bps": _cost("c_slip2", slippage_bps=2.0),
        "slippage_5bps": _cost("c_slip5", slippage_bps=5.0),
        "slippage_10bps": _cost("c_slip10", slippage_bps=10.0),
        "no_tax": _cost("c_notax", sell_tax_bps=0.0),
        "no_fee": _cost("c_nofee", fee_bps_per_side=0.0),
        "zero_all_cost": _cost("c_zero", slippage_bps=0.0, sell_tax_bps=0.0, fee_bps_per_side=0.0),
        "roundtrip_cost_pct": rt, "breakeven_move_pct": rt,
        "note": "비용 전엔 양(+)인데 비용 후 음(-)이면 비용이 엣지를 잠식. 단타 빈도/최소 기대수익 필터 필요.",
    }

    # 8. universe 필터.
    universe_filter = (
        _run("UNI_all", agent_mode="AGENT_ENTRY_DECIDER"),
        _run("UNI_exclude_removed", universe=go_tune),
        _run("UNI_go_only", universe=go_only),
        _run("UNI_go_tune_top5", universe=top5),
        _run("UNI_go_tune_top10", universe=top10),
        _run("UNI_go_tune_top20", universe=top20),
    )

    # 9. 시간/보유/청산.
    exit_exps = (
        _run("EXIT_max_hold_30", universe=go_tune, selection_mode="composite_rank", max_hold_min=30),
        _run("EXIT_max_hold_60", universe=go_tune, selection_mode="composite_rank", max_hold_min=60),
        _run("EXIT_max_hold_120", universe=go_tune, selection_mode="composite_rank", max_hold_min=120),
        _run("EXIT_trailing_2", universe=go_tune, selection_mode="composite_rank", trailing_stop_pct=2.0),
        _run("EXIT_time_stop_30", universe=go_tune, selection_mode="composite_rank", time_stop_min=30),
        _run("EXIT_no_entry_after_1400", universe=go_tune, selection_mode="composite_rank",
             entry_cutoff_min=14 * 60),
        _run("EXIT_forced_close_1450", universe=go_tune, selection_mode="composite_rank",
             entry_cutoff_min=14 * 60, forced_close_min=14 * 60 + 50),
    )
    time_hold_exit = {
        "by_time_bucket": base.get("by_time_bucket") or _slim_buckets(bars, signals, _cfg, run_sim_v2, result_to_dict),
        "exit_experiments": list(exit_exps),
    }

    # 10. 손실장 방어.
    loss_defense = (
        _run("DEF_monthly_dd_5", universe=go_tune, selection_mode="composite_rank", monthly_dd_stop_pct=5.0),
        _run("DEF_daily_loss_1.5", universe=go_tune, selection_mode="composite_rank", daily_loss_stop_pct=1.5),
        _run("DEF_consec_loss_5", universe=go_tune, selection_mode="composite_rank", consecutive_loss_stop=5),
        _run("DEF_equity_dd_10", universe=go_tune, selection_mode="composite_rank", equity_dd_stop_pct=10.0),
        _run("DEF_size_scale_0.5", universe=go_tune, selection_mode="composite_rank", size_scale=0.5),
    )

    # 11. rolling OOS (best universe 조합의 월별).
    best_uni = max(universe_filter, key=lambda r: (r["total_return_pct"] or -1e9))
    walk_forward_oos = {
        "best_universe_label": best_uni["_label"],
        "monthly_returns": best_uni.get("monthly_returns", {}),
        "positive_months": sum(1 for v in best_uni.get("monthly_returns", {}).values() if v > 0),
        "negative_months": sum(1 for v in best_uni.get("monthly_returns", {}).values() if v < 0),
        "walk_forward_score": best_uni["walk_forward_score"],
        "oos_positive": best_uni["oos_positive"],
        "worst_month": (min(best_uni.get("monthly_returns", {}),
                            key=best_uni["monthly_returns"].get) if best_uni.get("monthly_returns") else None),
        "note": "수익률이 좋아도 OOS(마지막 구간) 음(-) 또는 특정 월 붕괴면 WATCHLIST 이하.",
    }

    # 12. 통합 조합.
    integrated_combos = (
        _run("COMBO_off_excl_composite_hold60", universe=go_tune, agent_mode="AGENT_OFF",
             selection_mode="composite_rank", max_hold_min=60),
        _run("COMBO_riskveto_top10_trail2", universe=top10, agent_mode="AGENT_RISK_VETO_ONLY",
             selection_mode="composite_rank", trailing_stop_pct=2.0),
        _run("COMBO_sizer_top10_cut1400", universe=top10, agent_mode="AGENT_POSITION_SIZER_ONLY",
             selection_mode="composite_rank", entry_cutoff_min=14 * 60),
        _run("COMBO_review_gapfocus_def", universe=go_tune, agent_mode="AGENT_REVIEW_ONLY",
             allowed_strategies=frozenset({"GAP", "ORB", "VWAP"}), monthly_dd_stop_pct=5.0),
        _run("COMBO_top10_composite_daily1.5", universe=top10, selection_mode="composite_rank",
             daily_loss_stop_pct=1.5),
    )

    # TOP 정렬 (전체 run 풀에서).
    def _ok(r):
        return r["total_return_pct"] is not None and r["profit_factor"] is not None
    pool = [r for r in all_runs if _ok(r)]
    top_by_return = tuple(sorted(pool, key=lambda r: r["total_return_pct"], reverse=True)[:10])
    stable_pool = [r for r in pool if r["total_return_pct"] >= 0 and (r["profit_factor"] or 0) >= 1.05]
    top_by_stability = tuple(sorted(stable_pool, key=lambda r: r["max_drawdown_pct"] if r["max_drawdown_pct"] is not None else 1e9)[:10])
    top_by_walk_forward = tuple(sorted(pool, key=lambda r: ((r["walk_forward_score"] or 0), r["total_return_pct"]), reverse=True)[:10])
    low_risk_pool = [r for r in pool if r["trade_count"] >= 100 and r["total_return_pct"] >= 0
                     and (r["max_drawdown_pct"] or 99) <= 15]
    top_low_live_risk = tuple(sorted(low_risk_pool, key=lambda r: ((r["max_drawdown_pct"] or 99), -(r["total_return_pct"])))[:10])

    best = max(pool, key=lambda r: (rb._TIER_RANK.get(r["verdict"], 0), r["total_return_pct"]))
    final_verdict = best["verdict"]
    paper_ok = rb._TIER_RANK.get(final_verdict, 0) >= rb._TIER_RANK[rb.PAPER_REHEARSAL_CANDIDATE]
    exe_rec = _EXE_REC.get(final_verdict, _EXE_REC[rb.STILL_NOT_RECOMMENDED])

    # 결론.
    best_strat = max(strategy_only, key=lambda r: (r["total_return_pct"] or -1e9))
    conclusions = [
        f"매매기법 only 최고: {best_strat['_label']} {best_strat['total_return_pct']}% "
        f"(PF {best_strat['profit_factor']}) — 전략 자체가 죽지는 않았으나 단타·비용 한계.",
        f"Agent OFF {agent_off['total_return_pct']}% — "
        + ("Agent 제거가 더 나쁨 → veto/sizing 역할로 유지 타당."
           if (agent_off["total_return_pct"] or 0) < (base["total_return_pct"] or 0)
           else "Agent 진입권 회수가 유리."),
        f"최고 조합: {best['_label']} {best['total_return_pct']}% PF {best['profit_factor']} "
        f"MDD {best['max_drawdown_pct']}% → {final_verdict}.",
        "비용·5슬롯 선착순·종목선별·손실장(3월)·청산이 복합 손실 원인. 단일 레버로는 부족.",
    ]

    return DecompositionReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M", baseline=base,
        strategy_only=strategy_only, agent_roles=agent_roles,
        agent_off_comparison=agent_off_comparison, signal_selection=signal_selection,
        five_slot_analysis=five_slot_analysis, cost_sensitivity=cost_sensitivity,
        universe_filter=universe_filter, time_hold_exit=time_hold_exit,
        loss_defense=loss_defense, walk_forward_oos=walk_forward_oos,
        integrated_combos=integrated_combos, top_by_return=top_by_return,
        top_by_stability=top_by_stability, top_by_walk_forward=top_by_walk_forward,
        top_low_live_risk=top_low_live_risk, final_verdict=final_verdict,
        paper_rehearsal_candidate=paper_ok, exe_rebuild_recommendation=exe_rec,
        conclusions=tuple(conclusions), grade_map=grade_map)


def _slim_buckets(bars, signals, cfgfn, runfn, todict) -> dict[str, Any]:
    r = todict(runfn(bars, signals, cfgfn(agent_mode="AGENT_ENTRY_DECIDER"), label="bk"))
    return r.get("by_time_bucket", {})


def baseline_snapshot(r: DecompositionReport) -> dict[str, Any]:
    b = r.baseline
    return {k: b.get(k) for k in (
        "total_return_pct", "profit_factor", "max_drawdown_pct", "win_rate", "expectancy",
        "payoff_ratio", "trade_count", "avg_trades_per_day", "worst_day_pnl",
        "worst_consecutive_losses", "avg_hold_minutes", "monthly_returns")} | {
        "is_live_authorization": False, "do_not_auto_apply": True, "no_profit_guarantee": True,
        "note": "WF-6M baseline 재현(immutable 기준값). 실전 권고 아님."}


def to_dict(r: DecompositionReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source, "baseline": r.baseline,
        "strategy_only": list(r.strategy_only), "agent_roles": list(r.agent_roles),
        "agent_off_comparison": r.agent_off_comparison, "signal_selection": list(r.signal_selection),
        "five_slot_analysis": r.five_slot_analysis, "cost_sensitivity": r.cost_sensitivity,
        "universe_filter": list(r.universe_filter), "time_hold_exit": r.time_hold_exit,
        "loss_defense": list(r.loss_defense), "walk_forward_oos": r.walk_forward_oos,
        "integrated_combos": list(r.integrated_combos), "top_by_return": list(r.top_by_return),
        "top_by_stability": list(r.top_by_stability), "top_by_walk_forward": list(r.top_by_walk_forward),
        "top_low_live_risk": list(r.top_low_live_risk), "final_verdict": r.final_verdict,
        "paper_rehearsal_candidate": r.paper_rehearsal_candidate,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation,
        "conclusions": list(r.conclusions), "grade_map": r.grade_map,
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization, "broker_order_sent": r.broker_order_sent,
        "order_created": r.order_created, "exe_build_executed": r.exe_build_executed,
        "contains_secret": r.contains_secret, "no_profit_guarantee": r.no_profit_guarantee,
        "disclaimer": r.disclaimer,
    }


def render_markdown(r: DecompositionReport) -> str:
    def _t(rows, n=10):
        out = ["| config | verdict | return% | PF | MDD% | trades | WF | conf |",
               "|---|---|---|---|---|---|---|---|"]
        for e in rows[:n]:
            out.append(f"| {e['_label']} | {e.get('verdict','')} | {e['total_return_pct']} | "
                       f"{e['profit_factor']} | {e['max_drawdown_pct']} | {e['trade_count']} | "
                       f"{e['walk_forward_score']} | {'LOW' if e.get('low_confidence') else ''} |")
        return out
    b = r.baseline
    lines = [
        "# WF-6M 매매기법 vs Agent 분해 (Strategy/Agent Decomposition)",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        "",
        f"## 1. Baseline: {b['total_return_pct']}% · PF {b['profit_factor']} · MDD {b['max_drawdown_pct']}% · "
        f"거래 {b['trade_count']} · {b.get('verdict','')}",
        "",
        "## 2. 매매기법 only (Agent OFF, 전략 단독/조합)",
        *_t(list(r.strategy_only), 9),
        "",
        "## 4. Agent 7역할 (Agent OFF 대비)",
        "| role | return% | MDD% | PF | vs_off_return(pp) | vs_off_mdd(pp) |",
        "|---|---|---|---|---|---|",
        *[f"| {x['role']} | {x['return_pct']} | {x['mdd']} | {x['pf']} | {x['vs_off_return_pp']} | {x['vs_off_mdd_pp']} |"
          for x in r.agent_off_comparison["by_role"]],
        "",
        "## 6. 5슬롯 선착순 분석",
        f"- council BUY {r.five_slot_analysis['council_buy_signals']} vs 체결 "
        f"{r.five_slot_analysis['baseline_filled_trades']} (체결률 {r.five_slot_analysis['fill_ratio']})",
        f"- 5후보 초과 시점 {r.five_slot_analysis['timestamps_over_5_candidates']} · 평균 fwd 전체 "
        f"{r.five_slot_analysis['mean_fwd_all']} / 5초과 {r.five_slot_analysis['mean_fwd_over5']}",
        "",
        "## 7. 비용 민감도",
        f"- baseline {r.cost_sensitivity['baseline']['return_pct']}% → 비용0 "
        f"{r.cost_sensitivity['zero_all_cost']['return_pct']}% (왕복 {r.cost_sensitivity['roundtrip_cost_pct']}%)",
        f"- 세금0 {r.cost_sensitivity['no_tax']['return_pct']}% · 슬리피지0 "
        f"{r.cost_sensitivity['slippage_0bps']['return_pct']}%",
        "",
        "## 8. Universe 필터",
        *_t(list(r.universe_filter), 6),
        "",
        "## 9. 청산 실험",
        *_t(list(r.time_hold_exit["exit_experiments"]), 7),
        "",
        "## 10. 손실장 방어",
        *_t(list(r.loss_defense), 5),
        "",
        "## 11. Walk-forward / OOS (best universe)",
        f"- {r.walk_forward_oos['best_universe_label']} · 월별 {r.walk_forward_oos['monthly_returns']}",
        f"- WF {r.walk_forward_oos['walk_forward_score']} · OOS+ {r.walk_forward_oos['oos_positive']} · "
        f"worst month {r.walk_forward_oos['worst_month']}",
        "",
        "## 12. 통합 조합",
        *_t(list(r.integrated_combos), 5),
        "",
        "## 수익률 기준 TOP 10",
        *_t(list(r.top_by_return), 10),
        "",
        "## 안정성 기준 TOP 10",
        *(_t(list(r.top_by_stability), 10) if r.top_by_stability else ["(조건 충족 조합 없음)"]),
        "",
        "## 실전 위험 낮은 후보 TOP 10 (거래≥100·MDD≤15·return≥0)",
        *(_t(list(r.top_low_live_risk), 10) if r.top_low_live_risk else ["(조건 충족 조합 없음)"]),
        "",
        f"## 최종 개선 verdict: **{r.final_verdict}**",
        f"## Paper 리허설 후보: **{r.paper_rehearsal_candidate}**",
        f"## EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"
