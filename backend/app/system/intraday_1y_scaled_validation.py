"""KIS-INTRADAY-1Y-SCALED-VALIDATION-01 — 1년 데이터, 10/25/50 종목 확장 검증 (read-only).

LOCKED-60D-WEEKLY 의 RESEARCH_PROMISING 은 6개월 내부 holdout 결과였고 새 forward 데이터가
없었다. 본 모듈은 *과거 1년* 5분봉(기존 kis_6m 최근분 + 별도 수집한 older 6개월 병합)으로
**고정 룰(`FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1`)을 변경 없이** 10→25→50 종목으로 확장 검증해
종목 폭 의존성 / 분기·반기 안정성 / RISK_VETO 유효성을 본다.

룰 hash 검증(불일치→BLOCKED), look-ahead 금지(universe 는 직전 lookback 만). Paper/Backtest
only. broker / OrderExecutor / route_order / KIS 주문 API import·호출 0건. EXE 빌드 0건, 실전 금지.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.system import forward_universe_selector as fus
from app.system import forward_universe_validation as fuv
from app.system import locked_60d_weekly_validation as lk
from app.system.locked_60d_weekly_new_data_validation import (
    EXPECTED_LOCKED_RULE_HASH,
    compute_rule_hash,
)

_KST = timezone(timedelta(hours=9))

# 10종목 (core + 대표 대형주, 결과와 무관하게 사전 고정).
STAGE10 = ("005930", "000270", "005380", "012330", "042700",
           "066570", "006400", "000660", "035420", "051910")
MIN_TRADING_DAYS = 200

# verdict.
SCALE_FAIL = "SCALE_FAIL"
SCALE_WEAK = "SCALE_WEAK"
SCALE_WATCH = "SCALE_WATCH"
SCALE_PAPER_CANDIDATE = "SCALE_PAPER_CANDIDATE"
SCALE_RESEARCH_CONFIRMED = "SCALE_RESEARCH_CONFIRMED"
SCALE_BLOCKED = "SCALE_BLOCKED"
_RANK = {SCALE_BLOCKED: -1, SCALE_FAIL: 0, SCALE_WEAK: 1, SCALE_WATCH: 2,
         SCALE_PAPER_CANDIDATE: 3, SCALE_RESEARCH_CONFIRMED: 4}

_EXE_REC = {
    SCALE_BLOCKED: "EXE 재빌드 보류 (룰 hash 불일치/데이터 불가)",
    SCALE_FAIL: "EXE 재빌드 보류 (1년 확장 실패)",
    SCALE_WEAK: "EXE 재빌드 보류 또는 관찰 UI만",
    SCALE_WATCH: "관찰용 EXE 재빌드 가능, 자동매매/모의주문 비활성",
    SCALE_PAPER_CANDIDATE: "EXE 재빌드 후 dry-run KIS 모의 리허설 가능 (자동주문 OFF, DRY_RUN=true)",
    SCALE_RESEARCH_CONFIRMED: "EXE 재빌드 후 제한적 dry-run 리허설 가능 (실전 금지, 실제 모의주문은 별도 승인 게이트)",
}


@dataclass(frozen=True)
class Scaled1YReport:
    generated_at: str
    data_source: str
    locked_rule_name: str
    locked_rule_hash: str
    rule_hash_match: bool
    rule_locked_before_validation: bool
    no_parameter_change: bool
    no_look_ahead: bool
    data_quality: dict[str, Any]
    trading_days: int
    stage_10: dict[str, Any]
    stage_25: dict[str, Any]
    stage_50: dict[str, Any]
    scale_analysis: dict[str, Any]
    monthly_quarterly: dict[str, Any]
    half_split: dict[str, Any]
    worst_month: dict[str, Any]
    symbol_split: dict[str, Any]
    agent_compare: dict[str, Any]
    slippage_stress: dict[str, Any]
    defense_analysis: dict[str, Any]
    repeated_selected: tuple[str, ...]
    repeated_excluded: tuple[str, ...]
    breadth_dependency: dict[str, Any]
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
    real_order_allowed: bool = False
    dry_run_required: bool = True
    broker_order_sent: bool = False
    order_created: bool = False
    exe_build_executed: bool = False
    contains_secret: bool = False
    no_profit_guarantee: bool = True
    safety_disclaimer: str = (
        "이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 1년 확장이 통과해도 실전매매는 "
        "금지이며, Paper rehearsal 은 KIS 모의매매 dry-run 후보일 뿐입니다(실제 모의주문도 별도 승인 "
        "게이트 필요). 수익을 보장하지 않습니다. 본 작업은 EXE 빌드를 수행하지 않습니다."
    )
    _extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (not self.do_not_auto_apply or not self.no_profit_guarantee or self.auto_apply_allowed
                or self.exe_build_executed or self.live_trading_recommendation
                or self.real_order_allowed or not self.dry_run_required):
            raise ValueError("unsafe invariant")
        if self.is_live_authorization or self.broker_order_sent or self.order_created:
            raise ValueError("unsafe invariant True")
        if self.contains_secret:
            raise ValueError("contains_secret must be False")
        if self.final_verdict not in _RANK:
            raise ValueError(f"invalid verdict: {self.final_verdict}")


def _date_of(b) -> Any:
    return b.timestamp.astimezone(_KST).date()


def _load_year(dirs: list[str | Path]):
    """여러 디렉토리의 CSV 를 병합 — (symbol, timestamp) 중복 제거."""
    from app.backtest.strategy_council_backtest import load_ohlcv_from_csv
    seen: set[tuple[str, str]] = set()
    bars: list[Any] = []
    files: list[Path] = []
    for d in dirs:
        p = Path(d)
        if p.is_dir():
            files += sorted(p.glob("*.csv"))
    for f in files:
        for b in load_ohlcv_from_csv(str(f)):
            key = (b.symbol, b.timestamp.isoformat())
            if key in seen:
                continue
            seen.add(key)
            bars.append(b)
    bars.sort(key=lambda b: (b.symbol, b.timestamp))
    return bars


def _quality(bars) -> dict[str, Any]:
    by_sym: dict[str, set] = {}
    for b in bars:
        by_sym.setdefault(b.symbol, set()).add(_date_of(b))
    days_per = {s: len(ds) for s, ds in by_sym.items()}
    all_days = sorted({d for ds in by_sym.values() for d in ds})
    min_days = min(days_per.values()) if days_per else 0
    enough = sum(1 for v in days_per.values() if v >= 220)
    status = "PASS" if (days_per and enough >= len(days_per) * 0.8) else (
        "WARN" if min_days >= 200 or (all_days and len(all_days) >= 200) else "FAIL")
    return {"symbol_count": len(by_sym), "total_trading_days": len(all_days),
            "min_days_per_symbol": min_days,
            "symbols_ge_220d": enough, "date_range": [str(all_days[0]) if all_days else None,
                                                      str(all_days[-1]) if all_days else None],
            "quality_status": status}


def _run(bars, signals, *, universe, rule, count_window=None, label):
    sub = [b for b in bars if universe is None or b.symbol in universe]
    keep = {(b.symbol, b.timestamp.isoformat()) for b in sub}
    sig = {k: v for k, v in signals.items() if k in keep}
    return fuv.run_universe_backtest(
        sub, sig, selector="FORWARD_STABLE_UNIVERSE", lookback_days=60, rebalance_freq="weekly",
        universe_size=10, rule=rule, in_sample_top10=None, label=label, count_window=count_window)


def _slim(r: dict) -> dict:
    return {k: r.get(k) for k in ("_label", "forward_return_pct", "forward_mdd_pct", "median_pf",
                                  "positive_ratio", "worst_period_pct", "total_trades",
                                  "universe_stability", "selection_turnover", "low_confidence",
                                  "periods")}


def _stage_verdict(r: dict, *, slip_ok: bool, risk_veto_better: bool) -> str:
    fr = r.get("forward_return_pct")
    pf = r.get("median_pf")
    mdd = r.get("forward_mdd_pct")
    tr = r.get("total_trades", 0)
    posr = r.get("positive_ratio") or 0
    if fr is None or pf is None:
        return SCALE_FAIL
    if fr < 0 or pf < 1.05 or (mdd or 99) > 20:
        return SCALE_FAIL
    if (fr >= 8 and pf >= 1.20 and (mdd or 99) <= 12 and tr >= 150 and slip_ok and risk_veto_better
            and posr >= 0.5):
        return SCALE_RESEARCH_CONFIRMED
    if (fr >= 5 and pf >= 1.15 and (mdd or 99) <= 15 and tr >= 150 and slip_ok and risk_veto_better
            and posr >= 0.5):
        return SCALE_PAPER_CANDIDATE
    if fr >= 3 and pf >= 1.10 and (mdd or 99) <= 15:
        return SCALE_WATCH
    return SCALE_WEAK


def run_scaled_1y_validation(
    *, dirs: list[str | Path] | None = None, generated_at: str | None = None,
) -> Scaled1YReport:
    from app.system.wf_6m_signal_extract import extract_signal_map

    gen = generated_at or datetime.now(timezone.utc).isoformat()
    rule_hash = compute_rule_hash()
    hash_match = rule_hash == EXPECTED_LOCKED_RULE_HASH
    dirs = dirs or ["data/market/intraday_ohlcv/kis_6m", "data/market/intraday_5m_1y_old"]

    bars = _load_year(dirs)
    quality = _quality(bars)
    td = quality["total_trading_days"]

    empty = {"note": "데이터 부족/룰 변경으로 미실행."}
    if not hash_match:
        return _build(gen, rule_hash, hash_match, quality, td, SCALE_BLOCKED,
                      reason="locked rule hash 불일치 — 검증 중 룰 변경 감지.", empty=empty)
    if td < MIN_TRADING_DAYS or quality["quality_status"] == "FAIL":
        return _build(gen, rule_hash, hash_match, quality, td, SCALE_FAIL,
                      reason=f"1년 데이터 부족(거래일 {td} < {MIN_TRADING_DAYS} 또는 품질 FAIL).",
                      empty=empty)

    signals = extract_signal_map(bars).signals
    rule = dict(lk._LOCKED_BASE_RULE)
    all_syms = sorted({b.symbol for b in bars})
    uni10 = frozenset(s for s in STAGE10 if s in all_syms)
    uni25 = frozenset(all_syms[:25])
    uni50 = frozenset(all_syms)

    # RISK_VETO better? (50종목 기준 OFF 대비).
    s50 = _run(bars, signals, universe=uni50, rule=rule, label="STAGE_50")
    s50_off = _run(bars, signals, universe=uni50,
                   rule={**rule, "agent_mode": "AGENT_OFF"}, label="STAGE_50_OFF")
    risk_veto_better = (s50["forward_return_pct"] or -99) > (s50_off["forward_return_pct"] or -99)
    # slippage stress (50종목).
    slip = {}
    for bps in (5.0, 7.0, 10.0):
        r = _run(bars, signals, universe=uni50, rule={**rule, "slippage_bps": bps},
                 label=f"SLIP_{bps}")
        slip[f"slippage_{int(bps)}bps"] = _slim(r)
    slip_ok = (slip["slippage_10bps"]["forward_return_pct"] or -99) >= 0

    s10 = _run(bars, signals, universe=uni10, rule=rule, label="STAGE_10")
    s25 = _run(bars, signals, universe=uni25, rule=rule, label="STAGE_25")
    v10 = _stage_verdict(s10, slip_ok=slip_ok, risk_veto_better=risk_veto_better)
    v25 = _stage_verdict(s25, slip_ok=slip_ok, risk_veto_better=risk_veto_better)
    v50 = _stage_verdict(s50, slip_ok=slip_ok, risk_veto_better=risk_veto_better)

    # 분기/반기/worst-month/symbol-split (50종목 기준).
    days = fus.trading_days(bars)
    half = len(days) // 2
    first_half = set(days[:half])
    last_half = set(days[half:])
    h1 = _run(bars, signals, universe=uni50, rule=rule, count_window=first_half, label="FIRST_HALF")
    h2 = _run(bars, signals, universe=uni50, rule=rule, count_window=last_half, label="LAST_HALF")
    # 분기 (월 그룹 3개씩).
    months = sorted({f"{d.year}-{d.month:02d}" for d in days})
    quarters = {}
    for qi in range(0, len(months), 3):
        qmonths = months[qi:qi + 3]
        cw = {d for d in days if f"{d.year}-{d.month:02d}" in qmonths}
        qr = _run(bars, signals, universe=uni50, rule=rule, count_window=cw, label=f"Q_{qmonths[0]}")
        quarters[f"{qmonths[0]}..{qmonths[-1]}"] = {"return_pct": qr["forward_return_pct"],
                                                    "trades": qr["total_trades"]}
    # worst month.
    monthly = {}
    for mk in months:
        cw = {d for d in days if f"{d.year}-{d.month:02d}" == mk}
        mr = _run(bars, signals, universe=uni50, rule=rule, count_window=cw, label=f"M_{mk}")
        monthly[mk] = mr["forward_return_pct"]
    worst_m = min(monthly, key=monthly.get) if monthly else None
    pos_m = sum(1 for v in monthly.values() if (v or 0) > 0)
    neg_m = sum(1 for v in monthly.values() if (v or 0) < 0)
    # symbol split (50 even/odd).
    even = frozenset(all_syms[::2])
    odd = frozenset(all_syms[1::2])
    sp_e = _run(bars, signals, universe=even, rule=rule, label="SPLIT_EVEN")
    sp_o = _run(bars, signals, universe=odd, rule=rule, label="SPLIT_ODD")

    # agent compare (50).
    agent = {"RISK_VETO_ONLY": _slim(s50), "AGENT_OFF": _slim(s50_off)}
    for amode, sizing, nm in (("AGENT_REVIEW_ONLY", False, "REVIEW_ONLY"),
                              ("AGENT_POSITION_SIZER_ONLY", False, "POSITION_SIZER_ONLY"),
                              ("AGENT_RISK_VETO_ONLY", True, "RISK_VETO_PLUS_SIZER")):
        rr = dict(rule, agent_mode=amode)
        if sizing:
            rr["apply_position_sizing"] = True
        agent[nm] = _slim(_run(bars, signals, universe=uni50, rule=rr, label=f"AGENT_{nm}"))
    # defense (50).
    def_none = _run(bars, signals, universe=uni50,
                    rule={"agent_mode": "AGENT_RISK_VETO_ONLY", "selection_mode": "composite_rank",
                          "allowed_strategies": ("GAP", "ORB", "VWAP")}, label="DEF_NONE")
    defense = {"no_defense": _slim(def_none), "daily_1.5": _slim(s50)}

    # 반복 선택/제외 (50종목 full).
    sel_freq: dict[str, int] = {}
    for p in s50.get("per_period", []):
        for s in p["selected"]:
            sel_freq[s] = sel_freq.get(s, 0) + 1
    nper = max(1, len(s50.get("per_period", [])))
    rep_sel = tuple(s for s, c in sorted(sel_freq.items(), key=lambda x: -x[1]) if c >= nper * 0.5)[:15]
    rep_excl = tuple(sorted(uni50 - set(sel_freq)))[:15]

    breadth = {
        "stage10_return": s10["forward_return_pct"], "stage25_return": s25["forward_return_pct"],
        "stage50_return": s50["forward_return_pct"],
        "symbol_split_even": sp_e["forward_return_pct"], "symbol_split_odd": sp_o["forward_return_pct"],
        "split_decay_pp": round((s50["forward_return_pct"] or 0)
                                - min(sp_e["forward_return_pct"] or 0, sp_o["forward_return_pct"] or 0), 2),
        "note": ("종목 폭(10→25→50)에 따라 성과가 어떻게 변하는지 + 종목 절반 split 에서 붕괴 여부. "
                 "10만 좋고 25/50 에서 약화되면 breadth 의존."),
    }

    # 전체 verdict: 단계별 최소(보수). 50 통과해야 PAPER 이상.
    stage_ranks = [_RANK[v10], _RANK[v25], _RANK[v50]]
    overall_rank = min(stage_ranks)
    # 50 이 PAPER 이상이어도 10/25 중 하나라도 FAIL 이면 WATCH 로 cap.
    rank_to_v = {v: k for k, v in _RANK.items()}
    final = rank_to_v[overall_rank]
    if _RANK[v50] >= _RANK[SCALE_PAPER_CANDIDATE] and overall_rank < _RANK[SCALE_WATCH]:
        final = SCALE_WATCH

    scale_analysis = {"stage_10": {"verdict": v10, **_slim(s10)},
                      "stage_25": {"verdict": v25, **_slim(s25)},
                      "stage_50": {"verdict": v50, **_slim(s50)},
                      "scaling_note": ("10→25→50 단계 verdict. 전체는 단계 최소(보수)이며, 50 이 "
                                       "통과해도 하위 단계 FAIL 시 WATCH 로 cap.")}
    mq = {"monthly_returns": monthly, "positive_months": pos_m, "negative_months": neg_m,
          "quarters": quarters, "worst_month": worst_m,
          "worst_month_return": monthly.get(worst_m) if worst_m else None}
    return _build(gen, rule_hash, hash_match, quality, td, final,
                  reason="1년 데이터 10/25/50 확장 검증 실행.", empty=None,
                  stage10={"verdict": v10, **_slim(s10)}, stage25={"verdict": v25, **_slim(s25)},
                  stage50={"verdict": v50, **_slim(s50)}, scale_analysis=scale_analysis,
                  mq=mq, half={"first_half": _slim(h1), "last_half": _slim(h2)},
                  worst={"worst_month": worst_m, "worst_month_return": monthly.get(worst_m) if worst_m else None,
                         "positive_months": pos_m, "negative_months": neg_m},
                  symsplit={"even": _slim(sp_e), "odd": _slim(sp_o),
                            "decay_pp": breadth["split_decay_pp"]},
                  agent=agent, slip={**slip, "slippage_10bps_ok": slip_ok},
                  defense=defense, rep_sel=rep_sel, rep_excl=rep_excl, breadth=breadth,
                  risk_veto_better=risk_veto_better)


def _build(gen, rule_hash, hash_match, quality, td, verdict, *, reason, empty,
           stage10=None, stage25=None, stage50=None, scale_analysis=None, mq=None, half=None,
           worst=None, symsplit=None, agent=None, slip=None, defense=None, rep_sel=(), rep_excl=(),
           breadth=None, risk_veto_better=False) -> Scaled1YReport:
    paper_ok = _RANK[verdict] >= _RANK[SCALE_PAPER_CANDIDATE]
    paper_rec = ("KIS 모의매매 dry-run 리허설 후보 (1년 확장 통과, 단 실전 금지)"
                 if paper_ok else "Paper 리허설 아직 불가 — 추가 검증/데이터 필요")
    conclusions = [
        f"locked rule hash {'일치(불변)' if hash_match else '불일치(변경 감지)'} · 거래일 {td} · verdict {verdict}.",
        reason,
        (f"breadth: 10 {breadth['stage10_return']}% / 25 {breadth['stage25_return']}% / "
         f"50 {breadth['stage50_return']}% · split decay {breadth['split_decay_pp']}pp · "
         f"RISK_VETO 우위 {risk_veto_better}." if breadth else
         "1년 데이터 부족 또는 룰 변경으로 단계 검증 미실행."),
    ]
    next_steps = [
        "50종목 1년 verdict 가 PAPER_CANDIDATE 이상일 때만 dry-run(자동주문 OFF) EXE 재빌드 검토.",
        "breadth 의존(10만 좋고 50 약화)·분기 편중 시 종목 폭/기간을 더 넓혀 재검증.",
        "실전매매는 금지, 실제 모의주문도 별도 승인 게이트 필요.",
    ]
    return Scaled1YReport(
        generated_at=gen, data_source="KIS_INTRADAY_DAILYCHART_5M_1Y",
        locked_rule_name=lk.LOCKED_RULE_NAME, locked_rule_hash=rule_hash, rule_hash_match=hash_match,
        rule_locked_before_validation=True, no_parameter_change=True, no_look_ahead=True,
        data_quality=quality, trading_days=td,
        stage_10=stage10 or dict(empty or {}), stage_25=stage25 or dict(empty or {}),
        stage_50=stage50 or dict(empty or {}), scale_analysis=scale_analysis or dict(empty or {}),
        monthly_quarterly=mq or dict(empty or {}), half_split=half or dict(empty or {}),
        worst_month=worst or dict(empty or {}), symbol_split=symsplit or dict(empty or {}),
        agent_compare=agent or dict(empty or {}), slippage_stress=slip or dict(empty or {}),
        defense_analysis=defense or dict(empty or {}), repeated_selected=tuple(rep_sel),
        repeated_excluded=tuple(rep_excl), breadth_dependency=breadth or dict(empty or {}),
        final_verdict=verdict, paper_rehearsal_recommendation=paper_rec,
        exe_rebuild_recommendation=_EXE_REC[verdict], next_steps=tuple(next_steps),
        conclusions=tuple(conclusions))


def to_dict(r: Scaled1YReport) -> dict[str, Any]:
    return {
        "generated_at": r.generated_at, "data_source": r.data_source,
        "locked_rule_name": r.locked_rule_name, "locked_rule_hash": r.locked_rule_hash,
        "rule_hash_match": r.rule_hash_match,
        "rule_locked_before_validation": r.rule_locked_before_validation,
        "no_parameter_change": r.no_parameter_change, "no_look_ahead": r.no_look_ahead,
        "data_quality": r.data_quality, "trading_days": r.trading_days,
        "stage_10": r.stage_10, "stage_25": r.stage_25, "stage_50": r.stage_50,
        "scale_analysis": r.scale_analysis, "monthly_quarterly": r.monthly_quarterly,
        "half_split": r.half_split, "worst_month": r.worst_month, "symbol_split": r.symbol_split,
        "agent_compare": r.agent_compare, "slippage_stress": r.slippage_stress,
        "defense_analysis": r.defense_analysis, "repeated_selected": list(r.repeated_selected),
        "repeated_excluded": list(r.repeated_excluded), "breadth_dependency": r.breadth_dependency,
        "final_verdict": r.final_verdict, "paper_rehearsal_recommendation": r.paper_rehearsal_recommendation,
        "exe_rebuild_recommendation": r.exe_rebuild_recommendation, "next_steps": list(r.next_steps),
        "conclusions": list(r.conclusions), "do_not_auto_apply": r.do_not_auto_apply,
        "auto_apply_allowed": r.auto_apply_allowed, "is_live_authorization": r.is_live_authorization,
        "live_trading_recommendation": r.live_trading_recommendation,
        "real_order_allowed": r.real_order_allowed, "dry_run_required": r.dry_run_required,
        "broker_order_sent": r.broker_order_sent, "order_created": r.order_created,
        "exe_build_executed": r.exe_build_executed, "contains_secret": r.contains_secret,
        "no_profit_guarantee": r.no_profit_guarantee, "safety_disclaimer": r.safety_disclaimer,
    }


def render_markdown(r: Scaled1YReport) -> str:
    def _s(x):
        return (f"{x.get('forward_return_pct')}% · PF {x.get('median_pf')} · MDD "
                f"{x.get('forward_mdd_pct')}% · 거래 {x.get('total_trades')} · {x.get('verdict','')}")
    q = r.data_quality
    lines = [
        "# 1년 데이터 10/25/50 확장 검증 (locked rule)",
        "",
        "> 이 결과는 연구/백테스트 결과이며 실전매매 권고가 아닙니다. 자동 적용/실전 전환/EXE 빌드 0건. 수익 보장 아님.",
        f"> locked_rule={r.locked_rule_name} · hash_match={r.rule_hash_match} · "
        f"no_parameter_change={r.no_parameter_change} · no_look_ahead={r.no_look_ahead}",
        "",
        f"## 최종 verdict: **{r.final_verdict}**",
        f"- EXE 재빌드 권고: **{r.exe_rebuild_recommendation}**",
        f"- Paper 리허설: {r.paper_rehearsal_recommendation}",
        f"- 실전매매 권고 {r.live_trading_recommendation} · 실제주문 허용 {r.real_order_allowed} · "
        f"dry_run 필수 {r.dry_run_required}",
        "",
        "## 데이터 품질",
        f"- 종목 {q.get('symbol_count')} · 총 거래일 {q.get('total_trading_days')} · "
        f"종목당 최소 {q.get('min_days_per_symbol')} · ≥220d {q.get('symbols_ge_220d')} · "
        f"품질 {q.get('quality_status')} · 기간 {q.get('date_range')}",
        "",
        "## 단계별 (10 → 25 → 50)",
        f"- 10종목: {_s(r.stage_10)}",
        f"- 25종목: {_s(r.stage_25)}",
        f"- 50종목: {_s(r.stage_50)}",
        f"- breadth: {r.breadth_dependency}",
        "",
        f"## 월/분기: 양월 {r.monthly_quarterly.get('positive_months')} / 음월 "
        f"{r.monthly_quarterly.get('negative_months')} · worst {r.monthly_quarterly.get('worst_month')} "
        f"({r.monthly_quarterly.get('worst_month_return')}%)",
        f"- 분기: {r.monthly_quarterly.get('quarters')}",
        f"## 전반/후반: {r.half_split}",
        f"## symbol split: {r.symbol_split}",
        f"## Agent 비교: {r.agent_compare}",
        f"## slippage: {r.slippage_stress}",
        f"## 손실방어: {r.defense_analysis}",
        f"## 반복 선택: {list(r.repeated_selected)}",
        "",
        "## 결론",
        *[f"- {c}" for c in r.conclusions],
        "## 다음 단계",
        *[f"- {s}" for s in r.next_steps],
        "",
        f"> {r.safety_disclaimer}",
    ]
    return "\n".join(lines) + "\n"
