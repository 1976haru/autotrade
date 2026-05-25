"""KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — 재설계 실험 + 개선 verdict (read-only).

원인분해(`wf_6m_root_cause_analysis`)에서 도출된 레버를 실험한다: universe 필터 / 신호 선택
랭킹 / 시간 컷오프·강제청산 / Agent 역할 / 장세 필터 / worst-month 방어. 각 조합을
sim_v2 로 평가하고 개선 verdict 를 산출한다.

Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
**어떤 조합도 자동 적용/실전 전환하지 않는다.**
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BLOCKED = "BLOCKED"
STILL_NOT_RECOMMENDED = "STILL_NOT_RECOMMENDED"
WATCHLIST_ONLY = "WATCHLIST_ONLY"
PAPER_REHEARSAL_CANDIDATE = "PAPER_REHEARSAL_CANDIDATE"
RESEARCH_PROMISING = "RESEARCH_PROMISING"

_TIER_RANK = {BLOCKED: 0, STILL_NOT_RECOMMENDED: 1, WATCHLIST_ONLY: 2,
              PAPER_REHEARSAL_CANDIDATE: 3, RESEARCH_PROMISING: 4}


def verdict_for(r: dict[str, Any], *, worst_month_defended: bool = False,
                agent_hurt_reduced: bool = False) -> str:
    ret = r.get("total_return_pct")
    pf = r.get("profit_factor")
    mdd = r.get("max_drawdown_pct")
    wcl = r.get("worst_consecutive_losses", 99)
    trades = r.get("trade_count", 0)
    oos = r.get("oos_positive", False)
    if ret is None or pf is None or mdd is None:
        return BLOCKED
    if pf < 1.05 or mdd > 20 or ret < 0:
        return STILL_NOT_RECOMMENDED
    # WATCHLIST 최소 충족.
    tier = WATCHLIST_ONLY
    if (pf >= 1.15 and mdd <= 15 and ret >= 5 and oos and wcl <= 12 and trades >= 100):
        tier = PAPER_REHEARSAL_CANDIDATE
    if (pf >= 1.25 and mdd <= 12 and ret >= 8 and oos and worst_month_defended
            and agent_hurt_reduced and trades >= 100):
        tier = RESEARCH_PROMISING
    return tier


@dataclass(frozen=True)
class RebuildReport:
    generated_at: str
    data_source: str
    baseline: dict[str, Any]
    experiments: tuple[dict[str, Any], ...]
    most_improved_top10: tuple[dict[str, Any], ...]
    most_stable_top10: tuple[dict[str, Any], ...]
    best_config: dict[str, Any]
    final_verdict: str
    unresolved_issues: tuple[str, ...]
    next_steps: tuple[str, ...]
    grade_map: dict[str, str]
    # 안전 불변값.
    do_not_auto_apply: bool = True
    auto_apply_allowed: bool = False
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 어떤 조합도 자동 적용/실전 "
        "전환되지 않습니다. 'Paper rehearsal candidate'는 *조건부 검토 후보*일 뿐 실전 가능이 "
        "아니며, 수익을 보장하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed:
            raise ValueError("unsafe apply invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in _TIER_RANK:
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def _bucket_edge(signals: dict) -> dict[str, float]:
    agg: dict[str, list[float]] = defaultdict(list)
    for s in signals.values():
        agg[s["time_bucket"]].append(s["fwd_eod_return"])
    return {k: round(statistics.mean(v), 6) for k, v in agg.items() if v}


def run_rebuild_experiments(
    input_dir: str | Path, *, symbols: list[str] | None = None,
    grade_map: dict[str, str] | None = None, generated_at: str | None = None,
) -> RebuildReport:
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    from app.system.wf_6m_signal_extract import extract_signal_map
    from app.system.wf_6m_sim_v2 import (
        AGENT_MODES,
        SELECTION_MODES,
        SimV2Config,
        result_to_dict,
        run_sim_v2,
    )

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
    top10 = frozenset(ranked[:10])
    top20 = frozenset(ranked[:20])

    def _cfg(**kw) -> SimV2Config:
        base = dict(grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge)
        base.update(kw)
        return SimV2Config(**base)

    def _run(label: str, **kw) -> dict[str, Any]:
        r = result_to_dict(run_sim_v2(bars, signals, _cfg(**kw), label=label))
        r["_label"] = label
        # worst-month 방어/agent hurt 판단용 부가.
        mr = r.get("monthly_returns", {})
        r["_min_month"] = min(mr.values()) if mr else None
        return r

    experiments: list[dict[str, Any]] = []
    # baseline.
    base = _run("BASELINE_earliest_council_ALL", selection_mode="earliest_first",
                agent_mode="AGENT_ENTRY_DECIDER")
    base_min_month = base["_min_month"]
    experiments.append(base)

    # A. Universe filter.
    experiments.append(_run("UNI_exclude_removed", universe=go_tune))
    experiments.append(_run("UNI_go_only", universe=go_only))
    experiments.append(_run("UNI_go_tune_top10", universe=top10))
    experiments.append(_run("UNI_go_tune_top20", universe=top20))

    # B. Signal selection (universe=GO+TUNE 로 선택 효과 분리).
    for mode in SELECTION_MODES:
        experiments.append(_run(f"SEL_{mode}", universe=go_tune, selection_mode=mode))

    # C. Time-of-day.
    experiments.append(_run("TIME_cutoff_1300", universe=go_tune, selection_mode="composite_rank",
                            entry_cutoff_min=13 * 60))
    experiments.append(_run("TIME_cutoff_1400", universe=go_tune, selection_mode="composite_rank",
                            entry_cutoff_min=14 * 60))
    experiments.append(_run("TIME_forced_close_1450", universe=go_tune,
                            selection_mode="composite_rank", forced_close_min=14 * 60 + 50))

    # F. Agent role.
    for mode in AGENT_MODES:
        experiments.append(_run(f"AGENT_{mode}", universe=go_tune, selection_mode="composite_rank",
                                agent_mode=mode))

    # G. Regime filter.
    experiments.append(_run("REGIME_block_down_day", universe=go_tune,
                            selection_mode="composite_rank", regime_block_down_day=True))

    # H. Worst-month defense.
    experiments.append(_run("DEF_monthly_dd_stop_5", universe=go_tune,
                            selection_mode="composite_rank", monthly_dd_stop_pct=5.0))
    experiments.append(_run("DEF_consec_loss_5", universe=go_tune,
                            selection_mode="composite_rank", consecutive_loss_stop=5))
    # BEST_COMBO — 모든 레버 결합.
    best_combo = _run("BEST_COMBO", universe=go_tune, selection_mode="composite_rank",
                      agent_mode="AGENT_VETO_ONLY", entry_cutoff_min=14 * 60,
                      forced_close_min=14 * 60 + 50, monthly_dd_stop_pct=5.0,
                      consecutive_loss_stop=5, regime_block_down_day=True)
    experiments.append(best_combo)

    # verdict 부여.
    for e in experiments:
        wm_def = (e["_min_month"] is not None and base_min_month is not None
                  and e["_min_month"] > base_min_month)
        # agent hurt 감소: AGENT_OFF/VETO/RISK_FILTER 계열은 진입권 회수 → hurt 감소로 간주.
        agent_reduced = "AGENT_AGENT_ENTRY_DECIDER" not in e["_label"] and "council" not in e["_label"]
        e["verdict"] = verdict_for(e, worst_month_defended=wm_def, agent_hurt_reduced=agent_reduced)
        e["worst_month_defended"] = wm_def
        if e["low_confidence"]:
            e["confidence_flag"] = "LOW_CONFIDENCE"

    # 정렬: most improved (return), most stable (MDD 낮고 return>=0, PF>=1.05).
    def _slim(e):
        return {k: e.get(k) for k in (
            "_label", "verdict", "total_return_pct", "profit_factor", "max_drawdown_pct",
            "expectancy", "win_rate", "trade_count", "worst_consecutive_losses",
            "avg_hold_minutes", "walk_forward_score", "oos_positive", "worst_month_defended",
            "low_confidence", "turnover")}
    improved = sorted(experiments, key=lambda e: (e["total_return_pct"] or -1e9), reverse=True)
    stable_pool = [e for e in experiments
                   if (e["total_return_pct"] or -1) >= 0 and (e["profit_factor"] or 0) >= 1.05]
    stable = sorted(stable_pool, key=lambda e: (e["max_drawdown_pct"] if e["max_drawdown_pct"] is not None else 1e9))

    # best config = 최고 tier, 동률 시 return.
    best = max(experiments, key=lambda e: (_TIER_RANK[e["verdict"]],
                                           e["total_return_pct"] or -1e9))
    final_verdict = best["verdict"]

    unresolved = []
    if final_verdict in (BLOCKED, STILL_NOT_RECOMMENDED):
        unresolved.append("모든 실험 조합이 비용 후 양(+)·PF≥1.05·MDD≤20% 동시 충족에 실패 — 구조적 엣지 부족.")
    if best.get("walk_forward_score") is not None and not best.get("oos_positive"):
        unresolved.append("최고 조합도 OOS(표본 외) 양(+) 미확인 — 과최적화 위험 잔존.")
    if best.get("low_confidence"):
        unresolved.append("최고 조합 거래수 < 100 — LOW_CONFIDENCE, 과대평가 금지.")
    if not unresolved:
        unresolved.append("개선은 있으나 실전 전환 전 Paper 모의 100건·28거래일 + 운영자 승인 필요.")

    next_steps = [
        f"최고 조합({best['_label']}) 을 *별도 PR* 로 재현·재백테스트(자동 적용 금지).",
        "EXCLUDE 종목 제거 + 신호 선택 랭킹 + Agent 진입권 회수 조합을 우선 검토.",
        "worst-month 방어(월간 DD/연속손실 stop) 효과를 더 긴 기간으로 재확인.",
        "조건 충족 시에만 Paper 모의 리허설 → 운영자 승인 → 실전 검토(현재 단계 아님).",
    ]

    return RebuildReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        baseline=_slim(base), experiments=tuple(_slim(e) for e in experiments),
        most_improved_top10=tuple(_slim(e) for e in improved[:10]),
        most_stable_top10=tuple(_slim(e) for e in stable[:10]),
        best_config=_slim(best), final_verdict=final_verdict,
        unresolved_issues=tuple(unresolved), next_steps=tuple(next_steps), grade_map=grade_map)


def to_dict(r: RebuildReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source, "baseline": r.baseline,
        "experiments": list(r.experiments), "most_improved_top10": list(r.most_improved_top10),
        "most_stable_top10": list(r.most_stable_top10), "best_config": r.best_config,
        "final_verdict": r.final_verdict, "unresolved_issues": list(r.unresolved_issues),
        "next_steps": list(r.next_steps), "grade_map": r.grade_map,
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization, "broker_order_sent": r.broker_order_sent,
        "order_created": r.order_created, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "disclaimer": r.disclaimer,
    }


def render_markdown(r: RebuildReport) -> str:
    def _row(e):
        return (f"| {e['_label']} | {e['verdict']} | {e['total_return_pct']}% | "
                f"{e['profit_factor']} | {e['max_drawdown_pct']}% | {e['trade_count']} | "
                f"{e['worst_consecutive_losses']} | {e['walk_forward_score']} | "
                f"{'LOW' if e['low_confidence'] else ''} |")
    lines = [
        "# WF-6M Rebuild Experiments — 재설계 실험",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 어떤 조합도 자동 적용/실전 전환되지 않습니다. 수익 보장 아님.",
        "",
        f"## Baseline: {r.baseline['total_return_pct']}% · PF {r.baseline['profit_factor']} · "
        f"MDD {r.baseline['max_drawdown_pct']}% · {r.baseline['verdict']}",
        "",
        f"## 최종 개선 verdict: **{r.final_verdict}**",
        f"- best config: **{r.best_config['_label']}** "
        f"({r.best_config['total_return_pct']}% · PF {r.best_config['profit_factor']} · "
        f"MDD {r.best_config['max_drawdown_pct']}% · 거래 {r.best_config['trade_count']})",
        "",
        "## 가장 개선된 조합 TOP10",
        "| config | verdict | return | PF | MDD | trades | 연속손실 | WF | conf |",
        "|---|---|---|---|---|---|---|---|---|",
        *[_row(e) for e in r.most_improved_top10],
        "",
        "## 가장 안정적인 조합 TOP10 (return≥0, PF≥1.05, MDD 오름차순)",
        "| config | verdict | return | PF | MDD | trades | 연속손실 | WF | conf |",
        "|---|---|---|---|---|---|---|---|---|",
        *([_row(e) for e in r.most_stable_top10] or ["| (없음) | | | | | | | | |"]),
        "",
        "## 아직 해결 안 된 문제",
        *[f"- {x}" for x in r.unresolved_issues],
        "",
        "## 다음 단계",
        *[f"- {x}" for x in r.next_steps],
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"
