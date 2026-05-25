"""KIS-INTRADAY-FORWARD-VALIDATION-01 — 고정 룰 forward/OOS 검증 (read-only).

DECOMPOSITION-01 의 개선 조합은 *동일 6개월 내부* 에서 선택돼 in-sample 편향 가능성이 있다.
본 모듈은 룰을 **검증 전에 고정**(rule_locked_before_validation=True)하고, universe/ranking 을
*train 구간/종목에서만* 만들어 *test 구간/종목* 에 적용해 forward 생존을 검증한다.

splits: Monthly forward / Anchored forward / Rolling symbol split / Worst-month holdout.
엔진: `wf_6m_signal_extract`(캐시) + `wf_6m_sim_v2`. Paper/Backtest only. broker /
OrderExecutor / route_order / KIS 주문 API import·호출 0건. **EXE 빌드 0건** (권고 라벨만).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_KST = timezone(timedelta(hours=9))

# forward verdict.
FORWARD_FAIL = "FORWARD_FAIL"
FORWARD_WEAK = "FORWARD_WEAK"
FORWARD_WATCH = "FORWARD_WATCH"
PAPER_REHEARSAL_CONFIRMED = "PAPER_REHEARSAL_CONFIRMED"
_FV_RANK = {FORWARD_FAIL: 0, FORWARD_WEAK: 1, FORWARD_WATCH: 2, PAPER_REHEARSAL_CONFIRMED: 3}

_EXE_REC = {
    FORWARD_FAIL: "EXE 재빌드 보류 — 전략 검증 계속 (forward 실패)",
    FORWARD_WEAK: "EXE 재빌드 가능하나 자동매매/모의주문 비활성, 관찰용 리포트 UI만",
    FORWARD_WATCH: "EXE 재빌드 가능 — 모의 리허설 준비 가능, 단 자동주문은 dry-run 우선",
    PAPER_REHEARSAL_CONFIRMED: "EXE 재빌드 후 KIS *모의매매* 리허설 가능 (실전 금지, 소액/모의/제한)",
}

# 고정 룰 후보 (검증 *전* 정의).
RULE_CANDIDATES = {
    "A_top10_riskveto_daily": {"universe": "GO_TUNE_TOP10", "agent_mode": "AGENT_RISK_VETO_ONLY",
                               "selection_mode": "composite_rank", "daily_loss_stop_pct": 1.5},
    "B_top20_sizer_equitydd": {"universe": "GO_TUNE_TOP20", "agent_mode": "AGENT_POSITION_SIZER_ONLY",
                               "selection_mode": "composite_rank", "equity_dd_stop_pct": 10.0},
    "C_excl_removed_riskveto": {"universe": "EXCLUDE_REMOVED", "agent_mode": "AGENT_RISK_VETO_ONLY",
                                "selection_mode": "composite_rank", "daily_loss_stop_pct": 1.5},
    "D_agent_off_pure": {"universe": "ALL", "agent_mode": "AGENT_OFF",
                         "selection_mode": "composite_rank",
                         "allowed_strategies": ("GAP", "ORB", "VWAP")},
}


@dataclass(frozen=True)
class ForwardReport:
    generated_at: str
    data_source: str
    rule_locked_before_validation: bool
    rule_candidates: dict[str, Any]
    monthly_forward: dict[str, Any]
    anchored_forward: dict[str, Any]
    symbol_split: dict[str, Any]
    worst_month_holdout: dict[str, Any]
    agent_forward: dict[str, Any]
    decay_analysis: dict[str, Any]
    overfit_warning: dict[str, Any]
    final_forward_verdict: str
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
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. forward 검증이 통과해도 "
        "실전매매는 금지이며, 'Paper rehearsal'은 KIS 모의매매 리허설 후보일 뿐입니다. "
        "수익을 보장하지 않습니다. 본 작업은 EXE 빌드를 수행하지 않습니다."
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
        if self.final_forward_verdict not in _FV_RANK:
            raise ValueError(f"invalid verdict: {self.final_forward_verdict}")


# ─────────────────────────── slicing ───────────────────────────

def _date_of(b) -> Any:
    return b.timestamp.astimezone(_KST).date()


def _mkey(d) -> str:
    return f"{d.year}-{d.month:02d}"


def _slice_dates(bars, signals, date_set):
    sb = [b for b in bars if _date_of(b) in date_set]
    keep = {(b.symbol, b.timestamp.isoformat()) for b in sb}
    ss = {k: v for k, v in signals.items() if k in keep}
    return sb, ss


def _slice_symbols(bars, signals, syms):
    sb = [b for b in bars if b.symbol in syms]
    ss = {k: v for k, v in signals.items() if k[0] in syms}
    return sb, ss


# ─────────────────────────── train context ───────────────────────────

def _train_context(train_bars, train_signals):
    """train 구간/종목에서만 universe·grade·ranking 보조값 생성 (test 미참조)."""
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
    r = result_to_dict(run_sim_v2(train_bars, train_signals,
                                  SimV2Config(selection_mode="earliest_first",
                                              agent_mode="AGENT_ENTRY_DECIDER")))
    by_sym = r.get("by_symbol", {})
    pos = sorted([(s, v["net_pnl"]) for s, v in by_sym.items() if (v["net_pnl"] or 0) > 0],
                 key=lambda x: -x[1])
    n = len(pos)
    grade: dict[str, str] = {}
    for i, (s, _) in enumerate(pos):
        grade[s] = "GO" if i < max(1, n // 3) else "TUNE"
    for s, v in by_sym.items():
        if s not in grade:
            grade[s] = "EXCLUDE" if (v["net_pnl"] or 0) <= 0 else "TUNE"
    ranked = [s for s, _ in pos]
    # strategy_pf / bucket_edge from train signals fwd.
    sg, sl = defaultdict(float), defaultdict(float)
    be = defaultdict(list)
    for v in train_signals.values():
        be[v["time_bucket"]].append(v["fwd_eod_return"])
        for st in v["single_buys"]:
            (sg if v["fwd_eod_return"] >= 0 else sl)[st] += abs(v["fwd_eod_return"])
    strat_pf = {st: (round(sg[st] / sl[st], 4) if sl[st] else None)
                for st in set(sg) | set(sl)}
    bucket_edge = {k: round(statistics.mean(v), 6) for k, v in be.items() if v}
    return {"grade_map": grade, "ranked": ranked, "strategy_pf": strat_pf,
            "bucket_edge": bucket_edge, "train_return": r["total_return_pct"]}


def _universe_for(spec: str, ctx: dict) -> frozenset[str] | None:
    grade, ranked = ctx["grade_map"], ctx["ranked"]
    if spec == "ALL":
        return None
    if spec == "EXCLUDE_REMOVED":
        return frozenset(s for s, g in grade.items() if g != "EXCLUDE")
    if spec == "GO_TUNE_TOP10":
        return frozenset(ranked[:10])
    if spec == "GO_TUNE_TOP20":
        return frozenset(ranked[:20])
    return None


def _run_candidate(test_bars, test_signals, cand: dict, ctx: dict, *,
                   agent_override: str | None = None, sizing: bool = False):
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
    kw = dict(grade_map=ctx["grade_map"], strategy_pf=ctx["strategy_pf"],
              bucket_edge=ctx["bucket_edge"],
              universe=_universe_for(cand["universe"], ctx),
              selection_mode=cand.get("selection_mode", "composite_rank"),
              agent_mode=agent_override or cand["agent_mode"])
    for k in ("daily_loss_stop_pct", "equity_dd_stop_pct"):
        if k in cand:
            kw[k] = cand[k]
    if cand.get("allowed_strategies"):
        kw["allowed_strategies"] = frozenset(cand["allowed_strategies"])
    if sizing:
        kw["apply_position_sizing"] = True
    return result_to_dict(run_sim_v2(test_bars, test_signals, SimV2Config(**kw)))


def _slim(r: dict, label: str) -> dict:
    return {"_label": label, "return_pct": r["total_return_pct"], "pf": r["profit_factor"],
            "mdd": r["max_drawdown_pct"], "trades": r["trade_count"],
            "worst_consec": r["worst_consecutive_losses"], "wf": r["walk_forward_score"],
            "oos_positive": r["oos_positive"], "low_confidence": r["low_confidence"]}


def _chain(returns: list[float]) -> dict[str, Any]:
    """test 구간 수익률 리스트를 복리 연결 → forward 요약."""
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
    total = (series[-1] - 10_000_000.0) / 10_000_000.0 * 100.0
    pos = sum(1 for r in returns if (r or 0) > 0)
    return {"forward_return_pct": round(total, 3), "forward_mdd_pct": round(mdd * 100, 2),
            "windows": len(returns), "positive_windows": pos,
            "positive_ratio": round(pos / len(returns), 3) if returns else None,
            "worst_window_pct": round(min(returns), 3) if returns else None,
            "monthly": [round(r, 3) for r in returns]}


# ─────────────────────────── splits ───────────────────────────

def _months_sorted(bars) -> list[str]:
    return sorted({_mkey(_date_of(b)) for b in bars})


def _month_dates(bars, mkey) -> set:
    return {_date_of(b) for b in bars if _mkey(_date_of(b)) == mkey}


def _monthly_forward(bars, signals, min_train_months=2) -> dict:
    months = _months_sorted(bars)
    out = {}
    for cname, cand in RULE_CANDIDATES.items():
        per_month, train_rets = [], []
        for i in range(min_train_months, len(months)):
            train_dates = set().union(*[_month_dates(bars, months[j]) for j in range(i)])
            test_dates = _month_dates(bars, months[i])
            tb, tsig = _slice_dates(bars, signals, train_dates)
            xb, xsig = _slice_dates(bars, signals, test_dates)
            if not xb:
                continue
            ctx = _train_context(tb, tsig)
            r = _run_candidate(xb, xsig, cand, ctx)
            per_month.append({"month": months[i], "return_pct": r["total_return_pct"],
                              "pf": r["profit_factor"], "mdd": r["max_drawdown_pct"],
                              "trades": r["trade_count"]})
            train_rets.append(ctx["train_return"])
        chained = _chain([m["return_pct"] for m in per_month])
        out[cname] = {**chained, "per_month": per_month,
                      "mean_train_return": round(statistics.mean(train_rets), 3) if train_rets else None,
                      "total_trades": sum(m["trades"] for m in per_month),
                      "median_pf": _med([m["pf"] for m in per_month])}
    return out


def _anchored_forward(bars, signals, anchor_months=2) -> dict:
    months = _months_sorted(bars)
    if len(months) <= anchor_months:
        return {"insufficient": True}
    train_dates = set().union(*[_month_dates(bars, months[j]) for j in range(anchor_months)])
    tb, tsig = _slice_dates(bars, signals, train_dates)
    out = {}
    for cname, cand in RULE_CANDIDATES.items():
        ctx = _train_context(tb, tsig)   # 앵커 구간에서 *한 번* 고정.
        per_month = []
        for i in range(anchor_months, len(months)):
            xb, xsig = _slice_dates(bars, signals, _month_dates(bars, months[i]))
            if not xb:
                continue
            r = _run_candidate(xb, xsig, cand, ctx)
            per_month.append({"month": months[i], "return_pct": r["total_return_pct"],
                              "pf": r["profit_factor"], "mdd": r["max_drawdown_pct"],
                              "trades": r["trade_count"]})
        chained = _chain([m["return_pct"] for m in per_month])
        out[cname] = {**chained, "per_month": per_month,
                      "anchor_train_return": ctx["train_return"],
                      "total_trades": sum(m["trades"] for m in per_month),
                      "median_pf": _med([m["pf"] for m in per_month])}
    return out


def _symbol_split(bars, signals) -> dict:
    syms = sorted({b.symbol for b in bars})
    train_syms = set(syms[::2])
    test_syms = set(syms[1::2])
    tb, tsig = _slice_symbols(bars, signals, train_syms)
    xb, xsig = _slice_symbols(bars, signals, test_syms)
    ctx = _train_context(tb, tsig)
    out = {"train_symbols": len(train_syms), "test_symbols": len(test_syms)}
    for cname, cand in RULE_CANDIDATES.items():
        # 종목 split: train 종목에서 만든 grade/ranking 을 test 종목에 적용.
        # universe spec 이 종목코드 기반이므로 test 종목 중 train-grade 가 있는 것만.
        r_train = _run_candidate(tb, tsig, cand, ctx)
        r_test = _run_candidate(xb, xsig, cand, ctx)
        out[cname] = {"train": _slim(r_train, "train"), "test": _slim(r_test, "test"),
                      "return_decay_pp": round((r_train["total_return_pct"] or 0)
                                               - (r_test["total_return_pct"] or 0), 2)}
    return out


def _worst_month_holdout(bars, signals) -> dict:
    months = _months_sorted(bars)
    # baseline 으로 worst month 탐지.
    from app.system.wf_6m_sim_v2 import SimV2Config, result_to_dict, run_sim_v2
    base = result_to_dict(run_sim_v2(bars, signals, SimV2Config()))
    mr = base.get("monthly_returns", {})
    worst = min(mr, key=mr.get) if mr else (months[0] if months else None)
    if worst is None:
        return {"insufficient": True}
    train_dates = set().union(*[_month_dates(bars, m) for m in months if m != worst])
    test_dates = _month_dates(bars, worst)
    tb, tsig = _slice_dates(bars, signals, train_dates)
    xb, xsig = _slice_dates(bars, signals, test_dates)
    ctx = _train_context(tb, tsig)
    out = {"worst_month": worst, "baseline_worst_month_return": mr.get(worst)}
    for cname, cand in RULE_CANDIDATES.items():
        r = _run_candidate(xb, xsig, cand, ctx)
        out[cname] = {"holdout_return_pct": r["total_return_pct"], "mdd": r["max_drawdown_pct"],
                      "trades": r["trade_count"], "pf": r["profit_factor"],
                      "defended": (r["total_return_pct"] or -99) > (mr.get(worst) or -99)}
    return out


def _agent_forward(bars, signals) -> dict:
    """Monthly forward 안에서 Agent 5모드 재검증 (candidate A universe 기준)."""
    months = _months_sorted(bars)
    cand = RULE_CANDIDATES["A_top10_riskveto_daily"]
    modes = {
        "AGENT_OFF": ("AGENT_OFF", False),
        "AGENT_RISK_VETO_ONLY": ("AGENT_RISK_VETO_ONLY", False),
        "AGENT_POSITION_SIZER_ONLY": ("AGENT_POSITION_SIZER_ONLY", False),
        "AGENT_RISK_VETO_PLUS_POSITION_SIZER": ("AGENT_RISK_VETO_ONLY", True),
        "AGENT_REVIEW_ONLY": ("AGENT_REVIEW_ONLY", False),
    }
    out = {}
    for mname, (amode, sizing) in modes.items():
        rets = []
        for i in range(2, len(months)):
            train_dates = set().union(*[_month_dates(bars, months[j]) for j in range(i)])
            tb, tsig = _slice_dates(bars, signals, train_dates)
            xb, xsig = _slice_dates(bars, signals, _month_dates(bars, months[i]))
            if not xb:
                continue
            ctx = _train_context(tb, tsig)
            r = _run_candidate(xb, xsig, cand, ctx, agent_override=amode, sizing=sizing)
            rets.append(r["total_return_pct"])
        out[mname] = _chain(rets)
    return out


def _med(xs):
    vals = [x for x in xs if x is not None]
    return round(statistics.median(vals), 4) if vals else None


# ─────────────────────────── verdict ───────────────────────────

def _forward_verdict(monthly: dict, anchored: dict, holdout: dict) -> tuple[str, dict, dict]:
    """best candidate 의 forward 지표로 판정 (in-sample 아님)."""
    cand_scores = {}
    for cname in RULE_CANDIDATES:
        m = monthly.get(cname, {})
        a = anchored.get(cname, {}) if not anchored.get("insufficient") else {}
        h = holdout.get(cname, {}) if not holdout.get("insufficient") else {}
        fr = m.get("forward_return_pct")
        mdd = m.get("forward_mdd_pct")
        pf = m.get("median_pf")
        trades = m.get("total_trades", 0)
        posr = m.get("positive_ratio") or 0
        defended = h.get("defended", False)
        anchored_pos = (a.get("forward_return_pct") or -1) >= 0
        if fr is None or pf is None:
            v = FORWARD_FAIL
        elif fr < 0 or pf < 1.05:
            v = FORWARD_FAIL
        elif fr >= 5 and pf >= 1.15 and (mdd or 99) <= 15 and trades >= 100 and posr >= 0.5 \
                and defended and anchored_pos:
            v = PAPER_REHEARSAL_CONFIRMED
        elif fr >= 2 and pf >= 1.10 and (mdd or 99) <= 20 and trades >= 60:
            v = FORWARD_WATCH
        else:
            v = FORWARD_WEAK
        cand_scores[cname] = {"verdict": v, "forward_return_pct": fr, "median_pf": pf,
                              "forward_mdd_pct": mdd, "total_trades": trades,
                              "positive_ratio": posr, "worst_month_defended": defended,
                              "anchored_positive": anchored_pos}
    best = max(cand_scores, key=lambda c: (_FV_RANK[cand_scores[c]["verdict"]],
                                           cand_scores[c]["forward_return_pct"] or -1e9))
    return cand_scores[best]["verdict"], {"best_candidate": best, **cand_scores[best]}, cand_scores


def run_forward_validation(
    input_dir: str | Path, *, symbols: list[str] | None = None, generated_at: str | None = None,
) -> ForwardReport:
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

    monthly = _monthly_forward(bars, signals)
    anchored = _anchored_forward(bars, signals)
    symbol_split = _symbol_split(bars, signals)
    holdout = _worst_month_holdout(bars, signals)
    agent_fwd = _agent_forward(bars, signals)

    verdict, best, cand_scores = _forward_verdict(monthly, anchored, holdout)

    # decay (in-sample train vs forward test) — candidate 별.
    decay = {}
    for c, m in monthly.items():
        tr = m.get("mean_train_return")
        te = m.get("forward_return_pct")
        decay[c] = {"mean_train_return": tr, "forward_return": te,
                    "decay_pp": round((tr or 0) - (te or 0), 2) if tr is not None else None}
    sym_decay = {c: symbol_split[c]["return_decay_pp"] for c in RULE_CANDIDATES if c in symbol_split}

    overfit = {
        "in_sample_decomposition_best": "DEF_daily_loss_1.5 (+9.2%, in-sample)",
        "forward_best": f"{best['best_candidate']} ({best.get('forward_return_pct')}% forward)",
        "warning": (verdict in (FORWARD_FAIL, FORWARD_WEAK)),
        "note": ("in-sample 개선이 forward 에서 약화/붕괴되면 과최적화. decay_pp 가 크고 "
                 "forward_return 이 음(-)/저조하면 룰이 6개월 내부에만 맞춘 것."),
        "candidate_decay": decay, "symbol_split_decay": sym_decay,
    }

    paper_rec = ("KIS 모의매매 리허설 후보 (forward 통과, 단 실전 금지)"
                 if verdict == PAPER_REHEARSAL_CONFIRMED else
                 "Paper 리허설 *아직* 불가 — forward 검증 보강 필요")
    conclusions = [
        f"forward 최고 후보: {best['best_candidate']} · forward_return {best.get('forward_return_pct')}% · "
        f"median PF {best.get('median_pf')} · MDD {best.get('forward_mdd_pct')}% → {verdict}.",
        ("in-sample(+9%) 대비 forward 가 약하면 과최적화 의심 — 룰을 더 단순/보수적으로."
         if verdict in (FORWARD_FAIL, FORWARD_WEAK) else
         "forward 에서도 양(+) 유지 — in-sample 편향이 전부는 아님(단 실전 아님)."),
        "Agent 기능별 forward 재검증 결과를 함께 확인 (RISK_VETO/SIZER 안정성).",
    ]
    next_steps = [
        "forward 통과 후보만 *고정* 해 추가 기간/추가 종목으로 재검증.",
        "통과 시에만 EXE 재빌드 → KIS 모의매매 dry-run 리허설 (실전 금지).",
        "forward 실패/약화 시 룰 단순화 + 비용·회전 축소 후 재검증.",
    ]

    return ForwardReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M",
        rule_locked_before_validation=True, rule_candidates=RULE_CANDIDATES,
        monthly_forward=monthly, anchored_forward=anchored, symbol_split=symbol_split,
        worst_month_holdout=holdout, agent_forward=agent_fwd, decay_analysis=decay,
        overfit_warning=overfit, final_forward_verdict=verdict,
        paper_rehearsal_recommendation=paper_rec, exe_rebuild_recommendation=_EXE_REC[verdict],
        next_steps=tuple(next_steps), conclusions=tuple(conclusions),
        _extra={"best": best, "candidate_scores": cand_scores})


def to_dict(r: ForwardReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "rule_locked_before_validation": r.rule_locked_before_validation,
        "rule_candidates": r.rule_candidates, "monthly_forward": r.monthly_forward,
        "anchored_forward": r.anchored_forward, "symbol_split": r.symbol_split,
        "worst_month_holdout": r.worst_month_holdout, "agent_forward": r.agent_forward,
        "decay_analysis": r.decay_analysis, "overfit_warning": r.overfit_warning,
        "final_forward_verdict": r.final_forward_verdict,
        "paper_rehearsal_recommendation": r.paper_rehearsal_recommendation,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation,
        "next_steps": list(r.next_steps), "conclusions": list(r.conclusions),
        "best": r._extra.get("best", {}), "candidate_scores": r._extra.get("candidate_scores", {}),
        "do_not_auto_apply": r.do_not_auto_apply, "auto_apply_allowed": r.auto_apply_allowed,
        "is_live_authorization": r.is_live_authorization,
        "live_trading_recommendation": r.live_trading_recommendation,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "exe_build_executed": r.exe_build_executed, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "safety_disclaimer": r.safety_disclaimer,
    }


def render_markdown(r: ForwardReport) -> str:
    bestc = r._extra.get("best", {})
    cs = r._extra.get("candidate_scores", {})
    lines = [
        "# WF-6M Forward / Out-of-Sample Validation",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        f"> rule_locked_before_validation = {r.rule_locked_before_validation}",
        "",
        f"## 최종 forward verdict: **{r.final_forward_verdict}**",
        f"- best candidate: **{bestc.get('best_candidate')}** · forward_return "
        f"{bestc.get('forward_return_pct')}% · median PF {bestc.get('median_pf')} · "
        f"MDD {bestc.get('forward_mdd_pct')}% · trades {bestc.get('total_trades')} · "
        f"worst-month 방어 {bestc.get('worst_month_defended')}",
        f"- EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        f"- Paper 리허설: {r.paper_rehearsal_recommendation}",
        f"- 실전매매 권고: **{r.live_trading_recommendation}** (항상 false)",
        "",
        "## 후보별 forward 점수 (Monthly forward 기준)",
        "| candidate | verdict | forward% | median PF | MDD% | trades | pos_ratio | worst_def |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {c} | {v['verdict']} | {v['forward_return_pct']} | {v['median_pf']} | "
          f"{v['forward_mdd_pct']} | {v['total_trades']} | {v['positive_ratio']} | "
          f"{v['worst_month_defended']} |" for c, v in cs.items()],
        "",
        "## Anchored forward (첫 2개월 고정 → 이후 적용)",
        *[f"- {c}: forward {a.get('forward_return_pct')}% · MDD {a.get('forward_mdd_pct')}% · "
          f"trades {a.get('total_trades')}" for c, a in r.anchored_forward.items()
          if isinstance(a, dict) and "forward_return_pct" in a],
        "",
        "## Rolling symbol split (train 종목 → test 종목)",
        *[f"- {c}: test {r.symbol_split[c]['test']['return_pct']}% (train "
          f"{r.symbol_split[c]['train']['return_pct']}%, decay {r.symbol_split[c]['return_decay_pp']}pp)"
          for c in RULE_CANDIDATES if c in r.symbol_split],
        "",
        f"## Worst-month holdout ({r.worst_month_holdout.get('worst_month')}, baseline "
        f"{r.worst_month_holdout.get('baseline_worst_month_return')}%)",
        *[f"- {c}: holdout {v['holdout_return_pct']}% · 방어 {v['defended']}"
          for c, v in r.worst_month_holdout.items() if isinstance(v, dict) and "holdout_return_pct" in v],
        "",
        "## Agent 기능별 forward (Monthly)",
        *[f"- {m}: forward {a.get('forward_return_pct')}% · MDD {a.get('forward_mdd_pct')}% · "
          f"pos_ratio {a.get('positive_ratio')}" for m, a in r.agent_forward.items()],
        "",
        "## Overfit / decay",
        f"- {r.overfit_warning.get('note')}",
        f"- warning={r.overfit_warning.get('warning')} · candidate decay={r.decay_analysis}",
        "",
        "## 다음 단계",
        *[f"- {s}" for s in r.next_steps],
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "",
        f"> {r.safety_disclaimer}",
    ]
    return "\n".join(lines) + "\n"
