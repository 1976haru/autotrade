"""KIS-INTRADAY-60D-WEEKLY-FIXED-REVALIDATION-01 — 60d/weekly 고정 룰 재검증 (read-only).

FORWARD-UNIVERSE-REBUILD 의 grid 에서 60d/weekly 가 +8.3% 로 유망했으나 *grid search 결과*라
사후최적화 의심이 있었다. 본 모듈은 그 변형을 **하나의 고정 룰(V1)** 로 잠그고
(`rule_locked_before_validation=True`, 검증 중 파라미터 변경 금지), original replay +
last-20D / last-40D / worst-month holdout + symbol split + slippage stress 로 forward 유지
여부를 검증한다. **모든 universe 선택은 직전 lookback 만 사용(look-ahead 금지).**

Paper/Backtest only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건.
**EXE 빌드 0건** (권고 라벨만), 실전 금지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.system import forward_universe_selector as fus
from app.system import forward_universe_validation as fuv

_KST = timezone(timedelta(hours=9))

# 고정 룰 V1 (검증 전 명문화 — 변경 금지).
LOCKED_RULE_NAME = "FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1"
LOCKED_PARAMS = {
    "selector": "FORWARD_STABLE_UNIVERSE",
    "lookback_days": 60,
    "rebalance_frequency": "weekly",
    "universe_size_primary": 10,          # FORWARD-UNIVERSE grid best(FORWARD_STABLE, size10).
    "universe_size_secondary": [15, 20],  # 보조 비교만.
    "agent_mode": "AGENT_RISK_VETO_ONLY",
    "selection_mode": "composite_rank",
    "allowed_strategies": ["GAP", "ORB", "VWAP"],
    "max_positions": 5,
    "capital_krw": 10_000_000,
    "fee_bps_per_side": 1.5, "sell_tax_bps": 18.0, "slippage_bps": 5.0,
    "loss_defense_primary": "daily_loss_stop_-1.5%",
    "exit": "stop/target + EOD (no trailing/max-hold/forced-close)",
}
_LOCKED_BASE_RULE = {"agent_mode": "AGENT_RISK_VETO_ONLY", "selection_mode": "composite_rank",
                     "daily_loss_stop_pct": 1.5, "allowed_strategies": ("GAP", "ORB", "VWAP")}

# verdict.
LOCKED_RULE_FAIL = "LOCKED_RULE_FAIL"
LOCKED_RULE_WEAK = "LOCKED_RULE_WEAK"
LOCKED_RULE_WATCH = "LOCKED_RULE_WATCH"
LOCKED_RULE_PAPER_CANDIDATE = "LOCKED_RULE_PAPER_CANDIDATE"
LOCKED_RULE_RESEARCH_PROMISING = "LOCKED_RULE_RESEARCH_PROMISING"
_LV_RANK = {LOCKED_RULE_FAIL: 0, LOCKED_RULE_WEAK: 1, LOCKED_RULE_WATCH: 2,
            LOCKED_RULE_PAPER_CANDIDATE: 3, LOCKED_RULE_RESEARCH_PROMISING: 4}

_EXE_REC = {
    LOCKED_RULE_FAIL: "EXE 재빌드 보류 (holdout 실패) — 검증 계속",
    LOCKED_RULE_WEAK: "EXE 재빌드 보류 또는 관찰용 UI만 (자동매매/모의주문 비활성)",
    LOCKED_RULE_WATCH: "EXE 관찰용 재빌드 가능, 자동매매/모의주문 비활성",
    LOCKED_RULE_PAPER_CANDIDATE: "EXE 재빌드 후 dry-run 중심 KIS 모의 리허설 가능 (실전 금지)",
    LOCKED_RULE_RESEARCH_PROMISING: "EXE 재빌드 후 dry-run KIS 모의 리허설 가능 (실전 금지, 추가기간 권장)",
}


@dataclass(frozen=True)
class Locked60dReport:
    generated_at: str
    data_source: str
    rule_locked_before_validation: bool
    locked_rule_name: str
    locked_at: str
    locked_parameters: dict[str, Any]
    score_formula: dict[str, Any]
    no_look_ahead: bool
    additional_data_collected: bool
    original_6m_replay: dict[str, Any]
    last20_holdout: dict[str, Any]
    last40_holdout: dict[str, Any]
    worst_month_holdout: dict[str, Any]
    symbol_split: dict[str, Any]
    slippage_stress: dict[str, Any]
    compare_40d_monthly: dict[str, Any]
    risk_veto_recheck: dict[str, Any]
    defense_recheck: dict[str, Any]
    secondary_size_compare: dict[str, Any]
    repeated_selected: tuple[str, ...]
    repeated_excluded: tuple[str, ...]
    missed_opportunity: tuple[dict[str, Any], ...]
    train_test_decay: dict[str, Any]
    final_verdict: str
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
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. holdout 이 통과해도 실전매매는 "
        "금지이며, Paper rehearsal 은 KIS 모의매매 후보일 뿐입니다. 수익을 보장하지 않습니다. "
        "본 작업은 EXE 빌드를 수행하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed
                or self.exe_build_executed or self.live_trading_recommendation
                or not self.rule_locked_before_validation or not self.no_look_ahead):
            raise ValueError("unsafe / lock invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in _LV_RANK:
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def _date_of(b) -> Any:
    return b.timestamp.astimezone(_KST).date()


def _run(bars, signals, *, rule, lookback, freq, size, count_window=None, label):
    return fuv.run_universe_backtest(
        bars, signals, selector="FORWARD_STABLE_UNIVERSE", lookback_days=lookback,
        rebalance_freq=freq, universe_size=size, rule=rule, in_sample_top10=None,
        label=label, count_window=count_window)


def _slim(r: dict) -> dict:
    return {k: r.get(k) for k in ("_label", "forward_return_pct", "forward_mdd_pct", "median_pf",
                                  "positive_ratio", "worst_period_pct", "total_trades",
                                  "universe_stability", "selection_turnover", "low_confidence")}


def _verdict(h: dict, *, worst_month_defended: bool, slippage_ok: bool, decay_ok: bool) -> str:
    fr = h.get("forward_return_pct")
    pf = h.get("median_pf")
    mdd = h.get("forward_mdd_pct")
    trades = h.get("total_trades", 0)
    if fr is None or pf is None:
        return LOCKED_RULE_FAIL
    if fr < 0 or pf < 1.05 or (mdd or 99) > 20:
        return LOCKED_RULE_FAIL
    sufficient = trades >= 50  # holdout(40D) 기준 충분 거래.
    if (fr >= 8 and pf >= 1.20 and (mdd or 99) <= 12 and sufficient
            and worst_month_defended and slippage_ok and decay_ok):
        return LOCKED_RULE_RESEARCH_PROMISING
    if (fr >= 5 and pf >= 1.15 and (mdd or 99) <= 15 and sufficient
            and worst_month_defended and slippage_ok):
        return LOCKED_RULE_PAPER_CANDIDATE
    if fr >= 3 and pf >= 1.10 and (mdd or 99) <= 15:
        return LOCKED_RULE_WATCH
    return LOCKED_RULE_WEAK


def run_locked_60d_weekly(
    input_dir: str | Path, *, symbols: list[str] | None = None, generated_at: str | None = None,
) -> Locked60dReport:
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

    days = fus.trading_days(bars)
    last20 = set(days[-20:])
    last40 = set(days[-40:])
    # worst month (라벨용) — 전체 baseline 월별에서 최저.
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
    base_full = result_to_dict(run_sim_v2(bars, signals, SimV2Config()))
    mr = base_full.get("monthly_returns", {})
    worst_m = min(mr, key=mr.get) if mr else None
    worst_dates = {dd for dd in days if f"{dd.year}-{dd.month:02d}" == worst_m} if worst_m else set()

    # A. original 6M replay (locked, full).
    orig = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10,
                label="ORIGINAL_6M_REPLAY")
    # B/C/D holdouts (point-in-time lookback, count only holdout periods).
    h20 = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10,
               count_window=last20, label="LAST20_HOLDOUT")
    h40 = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10,
               count_window=last40, label="LAST40_HOLDOUT")
    hwm = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10,
               count_window=worst_dates, label=f"WORST_MONTH_HOLDOUT_{worst_m}")
    # E. symbol split (even/odd 종목군).
    syms = sorted({b.symbol for b in bars})
    even, odd = set(syms[::2]), set(syms[1::2])
    eb = [b for b in bars if b.symbol in even]
    ob = [b for b in bars if b.symbol in odd]
    es = {k: v for k, v in signals.items() if k[0] in even}
    os_ = {k: v for k, v in signals.items() if k[0] in odd}
    sp_even = _run(eb, es, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10, label="SPLIT_EVEN")
    sp_odd = _run(ob, os_, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=10, label="SPLIT_ODD")
    symbol_split = {"even_symbols": len(even), "odd_symbols": len(odd),
                    "even": _slim(sp_even), "odd": _slim(sp_odd),
                    "return_decay_pp": round((sp_even["forward_return_pct"] or 0)
                                             - (sp_odd["forward_return_pct"] or 0), 2)}
    # F. slippage stress.
    slip = {}
    for bps in (5.0, 7.0, 10.0):
        r = _run(bars, signals, rule={**_LOCKED_BASE_RULE, "slippage_bps": bps}, lookback=60,
                 freq="weekly", size=10, label=f"SLIP_{bps}bps")
        slip[f"slippage_{int(bps)}bps"] = _slim(r)
    slippage_ok = (slip["slippage_10bps"]["forward_return_pct"] or -99) >= 0

    # 비교: STATIC_ALL + 40d/monthly.
    static_all = fuv.run_universe_backtest(bars, signals, selector="STATIC_BASELINE_ALL",
                                           lookback_days=60, rebalance_freq="weekly",
                                           universe_size=10, rule=_LOCKED_BASE_RULE,
                                           in_sample_top10=None, label="STATIC_ALL")
    m40 = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=40, freq="monthly", size=10,
               label="FORWARD_STABLE_40D_MONTHLY")
    compare = {"static_all": _slim(static_all), "forward_40d_monthly": _slim(m40),
               "locked_60d_weekly_full": _slim(orig),
               "note": "60d/weekly 가 40d/monthly·static 대비 holdout 에서 우위인지 확인."}

    # RISK_VETO recheck (locked vs OFF, full).
    rv_off = _run(bars, signals, rule={**_LOCKED_BASE_RULE, "agent_mode": "AGENT_OFF"},
                  lookback=60, freq="weekly", size=10, label="AGENT_OFF")
    risk_veto_recheck = {"risk_veto_only": _slim(orig), "agent_off": _slim(rv_off),
                         "risk_veto_better": (orig["forward_return_pct"] or -99) > (rv_off["forward_return_pct"] or -99)}
    # defense recheck (none vs daily).
    def_none = _run(bars, signals, rule={"agent_mode": "AGENT_RISK_VETO_ONLY",
                                         "selection_mode": "composite_rank",
                                         "allowed_strategies": ("GAP", "ORB", "VWAP")},
                    lookback=60, freq="weekly", size=10, label="DEFENSE_NONE")
    defense_recheck = {"no_defense": _slim(def_none), "daily_loss_1.5": _slim(orig),
                       "defense_changed_outcome": abs((def_none["forward_return_pct"] or 0)
                                                      - (orig["forward_return_pct"] or 0)) > 0.5}
    # 보조 size 비교.
    sec = {}
    for sz in (15, 20):
        r = _run(bars, signals, rule=_LOCKED_BASE_RULE, lookback=60, freq="weekly", size=sz,
                 label=f"SIZE_{sz}")
        sec[f"size_{sz}"] = _slim(r)

    # 반복 선택/제외.
    sel_freq: dict[str, int] = {}
    for p in orig.get("per_period", []):
        for s in p["selected"]:
            sel_freq[s] = sel_freq.get(s, 0) + 1
    nper = max(1, len(orig.get("per_period", [])))
    rep_sel = tuple(s for s, c in sorted(sel_freq.items(), key=lambda x: -x[1]) if c >= nper * 0.5)[:15]
    all_syms = {b.symbol for b in bars}
    rep_excl = tuple(sorted(all_syms - set(sel_freq)))[:15]
    by_sym = base_full.get("by_symbol", {})
    missed = tuple({"symbol": s, "full_period_net_pnl": by_sym[s]["net_pnl"]}
                   for s in sorted(by_sym, key=lambda x: -(by_sym[x]["net_pnl"] or 0))
                   if s not in set(sel_freq) and (by_sym[s]["net_pnl"] or 0) > 0)[:10]

    # decay: original full vs last40 holdout.
    decay = {"full_return": orig["forward_return_pct"], "holdout40_return": h40["forward_return_pct"],
             "decay_pp": round((orig["forward_return_pct"] or 0) - (h40["forward_return_pct"] or 0), 2)}
    decay_ok = (decay["decay_pp"] or 0) < (abs(orig["forward_return_pct"] or 1) * 0.7 + 5)

    worst_month_defended = (hwm["forward_return_pct"] or -99) > (mr.get(worst_m, -99) if worst_m else -99)

    # 최종 verdict — 주 holdout = last-40D.
    verdict = _verdict(h40, worst_month_defended=worst_month_defended,
                       slippage_ok=slippage_ok, decay_ok=decay_ok)
    paper_rec = ("KIS 모의매매 dry-run 리허설 후보 (holdout 통과, 단 실전 금지)"
                 if _LV_RANK[verdict] >= _LV_RANK[LOCKED_RULE_PAPER_CANDIDATE] else
                 "Paper 리허설 아직 불가 — holdout/추가기간 보강 필요")
    conclusions = [
        f"original 6M replay {orig['forward_return_pct']}% (grid +8.3% 재현 확인용) · "
        f"last-40D holdout {h40['forward_return_pct']}%/PF{h40['median_pf']}/MDD{h40['forward_mdd_pct']}% → {verdict}.",
        f"40d/monthly {m40['forward_return_pct']}% vs 60d/weekly full {orig['forward_return_pct']}% · "
        f"static_all {static_all['forward_return_pct']}%.",
        f"RISK_VETO 우위 {risk_veto_recheck['risk_veto_better']} · worst-month({worst_m}) 방어 "
        f"{worst_month_defended} · slippage10bps OK {slippage_ok} · symbol-split decay "
        f"{symbol_split['return_decay_pp']}pp.",
    ]
    next_steps = [
        "holdout 이 WATCH 이상이면 *관찰용* EXE 재빌드 검토(자동매매 비활성).",
        "PAPER_CANDIDATE 이상이면 추가 기간 데이터 수집 후 동일 룰로 재검증(룰 변경 금지).",
        "WEAK/FAIL 이면 추가 기간 확보가 우선 — 6개월 holdout 표본이 작아 신뢰도 제한.",
    ]

    return Locked60dReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        rule_locked_before_validation=True, locked_rule_name=LOCKED_RULE_NAME, locked_at=gen,
        locked_parameters=LOCKED_PARAMS,
        score_formula={"weights": fus.SCORE_WEIGHTS, "roundtrip_cost_frac": fus.ROUNDTRIP_COST_FRAC,
                       "min_trades": fus.MIN_TRADES},
        no_look_ahead=True, additional_data_collected=False,
        original_6m_replay=_slim(orig), last20_holdout=_slim(h20), last40_holdout=_slim(h40),
        worst_month_holdout={**_slim(hwm), "worst_month": worst_m,
                             "baseline_worst_month_return": mr.get(worst_m) if worst_m else None,
                             "defended": worst_month_defended},
        symbol_split=symbol_split, slippage_stress={**slip, "slippage_10bps_ok": slippage_ok},
        compare_40d_monthly=compare, risk_veto_recheck=risk_veto_recheck,
        defense_recheck=defense_recheck, secondary_size_compare=sec,
        repeated_selected=rep_sel, repeated_excluded=rep_excl, missed_opportunity=missed,
        train_test_decay={**decay, "decay_ok": decay_ok}, final_verdict=verdict,
        paper_rehearsal_recommendation=paper_rec, exe_rebuild_recommendation=_EXE_REC[verdict],
        next_steps=tuple(next_steps), conclusions=tuple(conclusions))


def to_dict(r: Locked60dReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "rule_locked_before_validation": r.rule_locked_before_validation,
        "locked_rule_name": r.locked_rule_name, "locked_at": r.locked_at,
        "locked_parameters": r.locked_parameters, "score_formula": r.score_formula,
        "no_look_ahead": r.no_look_ahead, "additional_data_collected": r.additional_data_collected,
        "original_6m_replay": r.original_6m_replay, "last20_holdout": r.last20_holdout,
        "last40_holdout": r.last40_holdout, "worst_month_holdout": r.worst_month_holdout,
        "symbol_split": r.symbol_split, "slippage_stress": r.slippage_stress,
        "compare_40d_monthly": r.compare_40d_monthly, "risk_veto_recheck": r.risk_veto_recheck,
        "defense_recheck": r.defense_recheck, "secondary_size_compare": r.secondary_size_compare,
        "repeated_selected": list(r.repeated_selected), "repeated_excluded": list(r.repeated_excluded),
        "missed_opportunity": list(r.missed_opportunity), "train_test_decay": r.train_test_decay,
        "final_verdict": r.final_verdict, "paper_rehearsal_recommendation": r.paper_rehearsal_recommendation,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation, "next_steps": list(r.next_steps),
        "conclusions": list(r.conclusions), "do_not_auto_apply": r.do_not_auto_apply,
        "auto_apply_allowed": r.auto_apply_allowed, "is_live_authorization": r.is_live_authorization,
        "live_trading_recommendation": r.live_trading_recommendation,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "exe_build_executed": r.exe_build_executed, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "safety_disclaimer": r.safety_disclaimer,
    }


def render_markdown(r: Locked60dReport) -> str:
    def _f(h):
        return (f"{h.get('forward_return_pct')}% · PF {h.get('median_pf')} · MDD "
                f"{h.get('forward_mdd_pct')}% · 거래 {h.get('total_trades')}")
    lines = [
        "# 60d/weekly 고정 룰 재검증 (LOCKED_V1)",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        f"> rule_locked_before_validation={r.rule_locked_before_validation} · no_look_ahead={r.no_look_ahead} · "
        f"locked_rule={r.locked_rule_name}",
        "",
        f"## 최종 verdict: **{r.final_verdict}**",
        f"- EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        f"- Paper 리허설: {r.paper_rehearsal_recommendation}",
        f"- 실전매매 권고: **{r.live_trading_recommendation}** (항상 false)",
        "",
        "## Locked rule",
        f"- {r.locked_parameters}",
        "",
        "## Holdout 결과 (모두 point-in-time lookback)",
        f"- original 6M replay: {_f(r.original_6m_replay)}",
        f"- last-20D holdout: {_f(r.last20_holdout)}",
        f"- last-40D holdout (주 판정): {_f(r.last40_holdout)}",
        f"- worst-month({r.worst_month_holdout.get('worst_month')}) holdout: {_f(r.worst_month_holdout)} · "
        f"방어 {r.worst_month_holdout.get('defended')} (baseline {r.worst_month_holdout.get('baseline_worst_month_return')}%)",
        "",
        "## Symbol split (even vs odd 종목군)",
        f"- even {_f(r.symbol_split['even'])} / odd {_f(r.symbol_split['odd'])} · decay {r.symbol_split['return_decay_pp']}pp",
        "",
        "## Slippage stress",
        *[f"- {k}: {v.get('forward_return_pct') if isinstance(v, dict) else v}%"
          for k, v in r.slippage_stress.items() if isinstance(v, dict)],
        f"- slippage10bps OK: {r.slippage_stress.get('slippage_10bps_ok')}",
        "",
        "## 40d/monthly vs 60d/weekly vs static",
        f"- static_all {r.compare_40d_monthly['static_all'].get('forward_return_pct')}% · "
        f"40d/monthly {r.compare_40d_monthly['forward_40d_monthly'].get('forward_return_pct')}% · "
        f"60d/weekly full {r.compare_40d_monthly['locked_60d_weekly_full'].get('forward_return_pct')}%",
        "",
        f"## RISK_VETO 재검증: RISK_VETO 우위 {r.risk_veto_recheck['risk_veto_better']} "
        f"(RV {r.risk_veto_recheck['risk_veto_only'].get('forward_return_pct')}% vs OFF "
        f"{r.risk_veto_recheck['agent_off'].get('forward_return_pct')}%)",
        f"## 손실방어 재검증: no-defense {r.defense_recheck['no_defense'].get('forward_return_pct')}% vs "
        f"daily1.5 {r.defense_recheck['daily_loss_1.5'].get('forward_return_pct')}%",
        f"## 보조 size: {r.secondary_size_compare}",
        f"## 반복 선택: {list(r.repeated_selected)}",
        f"## train/test decay: {r.train_test_decay}",
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "## 다음 단계",
        *[f"- {s}" for s in r.next_steps],
        "",
        f"> {r.safety_disclaimer}",
    ]
    return "\n".join(lines) + "\n"
