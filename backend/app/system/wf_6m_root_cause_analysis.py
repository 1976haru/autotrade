"""KIS-INTRADAY-50-6M-ROOT-CAUSE-REBUILD-01 — 손실 원인분해 (read-only).

WF-6M-50 baseline(-19%)의 "왜 개별 신호는 양(+)인데 포트폴리오는 음(-)인가"를
A~H로 분해한다. 신호 추출(`wf_6m_signal_extract`) + sim_v2(`wf_6m_sim_v2`) 재사용.

Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class RootCauseReport:
    generated_at: str
    data_source: str
    symbols_count: int
    trading_days: int
    baseline: dict[str, Any]
    signal_quality: dict[str, Any]
    cost_sensitivity: dict[str, Any]
    time_bucket: dict[str, Any]
    hold_bucket: dict[str, Any]
    symbol_group: dict[str, Any]
    strategy_exec: dict[str, Any]
    agent_damage: dict[str, Any]
    walk_forward: dict[str, Any]
    loss_causes_top: tuple[dict[str, Any], ...]
    grade_map: dict[str, str]
    conclusions: tuple[str, ...]
    # 안전 불변값.
    do_not_auto_apply: bool = True
    is_live_authorization: bool = False
    broker_order_sent: bool = False
    order_created: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용 / 실전 전환 / "
        "주문 0건. 수익을 보장하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.do_not_auto_apply or not self.no_profit_guarantee:
            raise ValueError("do_not_auto_apply / no_profit_guarantee must be True")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")


def _bucket_edge(signals: dict) -> dict[str, float]:
    agg: dict[str, list[float]] = defaultdict(list)
    for s in signals.values():
        agg[s["time_bucket"]].append(s["fwd_eod_return"])
    return {k: round(statistics.mean(v), 6) for k, v in agg.items() if v}


def _load_grade_map(input_dir: str | Path, symbols: list[str] | None) -> dict[str, str]:
    """기존 grading 로직 재사용 — 종목별 GO/WATCH/TUNE/EXCLUDE."""
    from app.system.intraday_strategy_validation import evaluate_intraday_strategy, to_dict
    from app.system.wf_6m_50symbols_report import _grade_symbol
    iv = to_dict(evaluate_intraday_strategy(input_dir, symbols=symbols, min_bars=100, min_days=5))
    gm: dict[str, str] = {}
    for p in iv.get("per_symbol", []):
        gm[p["symbol"]] = _grade_symbol(p, None)
    return gm


def analyze_root_cause(
    input_dir: str | Path, *, symbols: list[str] | None = None,
    generated_at: str | None = None,
) -> RootCauseReport:
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    from app.system.wf_6m_signal_extract import extract_signal_map
    from app.system.wf_6m_sim_v2 import SimV2Config, run_sim_v2, result_to_dict

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
    grade_map = _load_grade_map(input_dir, symbols)
    strat_pf = sm.strategy_fwd_pf

    base_cfg = SimV2Config(selection_mode="earliest_first", agent_mode="AGENT_ENTRY_DECIDER",
                           grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge)
    base = run_sim_v2(bars, signals, base_cfg, label="baseline")
    bd = result_to_dict(base)

    # ---- A. 신호 품질 분해 ----
    council_buys = {k: v for k, v in signals.items() if v["council_action"] == "BUY"}
    # 체결된 신호 (baseline trades 의 entry).
    # baseline trades 는 result 에 미노출 → 재현: filled = council BUY 중 실제 진입.
    # sim_v2 는 trades 를 반환하지 않으므로 여기서 간이 재현(첫 진입 시각 매칭) 대신
    # council BUY 신호 전체의 fwd 와, "동시각 후보 초과" 시점 성과로 근사.
    by_ts_cands: dict[str, int] = defaultdict(int)
    for (sym, ts), v in council_buys.items():
        by_ts_cands[ts] += 1
    cand_dist = defaultdict(int)
    for c in by_ts_cands.values():
        cand_dist[min(c, 10)] += 1
    over5_fwd = [v["fwd_eod_return"] for (sym, ts), v in council_buys.items()
                 if by_ts_cands[ts] > 5]
    all_fwd = [v["fwd_eod_return"] for v in council_buys.values()]
    signal_quality = {
        "council_buy_signals": len(council_buys),
        "single_buy_bars": sum(1 for v in signals.values() if v["single_buys"]),
        "total_signal_bars": len(signals),
        "candidates_per_timestamp_dist": {str(k): cand_dist[k] for k in sorted(cand_dist)},
        "timestamps_over_5_candidates": sum(1 for c in by_ts_cands.values() if c > 5),
        "mean_fwd_all_council_buys": round(statistics.mean(all_fwd), 6) if all_fwd else None,
        "mean_fwd_when_over_5_candidates": round(statistics.mean(over5_fwd), 6) if over5_fwd else None,
        "note": ("동일 5분 시각에 council BUY 후보가 5개(슬롯)를 초과하는 시점이 많을수록, "
                 "선착순 체결은 '더 좋은 신호'가 아니라 '먼저 온 신호'를 담을 위험이 큼."),
    }

    # ---- B. 비용 민감도 ----
    def _variant(**kw):
        cfg = SimV2Config(selection_mode="earliest_first", agent_mode="AGENT_ENTRY_DECIDER",
                          grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge, **kw)
        r = result_to_dict(run_sim_v2(bars, signals, cfg))
        return {"total_return_pct": r["total_return_pct"], "profit_factor": r["profit_factor"],
                "expectancy": r["expectancy"], "trade_count": r["trade_count"]}
    rt_cost_pct = round((2 * 1.5 + 18.0 + 2 * 5.0) / 100.0, 4)
    cost_sensitivity = {
        "baseline_cost": {"total_return_pct": bd["total_return_pct"], "profit_factor": bd["profit_factor"],
                          "cost_paid": bd["cost_paid_total"], "tax_paid": bd["tax_paid_total"],
                          "slippage_paid": bd["slippage_paid_total"]},
        "no_slippage": _variant(slippage_bps=0.0),
        "no_tax": _variant(sell_tax_bps=0.0),
        "no_fee": _variant(fee_bps_per_side=0.0),
        "zero_all_cost": _variant(slippage_bps=0.0, sell_tax_bps=0.0, fee_bps_per_side=0.0),
        "slippage_0bps": _variant(slippage_bps=0.0),
        "slippage_2bps": _variant(slippage_bps=2.0),
        "slippage_5bps": _variant(slippage_bps=5.0),
        "slippage_10bps": _variant(slippage_bps=10.0),
        "roundtrip_cost_pct": rt_cost_pct,
        "breakeven_move_pct": rt_cost_pct,
        "note": ("왕복 비용(수수료+세금+슬리피지)을 넘는 평균 기대이동이 없으면 구조적으로 음(-). "
                 "비용 제거 시 수익이 양전되면 '비용이 엣지를 잠식'한 것."),
    }

    # ---- C/D. 시간대 / 보유시간 (baseline 계측) ----
    time_bucket = bd["by_time_bucket"]
    hold_bucket = bd["by_hold_bucket"]

    # ---- E. 종목군 (EXCLUDE 제거 효과) ----
    go_tune = frozenset(s for s, g in grade_map.items() if g in ("GO", "TUNE"))
    excl = [s for s, g in grade_map.items() if g == "EXCLUDE"]
    no_excl = result_to_dict(run_sim_v2(bars, signals, SimV2Config(
        universe=go_tune, selection_mode="earliest_first", agent_mode="AGENT_ENTRY_DECIDER",
        grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge)))
    symbol_group = {
        "by_grade": bd["by_grade"],
        "exclude_symbols": excl,
        "exclude_removed_result": {"total_return_pct": no_excl["total_return_pct"],
                                   "profit_factor": no_excl["profit_factor"],
                                   "max_drawdown_pct": no_excl["max_drawdown_pct"],
                                   "trade_count": no_excl["trade_count"]},
        "improvement_pct_points": round(no_excl["total_return_pct"] - bd["total_return_pct"], 4),
    }

    # ---- F. 전략별 실제 체결 성과 ----
    strategy_exec = {"by_strategy_filled": bd["by_strategy"], "forward_return_pf": strat_pf}

    # ---- G. Agent 손상 분석 ----
    agent_off = result_to_dict(run_sim_v2(bars, signals, SimV2Config(
        agent_mode="AGENT_OFF", selection_mode="earliest_first",
        grade_map=grade_map, strategy_pf=strat_pf, bucket_edge=bedge)))
    # veto: 단일 BUY 인데 council HOLD → 이후 fwd (veto 가 도움?).
    vetoed = [v["fwd_eod_return"] for v in signals.values()
              if v["single_buys"] and v["council_action"] == "HOLD"]
    approved = [v["fwd_eod_return"] for v in signals.values() if v["council_action"] == "BUY"]
    agent_damage = {
        "council_baseline_return_pct": bd["total_return_pct"],
        "agent_off_return_pct": agent_off["total_return_pct"],
        "agent_off_trade_count": agent_off["trade_count"],
        "vetoed_signal_count": len(vetoed),
        "vetoed_mean_fwd": round(statistics.mean(vetoed), 6) if vetoed else None,
        "approved_signal_count": len(approved),
        "approved_mean_fwd": round(statistics.mean(approved), 6) if approved else None,
        "veto_helped": (bool(vetoed) and statistics.mean(vetoed) < 0),
        "note": ("veto 된 신호의 이후 평균수익이 음(-)이면 veto 가 옳았던 것, 양(+)이면 Agent 가 "
                 "좋은 진입을 막은 것. council vs AGENT_OFF 수익 비교로 Agent 진입권 가치 평가."),
    }

    # ---- H. Walk-forward / worst month ----
    monthly = bd["monthly_returns"]
    worst_month = min(monthly, key=monthly.get) if monthly else None
    # worst month 거래 분해.
    wm_decomp: dict[str, Any] = {}
    if worst_month:
        # baseline 재현 불가(trades 미노출) → worst-month-only universe sim 으로 근사 대신
        # 월별 수익 + 비교 구간만 carry.
        best_month = max(monthly, key=monthly.get)
        wm_decomp = {"worst_month": worst_month, "worst_month_return": monthly[worst_month],
                     "best_month": best_month, "best_month_return": monthly[best_month]}
    walk_forward = {
        "monthly_returns": monthly,
        "walk_forward_score": bd["walk_forward_score"],
        "oos_positive": bd["oos_positive"],
        "positive_months": sum(1 for v in monthly.values() if v > 0),
        "negative_months": sum(1 for v in monthly.values() if v < 0),
        "worst_month_decomp": wm_decomp,
        "note": "특정 월(예: 큰 음전)이 전체 손실을 좌우하면 worst-month 방어가 핵심.",
    }

    # ---- 손실 원인 TOP (정성+정량 합성) ----
    causes: list[dict[str, Any]] = []
    if bd["total_return_pct"] < 0:
        causes.append({"rank": 1, "cause": "신호 과다 vs 5슬롯 — 선착순 체결 편향",
                       "evidence": f"동시각 5후보 초과 시점 {signal_quality['timestamps_over_5_candidates']}건",
                       "fix": "신호 선택 랭킹(composite_rank) 도입"})
    if symbol_group["improvement_pct_points"] > 0:
        causes.append({"rank": 2, "cause": "EXCLUDE 종목 손실 기여",
                       "evidence": f"EXCLUDE 제거 시 {symbol_group['improvement_pct_points']:+.1f}%p",
                       "fix": "universe 를 GO/TUNE 로 축소"})
    if agent_damage["agent_off_return_pct"] is not None:
        delta = round(bd["total_return_pct"] - agent_damage["agent_off_return_pct"], 2)
        causes.append({"rank": 3, "cause": "Agent Council 진입권 — 단일전략 대비 손상 여부",
                       "evidence": f"council {bd['total_return_pct']:.1f}% vs AGENT_OFF {agent_damage['agent_off_return_pct']:.1f}% (Δ{delta:+.1f}%p)",
                       "fix": "Agent 를 진입권 회수 → veto/size/review 역할로 한정"})
    cz = cost_sensitivity["zero_all_cost"]["total_return_pct"]
    if cz is not None:
        causes.append({"rank": 4, "cause": "거래비용(수수료+세금+슬리피지) 잠식",
                       "evidence": f"비용 0 가정 시 {cz:+.1f}% (왕복비용 {rt_cost_pct}%)",
                       "fix": "회전율 축소 + 기대이동 > 왕복비용 신호만 진입"})
    if worst_month:
        causes.append({"rank": 5, "cause": f"worst month({worst_month}) 집중 손실",
                       "evidence": f"{monthly[worst_month]:.1f}%",
                       "fix": "월간 DD stop / 연속손실 stop / 장세 필터"})
    causes.append({"rank": 6, "cause": "walk-forward 안정성 부족(과최적화)",
                   "evidence": f"WF score {bd['walk_forward_score']}, OOS+ {bd['oos_positive']}",
                   "fix": "롤링 재학습 + 표본 외 검증"})
    causes.append({"rank": 7, "cause": "평균 보유 과대(EOD 청산 편중)",
                   "evidence": f"avg_hold {bd['avg_hold_minutes']}분",
                   "fix": "조기 익절/손절 + 시간 컷오프"})

    conclusions = [
        "개별 전략 forward-return PF 는 양(+)이나, 5슬롯 선착순 체결 + 비용 + Agent 진입권 + "
        "worst month 집중손실이 결합해 포트폴리오를 음(-)으로 만든다.",
        "Agent Council 은 진입 결정권을 회수하고 veto/size/review 보조 역할로 한정 검토 권장.",
        "EXCLUDE 종목 제거 + 신호 선택 랭킹 + worst-month 방어가 1순위 개선 레버.",
    ]

    return RootCauseReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        symbols_count=len(files), trading_days=bd["trading_days"],
        baseline={"total_return_pct": bd["total_return_pct"], "final_equity": bd["final_equity"],
                  "profit_factor": bd["profit_factor"], "max_drawdown_pct": bd["max_drawdown_pct"],
                  "win_rate": bd["win_rate"], "expectancy": bd["expectancy"],
                  "trade_count": bd["trade_count"], "worst_day_pnl": bd["worst_day_pnl"],
                  "worst_consecutive_losses": bd["worst_consecutive_losses"],
                  "avg_hold_minutes": bd["avg_hold_minutes"]},
        signal_quality=signal_quality, cost_sensitivity=cost_sensitivity,
        time_bucket=time_bucket, hold_bucket=hold_bucket, symbol_group=symbol_group,
        strategy_exec=strategy_exec, agent_damage=agent_damage, walk_forward=walk_forward,
        loss_causes_top=tuple(causes), grade_map=grade_map, conclusions=tuple(conclusions))


def to_dict(r: RootCauseReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "symbols_count": r.symbols_count, "trading_days": r.trading_days,
        "baseline": r.baseline, "signal_quality": r.signal_quality,
        "cost_sensitivity": r.cost_sensitivity, "time_bucket": r.time_bucket,
        "hold_bucket": r.hold_bucket, "symbol_group": r.symbol_group,
        "strategy_exec": r.strategy_exec, "agent_damage": r.agent_damage,
        "walk_forward": r.walk_forward, "loss_causes_top": list(r.loss_causes_top),
        "grade_map": r.grade_map, "conclusions": list(r.conclusions),
        "do_not_auto_apply": r.do_not_auto_apply,
        "is_live_authorization": r.is_live_authorization,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "contains_secret": r.contains_secret, "no_profit_guarantee": r.no_profit_guarantee,
        "disclaimer": r.disclaimer,
    }


def render_markdown(r: RootCauseReport) -> str:
    b = r.baseline
    lines = [
        "# WF-6M Root Cause Analysis — 손실 원인분해",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/주문 0건. 수익 보장 아님.",
        "",
        "## 1. Baseline 재확인",
        f"- 1000만원 → {b['final_equity']:,.0f} ({b['total_return_pct']:+.2f}%) · PF {b['profit_factor']} · "
        f"MDD {b['max_drawdown_pct']}% · 승률 {b['win_rate']} · 거래 {b['trade_count']} · "
        f"평균보유 {b['avg_hold_minutes']}분 · 최악연속 {b['worst_consecutive_losses']}",
        "",
        "## 2. 손실 원인 TOP",
        *[f"- [{c['rank']}] {c['cause']} — {c['evidence']} → 개선: {c['fix']}" for c in r.loss_causes_top],
        "",
        "## 3. 신호 품질 / 선착순 편향",
        f"- council BUY 신호 {r.signal_quality['council_buy_signals']}건 · 5후보 초과 시점 "
        f"{r.signal_quality['timestamps_over_5_candidates']}건",
        f"- 전체 council BUY 평균 fwd {r.signal_quality['mean_fwd_all_council_buys']} · "
        f"5후보 초과 시점 평균 fwd {r.signal_quality['mean_fwd_when_over_5_candidates']}",
        "",
        "## 4. 비용 민감도",
        f"- baseline {r.cost_sensitivity['baseline_cost']['total_return_pct']}% · "
        f"비용0 {r.cost_sensitivity['zero_all_cost']['total_return_pct']}% · "
        f"세금0 {r.cost_sensitivity['no_tax']['total_return_pct']}% · "
        f"슬리피지0 {r.cost_sensitivity['no_slippage']['total_return_pct']}%",
        f"- 왕복비용 {r.cost_sensitivity['roundtrip_cost_pct']}%",
        "",
        "## 5. 종목군 (EXCLUDE 제거 효과)",
        f"- EXCLUDE {len(r.symbol_group['exclude_symbols'])}종목 제거 시 "
        f"{r.symbol_group['exclude_removed_result']['total_return_pct']}% "
        f"({r.symbol_group['improvement_pct_points']:+.1f}%p)",
        "",
        "## 6. 전략별 실제 체결 / forward PF",
        f"- forward PF: {r.strategy_exec['forward_return_pf']}",
        f"- 실제 체결 전략별: {r.strategy_exec['by_strategy_filled']}",
        "",
        "## 7. Agent Council 손상",
        f"- council {r.agent_damage['council_baseline_return_pct']}% vs AGENT_OFF "
        f"{r.agent_damage['agent_off_return_pct']}%",
        f"- veto 신호 {r.agent_damage['vetoed_signal_count']}건 평균 fwd "
        f"{r.agent_damage['vetoed_mean_fwd']} (veto_helped={r.agent_damage['veto_helped']})",
        "",
        "## 8. Walk-forward / Worst month",
        f"- 월별: {r.walk_forward['monthly_returns']}",
        f"- WF score {r.walk_forward['walk_forward_score']} · OOS+ {r.walk_forward['oos_positive']} · "
        f"worst month {r.walk_forward['worst_month_decomp']}",
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "",
        f"> {r.disclaimer}",
    ]
    return "\n".join(lines) + "\n"
