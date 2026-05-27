"""최종 다전략 + Agent Council 백테스트 (CHECKLIST-04 capstone, 백테스트 전용).

ORB / Momentum / Gap / VWAP 4전략 단독 + Agent Council 통합을 실제 60일 1분봉 aligned
데이터로 *정직하게* 비교한다. 기존 전략 evaluator(`evaluate_orb/momentum/gap/vwap`)와
`run_agent_council` 을 *그대로 재사용* — 새 전략 로직/파라미터 최적화 0건. 체결은
1분봉 intrabar replay(`simulate_intrabar_execution`), 비용은 cost_model 고정값.
RISK_FILTER_ONLY 는 *비교 옵션* 으로만 적용(검증된 후보) — 자동 적용/런타임 반영 0건.

broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import 0건, 실주문
0건, read-only. auto_apply_allowed=False / applied_to_runtime=False /
is_live_authorization=False / no_profit_guarantee=True.
"""

from __future__ import annotations

import math
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
from app.backtest.cost_model import DEFAULT_COST, STRESS_SLIPPAGES_BPS, cost_drag_bps
from app.backtest.intrabar_execution import CostModel, simulate_intrabar_execution
from app.backtest.strategy_council_backtest import (
    _build_input,
    _group_by_day,
    load_ohlcv_from_csv,
)
from app.system.intrabar_realdata_backtest import (
    FIVE_MIN_DIR_DEFAULT,
    ONE_MIN_DIR_DEFAULT,
    _SYMBOL_GROUP,
    _load_csv,
    _scan,
)

_STRATS = {"ORB": evaluate_orb, "MOMENTUM": evaluate_momentum,
           "GAP": evaluate_gap, "VWAP": evaluate_vwap}
# 모든 전략 공통 *고정* 체결 가정 (최적화 아님 — 비교 공정성용).
_STOP_PCT = 0.01
_TARGET_PCT = 0.015
_MAX_HOLD = 30
_SLOTS = 5
_COST = CostModel(slippage_bps=DEFAULT_COST.slippage_bps)
_ROUND_TRIP_BPS = cost_drag_bps()


def _1m_by_day(bars: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for b in bars:
        ts = b.get("ts")
        if ts is None:
            continue
        days.setdefault(ts.date().isoformat(), []).append(b)
    return days


def _pf(pnls: Sequence[float]) -> float | None:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl > 0:
        return round(gp / gl, 3)
    return None if gp == 0 else math.inf


def _metrics(trades: list[dict], key: str = "net") -> dict[str, Any]:
    """per-trade dict 리스트 → 성과지표. key=net(비용후)/gross(비용전)."""
    n = len(trades)
    if n == 0:
        return {"trade_count": 0, "profit_factor": None, "return_pct": None,
                "mdd_pct": None, "win_rate": None, "expectancy_bps": None,
                "payoff_ratio": None, "avg_hold_minutes": None, "worst_day_pct": None,
                "worst_consecutive_losses": 0}
    pnls = [t[key] for t in trades]
    ents = [t["entry"] for t in trades]
    avg_entry = sum(ents) / len(ents) if ents else 1.0
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    pf = _pf(pnls)
    cum = peak = mdd = 0.0
    consec = worst_consec = 0
    by_day: dict[str, float] = {}
    for t in trades:
        p = t[key]
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
        consec = consec + 1 if p < 0 else 0
        worst_consec = max(worst_consec, consec)
        by_day[t["day"]] = by_day.get(t["day"], 0.0) + p
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss = (abs(sum(losses) / len(losses))) if losses else 0.0
    worst_day = min(by_day.values()) if by_day else 0.0
    return {
        "trade_count": n,
        "profit_factor": (pf if pf != math.inf else None),
        "return_pct": round(sum(pnls) / avg_entry / n * 100.0, 4) if avg_entry else None,
        "mdd_pct": round(mdd / avg_entry * 100.0, 4) if avg_entry else None,
        "win_rate": round(len(wins) / n, 4),
        "expectancy_bps": round(sum(pnls) / n / avg_entry * 1e4, 2) if avg_entry else None,
        "payoff_ratio": round(avg_win / avg_loss, 3) if avg_loss > 0 else None,
        "avg_hold_minutes": round(sum(t.get("hold", 0.0) for t in trades) / n, 1),
        "worst_day_pct": round(worst_day / avg_entry * 100.0, 4) if avg_entry else None,
        "worst_consecutive_losses": worst_consec,
        "symbols": sorted({t["symbol"] for t in trades}),
    }


def _slippage_stress(trades: list[dict]) -> dict[str, float | None]:
    """슬리피지 5/7/10/15bps 별 PF (net = net5 + slip5*(1 - S/5))."""
    out = {}
    for s in STRESS_SLIPPAGES_BPS:
        adj = [t["net"] + t["slip5"] * (1 - s / 5.0) for t in trades]
        out[f"{s}bps"] = _pf(adj)
    return out


def _kept_by_risk_filter(t: dict) -> bool:
    return t["net_edge_bps"] > 0 and not t["low_liq"]


def _exit_trade(side, entry, stop, target, entry_time, day_bars_5m, day_bars_1m, *,
                use_1m: bool):
    # entry_time 은 *신호 발생 bar* 의 timestamp — replay 윈도우 [entry_time, +max_hold].
    return simulate_intrabar_execution(
        side=side, entry_time=entry_time,
        entry_price=entry, stop_price=stop, target_price=target,
        max_hold_minutes=_MAX_HOLD, bars_5m=day_bars_5m,
        bars_1m=(day_bars_1m if use_1m else None), cost=_COST)


def _collect(one_dir: Path, five_dir: Path, symbols: Sequence[str] | None):
    """전략별 + council 거래 수집 (1분봉 aligned). 기존 evaluator/council 재사용."""
    five_map = _scan(five_dir)
    one_map = _scan(one_dir)
    syms = list(symbols) if symbols else sorted(set(five_map) & set(one_map))
    strat_trades: dict[str, list[dict]] = {k: [] for k in _STRATS}
    council_trades: list[dict] = []
    blocks = {"risk_veto": 0, "quality_gate": 0, "no_exit_plan": 0, "council_buy": 0}
    agent = {"helped": 0, "hurt": 0}
    vote_dist: dict[str, int] = {"BUY": 0, "SELL": 0, "HOLD": 0}
    excluded: list[str] = []

    for sym in syms:
        if sym not in five_map or sym not in one_map:
            excluded.append(sym)
            continue
        bars5 = load_ohlcv_from_csv(str(five_map[sym]), default_symbol=sym)
        days5 = _group_by_day(bars5)
        days1 = _1m_by_day(_load_csv(one_map[sym]))
        group = _SYMBOL_GROUP.get(sym, "OTHER")
        prev_close = None
        for day_bars in days5:
            day = day_bars[0].timestamp.date().isoformat()
            d1 = days1.get(day)
            if not d1:
                prev_close = day_bars[-1].close
                continue
            done_strat: set[str] = set()
            council_done = False
            avg_vol = (sum(b.volume for b in day_bars) / len(day_bars)) or 1.0
            for i in range(len(day_bars)):
                mi = _build_input(day_bars, i, opening_range_bars=6,
                                  recent_closes_window=5, prev_close=prev_close)
                b = day_bars[i]
                ve = (b.volume / avg_vol) if avg_vol else 1.0
                low_liq = ve < 0.8
                # 4 전략 단독.
                for name, ev in _STRATS.items():
                    if name in done_strat:
                        continue
                    if ev(mi).signal == CouncilAction.BUY:
                        rec = _make_trade(name, sym, day, b, ve, low_liq, group,
                                          day_bars, d1, exit_plan=None)
                        if rec:
                            strat_trades[name].append(rec)
                            done_strat.add(name)
                # council.
                council = run_agent_council(mi)
                pre = (council.pre_exit_plan_action or "").upper()
                vote_dist[council.final_action.value] = vote_dist.get(
                    council.final_action.value, 0) + 1
                if not council_done:
                    if council.final_action == CouncilAction.BUY:
                        blocks["council_buy"] += 1
                        rec = _make_trade("COUNCIL", sym, day, b, ve, low_liq, group,
                                          day_bars, d1, exit_plan=council.exit_plan)
                        if rec:
                            council_trades.append(rec)
                            council_done = True
                    else:
                        # would-be BUY 가 게이트로 차단됐는지 카운트.
                        if pre == "BUY":
                            if not council.has_exit_plan:
                                blocks["no_exit_plan"] += 1
                            if council.risk_veto_result.get("veto") or council.risk_veto_result.get("blocked"):
                                blocks["risk_veto"] += 1
                            if int(council.quality_score or 0) < 60:
                                blocks["quality_gate"] += 1
            prev_close = day_bars[-1].close

    # agent helped/hurt: 같은 (symbol,day) 에서 council 거래 net 이 단독 평균보다 나은지.
    council_by_key = {(t["symbol"], t["day"]): t["net"] for t in council_trades}
    for (s, d), cnet in council_by_key.items():
        singles = [t["net"] for name in _STRATS for t in strat_trades[name]
                   if t["symbol"] == s and t["day"] == d]
        if not singles:
            continue
        avg_single = sum(singles) / len(singles)
        if cnet > avg_single:
            agent["helped"] += 1
        elif cnet < avg_single:
            agent["hurt"] += 1

    return strat_trades, council_trades, blocks, agent, vote_dist, excluded, syms


def _make_trade(name, sym, day, bar, ve, low_liq, group, day_bars5, day_bars1,
                *, exit_plan) -> dict | None:
    entry = bar.close
    if entry <= 0:
        return None
    sp, tp = _STOP_PCT, _TARGET_PCT
    if exit_plan:
        sp = float(exit_plan.get("stop_loss_pct", sp * 100) or sp * 100) / 100.0
        tp = float(exit_plan.get("take_profit_pct", tp * 100) or tp * 100) / 100.0
    stop = entry * (1 - sp)
    target = entry * (1 + tp)
    et = bar.timestamp   # 신호 발생 bar 시각 (09:00 아님).
    one = _exit_trade("BUY", entry, stop, target, et, day_bars5, day_bars1, use_1m=True)
    five = _exit_trade("BUY", entry, stop, target, et, day_bars5, day_bars1, use_1m=False)
    net_edge_bps = tp * 1e4 - _ROUND_TRIP_BPS
    return {
        "strategy": name, "symbol": sym, "day": day, "entry": entry,
        "net": one.net_pnl, "gross": one.gross_pnl, "slip5": one.slippage_paid,
        "net_5m": five.net_pnl, "hold": one.hold_minutes, "exit_reason": one.exit_reason,
        "vol_exp": ve, "low_liq": low_liq, "group": group, "net_edge_bps": net_edge_bps,
    }


def run_final_multi_strategy_backtest(*, one_min_dir: Path | None = None,
                                      five_min_dir: Path | None = None,
                                      symbols: Sequence[str] | None = None) -> dict[str, Any]:
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    strat_trades, council_trades, blocks, agent, vote_dist, excluded, syms = _collect(
        one_dir, five_dir, symbols)

    total = sum(len(v) for v in strat_trades.values()) + len(council_trades)
    if total < 10:
        return _empty(total, syms)

    # 전략별: basic_5m / intrabar_1m / +risk_filter + 비용전후 + stress + 등급.
    per_strategy = {}
    for name, trades in strat_trades.items():
        rf = [t for t in trades if _kept_by_risk_filter(t)]
        removed = [t for t in trades if not _kept_by_risk_filter(t)]
        m_1m = _metrics(trades, "net")
        per_strategy[name] = {
            "basic_5m": _metrics(trades, "net_5m"),
            "intrabar_1m": m_1m,
            "intrabar_1m_risk_filter": _metrics(rf, "net"),
            "cost_before_pf": _pf([t["gross"] for t in trades]),
            "cost_after_pf": m_1m["profit_factor"],
            "slippage_stress": _slippage_stress(trades),
            "risk_filter": {
                "removed_count": len(removed),
                "avoided_loss": round(sum(t["net"] for t in removed if t["net"] < 0), 2),
                "missed_profit": round(sum(t["net"] for t in removed if t["net"] > 0), 2),
            },
            "grade": _grade(m_1m),
        }

    # council.
    crf = [t for t in council_trades if _kept_by_risk_filter(t)]
    council_basic = _metrics(council_trades, "net")
    council_rf = _metrics(crf, "net")

    pfs = {n: per_strategy[n]["intrabar_1m"]["profit_factor"] for n in _STRATS
           if per_strategy[n]["intrabar_1m"]["profit_factor"] is not None}
    best_single = max(pfs, key=pfs.get) if pfs else None
    best_pf = pfs.get(best_single) if best_single else None
    avg_pf = round(sum(pfs.values()) / len(pfs), 3) if pfs else None
    cpf = council_basic["profit_factor"]

    verdict, conclusion = _verdict(per_strategy, council_basic, council_rf,
                                   best_pf, avg_pf)

    return {
        "available": True,
        "mode": "final_multi_strategy_backtest",
        "data": {"symbols": syms, "excluded": excluded,
                 "strategy_trade_counts": {n: len(v) for n, v in strat_trades.items()},
                 "council_trade_count": len(council_trades)},
        "cost_model": {"commission_bps": DEFAULT_COST.commission_bps,
                       "tax_bps": DEFAULT_COST.tax_bps,
                       "slippage_bps": DEFAULT_COST.slippage_bps,
                       "round_trip_bps": _ROUND_TRIP_BPS},
        "per_strategy": per_strategy,
        "council": {
            "basic": council_basic, "with_risk_filter": council_rf,
            "best_single_strategy": best_single, "best_single_pf": best_pf,
            "avg_single_pf": avg_pf,
            "council_vs_best_single_pf_delta": _d(cpf, best_pf),
            "council_vs_avg_single_pf_delta": _d(cpf, avg_pf),
            "risk_filter_effect_pf_delta": _d(council_rf["profit_factor"], cpf),
            "agent_helped_count": agent["helped"], "agent_hurt_count": agent["hurt"],
            "strategy_vote_distribution": vote_dist,
            "risk_veto_count": blocks["risk_veto"],
            "quality_gate_block_count": blocks["quality_gate"],
            "no_exit_plan_block_count": blocks["no_exit_plan"],
            "council_buy_count": blocks["council_buy"],
        },
        "strategy_grades": {n: per_strategy[n]["grade"] for n in _STRATS},
        "verdict": verdict,
        "conclusion": conclusion,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. 어떤 전략/필터도 런타임에 "
                      "자동 적용되지 않습니다. 수익을 보장하지 않습니다.",
        "next_steps": [
            "비용 후 PF<1 전략은 EXCLUDE — 전략 로직 자체 개선 필요(파라미터 최적화 아님)",
            "RISK_FILTER_ONLY 후보는 별도 승인 + paper rehearsal 후에만 검토",
            "Council 이 단독을 못 이기면 Council 구조 재검토",
        ],
    }


def _grade(m: dict) -> str:
    pf = m.get("profit_factor")
    if pf is None or pf < 1.0:
        return "WEAK_OR_EXCLUDE"
    if pf < 1.2:
        return "WATCH"
    if (m.get("mdd_pct") or 0) > 25:
        return "TUNE"
    return "KEEP"


def _d(a, b):
    return None if a is None or b is None else round(a - b, 4)


def _verdict(per_strategy, council_basic, council_rf, best_pf, avg_pf):
    cpf = council_basic.get("profit_factor")
    crf_pf = council_rf.get("profit_factor")
    surv = [n for n, v in per_strategy.items()
            if (v["intrabar_1m"]["profit_factor"] or 0) >= 1.2]
    any_edge = (best_pf or 0) >= 1.0

    if not any_edge and (cpf or 0) < 1.0 and (crf_pf or 0) < 1.0:
        return ("STRATEGY_EDGE_NOT_FOUND",
                ["비용 후 어떤 단독 전략도 PF≥1.0 미달, Council 도 미달 — 현 전략군은 "
                 "실전성 없음. 전략 로직 개선이 ranking/council 보다 우선."])
    if (crf_pf or 0) > (best_pf or 0) and (crf_pf or 0) >= 1.2:
        return ("COUNCIL_CANDIDATE",
                [f"Council+RiskFilter PF {crf_pf} 가 최고 단독 {best_pf} 초과 — Council 후보. "
                 "단 자동 적용 금지, 추가 검증 필요."])
    if (crf_pf or 0) >= 1.2 or any(per_strategy[n]["intrabar_1m_risk_filter"]["profit_factor"]
                                   and per_strategy[n]["intrabar_1m_risk_filter"]["profit_factor"] >= 1.2
                                   for n in per_strategy):
        return ("RISK_FILTER_CANDIDATE",
                ["RISK_FILTER 적용 시 일부 구성이 PF≥1.2 — filter 후보. 단독 reorder/council "
                 "우위는 불명확. 자동 적용 금지."])
    if surv:
        return ("SINGLE_STRATEGY_CANDIDATE",
                [f"단독 전략 {surv} 가 PF≥1.2 (비용 후) — 후보. Council 은 추가 가치 불명확. "
                 "자동 적용 금지."])
    return ("STRATEGY_EDGE_NOT_FOUND",
            ["비용 후 PF≥1.2 단독/council 없음 — 현 전략군 실전성 약함. 자동 적용 금지."])


def _empty(n, syms):
    return {"available": False, "verdict": "BACKTEST_INFRA_INCOMPLETE",
            "reason": "INSUFFICIENT_TRADES", "trade_count": n, "symbols": syms,
            "auto_apply_allowed": False, "applied_to_runtime": False,
            "is_live_authorization": False, "no_profit_guarantee": True,
            "contains_secret": False,
            "disclaimer": "연구용 백테스트 — 거래 표본 부족. 자동 적용 안 됨, 실전매매 권고 아님."}
