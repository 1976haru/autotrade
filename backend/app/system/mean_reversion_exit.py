"""평균회귀 exit 구조 연구 (CHECKLIST-05, 백테스트 전용).

평균회귀 후보 6종은 비용 전에도 PF<1 이었으나, 분석에서 "되돌림은 잦은데 고정 −1% stop /
+1.5% *연속형* target(추세용 exit)과 미스매치(target_hit 0.04~0.14)"라는 confound 가
나왔다. 본 모듈은 *진입 후보는 그대로 두고 exit 구조만* 사전 정의된 보수적 후보로 비교한다.

엄격히 read-only / research-only:
- 진입 신호는 `app.research.mean_reversion_candidates` 를 *그대로 재사용* — 새 진입 0건.
- exit 후보는 사전 정의된 소수만 (grid search / best-pick 과최적화 금지).
- broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import 0건, 실주문 0건.
- auto_apply_allowed=False / applied_to_runtime=False / is_live_authorization=False /
  research_only=True / no_profit_guarantee=True 불변.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, Sequence

from app.research.mean_reversion_candidates import (
    CANDIDATES,
    IS_RESEARCH_ONLY,
    _running_hilo,
)
from app.backtest.intrabar_execution import _compute_costs
from app.backtest.strategy_council_backtest import (
    _build_input,
    _group_by_day,
    load_ohlcv_from_csv,
)
from app.system.final_multi_strategy_backtest import (
    _COST,
    _ROUND_TRIP_BPS,
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
from app.system.mean_reversion_strategy import _cost_verdict, _symbol_split
from app.system.strategy_edge_redesign import (
    _day_regime_after,
    _oos_split,
    _rolling,
    _time_bucket,
)

# 사전 정의 exit 후보 (grid search 아님 — 보수적 소수).
_EXIT_PLANS = [
    "EXISTING_TREND",       # baseline: -1.0% / +1.5% / 30m (추세형)
    "SMALL_T30_S60",        # A: +30bps / -60bps / 30m
    "SMALL_T50_S80",        # A: +50bps / -80bps / 30m
    "VWAP_REVERSION",       # B: target=VWAP / -70bps / 20m
    "RANGE_MID",            # C: target=range mid / stop=range low / 30m
    "TIME_10",              # D: 10분 시간청산
    "TIME_15",              # D: 15분 시간청산
    "BREAKEVEN_PROTECT",    # E: +30bps 도달 후 stop→breakeven, target +50bps
    "PARTIAL_SMALL",        # F: +30bps 절반청산, 나머지 VWAP/시간
    "NO_STOP_TIME_10",      # G: 고정 stop 없이 10분 시간청산 (MDD 폭증 시 REJECT)
]


def _plan_prices(plan: str, ev: dict) -> dict[str, Any]:
    e = ev["entry"]
    vwap, hi, lo = ev["vwap"], ev["range_hi"], ev["range_lo"]
    vwap_tgt = vwap if (vwap and vwap > e * 1.0005) else e * 1.003
    if plan == "EXISTING_TREND":
        return dict(target_price=e * 1.015, stop_price=e * 0.99, max_hold_min=30)
    if plan == "SMALL_T30_S60":
        return dict(target_price=e * 1.003, stop_price=e * 0.994, max_hold_min=30)
    if plan == "SMALL_T50_S80":
        return dict(target_price=e * 1.005, stop_price=e * 0.992, max_hold_min=30)
    if plan == "VWAP_REVERSION":
        return dict(target_price=vwap_tgt, stop_price=e * 0.993, max_hold_min=20)
    if plan == "RANGE_MID":
        mid = (hi + lo) / 2 if hi > lo else e * 1.003
        tgt = mid if mid > e * 1.0005 else e * 1.003
        stp = lo if (lo and lo < e * 0.9995) else e * 0.992
        return dict(target_price=tgt, stop_price=stp, max_hold_min=30)
    if plan == "TIME_10":
        return dict(target_price=e * 1.05, stop_price=e * 0.95, max_hold_min=10)
    if plan == "TIME_15":
        return dict(target_price=e * 1.05, stop_price=e * 0.95, max_hold_min=15)
    if plan == "BREAKEVEN_PROTECT":
        return dict(target_price=e * 1.005, stop_price=e * 0.992, max_hold_min=30,
                    breakeven_at_bps=30)
    if plan == "PARTIAL_SMALL":
        return dict(target_price=vwap_tgt, stop_price=e * 0.992, max_hold_min=30,
                    partial_at_bps=30)
    if plan == "NO_STOP_TIME_10":
        return dict(target_price=e * 1.05, stop_price=e * 0.90, max_hold_min=10)
    raise ValueError(plan)


def simulate_exit(entry, et, bars1, *, target_price, stop_price, max_hold_min,
                  breakeven_at_bps=None, partial_at_bps=None, partial_frac=0.5
                  ) -> dict | None:
    """1분봉 intrabar replay 로 mean-reversion exit 체결 — net/gross/slip5/reason/hold."""
    if entry <= 0 or et is None or not bars1:
        return None
    end = et + timedelta(minutes=max_hold_min)
    win = [b for b in bars1 if b.get("ts") and et <= b["ts"] <= end]
    if not win:
        return None
    stop = stop_price
    took_partial = False
    partial_px = entry * (1 + (partial_at_bps or 0) / 1e4)
    exit_px = win[-1]["close"]
    exit_ts = win[-1]["ts"]
    reason = "TIME"
    for b in win:
        if breakeven_at_bps is not None and b["high"] >= entry * (1 + breakeven_at_bps / 1e4):
            stop = max(stop, entry)
        if partial_at_bps is not None and not took_partial and b["high"] >= partial_px:
            took_partial = True
        if b["low"] <= stop:
            exit_px, exit_ts = stop, b["ts"]
            reason = "BREAKEVEN" if (stop >= entry and breakeven_at_bps) else "STOP"
            break
        if b["high"] >= target_price:
            exit_px, exit_ts = target_price, b["ts"]
            reason = "TARGET"
            break
    if took_partial:
        g1, _c1, s1, _t1, n1 = _compute_costs("BUY", entry, partial_px, _COST)
        g2, _c2, s2, _t2, n2 = _compute_costs("BUY", entry, exit_px, _COST)
        gross = partial_frac * g1 + (1 - partial_frac) * g2
        slip = partial_frac * s1 + (1 - partial_frac) * s2
        net = partial_frac * n1 + (1 - partial_frac) * n2
        reason = "PARTIAL"
    else:
        gross, _c, slip, _t, net = _compute_costs("BUY", entry, exit_px, _COST)
    hold = max(0.0, (exit_ts - et).total_seconds() / 60.0) if exit_ts else 0.0
    return {"net": net, "gross": gross, "slip5": slip, "exit_reason": reason, "hold": hold}


# ─────────────────────────────────────────────────────────────────────────────
# 진입 이벤트 수집 (entry 그대로, exit 계산용 컨텍스트만 저장)
# ─────────────────────────────────────────────────────────────────────────────


def _collect_events(one_dir: Path, five_dir: Path, symbols):
    five_map, one_map = _scan(five_dir), _scan(one_dir)
    syms = list(symbols) if symbols else sorted(set(five_map) & set(one_map))
    events: dict[str, list[dict]] = {k: [] for k in CANDIDATES}
    one_by_sym_day: dict[tuple, list[dict]] = {}
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
            one_by_sym_day[(sym, day)] = d1
            regime = _day_regime_after(day_bars)
            gap = ((day_bars[0].open - prev_close) / prev_close) if prev_close else None
            avg_vol = (sum(b.volume for b in day_bars) / len(day_bars)) or 1.0
            done_c: set[str] = set()
            for i in range(len(day_bars)):
                mi = _build_input(day_bars, i, opening_range_bars=6,
                                  recent_closes_window=5, prev_close=prev_close)
                b = day_bars[i]
                ve = (b.volume / avg_vol) if avg_vol else 1.0
                hi, lo = _running_hilo(day_bars, i)
                for cname, cfn in CANDIDATES.items():
                    if cname in done_c:
                        continue
                    if cfn(mi, ve=ve, gap=gap, day_bars=day_bars, i=i) and b.close > 0:
                        events[cname].append({
                            "symbol": sym, "day": day, "entry": b.close, "et": b.timestamp,
                            "vwap": mi.vwap, "range_hi": hi, "range_lo": lo,
                            "group": group, "regime": regime,
                            "time_bucket": _time_bucket(b.timestamp)})
                        done_c.add(cname)
            prev_close = day_bars[-1].close

    return events, one_by_sym_day, excluded, syms


def _apply_plan(events: list[dict], plan: str, one_by_sym_day: dict) -> list[dict]:
    trades = []
    for ev in events:
        d1 = one_by_sym_day.get((ev["symbol"], ev["day"]))
        if not d1:
            continue
        res = simulate_exit(ev["entry"], ev["et"], d1, **_plan_prices(plan, ev))
        if res is None:
            continue
        trades.append({**res, "day": ev["day"], "symbol": ev["symbol"],
                       "entry": ev["entry"], "group": ev["group"],
                       "regime": ev["regime"], "time_bucket": ev["time_bucket"],
                       "stop_first": res["exit_reason"] in ("STOP", "BREAKEVEN"),
                       "target_hit": res["exit_reason"] in ("TARGET", "PARTIAL"),
                       "time_exit": res["exit_reason"] == "TIME",
                       "low_liq": False, "net_edge_bps": 0.0})
    return trades


def _combo_metrics(trades: list[dict], baseline_mdd: float | None) -> dict[str, Any]:
    m = _metrics(trades, "net")
    n = m["trade_count"]
    if n == 0:
        return {"trade_count": 0, "net_pf": None, "verdict": "REJECT"}
    gross_pf = _pf([t["gross"] for t in trades])
    stress = _slippage_stress(trades)
    oos = _oos_split(trades)
    roll = _rolling(trades)
    ssplit = _symbol_split(trades, sorted({t["symbol"] for t in trades}))
    tgt = sum(1 for t in trades if t["target_hit"])
    stp = sum(1 for t in trades if t["stop_first"])
    tex = sum(1 for t in trades if t["time_exit"])
    mdd_improved = (baseline_mdd is not None and m["mdd_pct"] is not None
                    and m["mdd_pct"] < baseline_mdd)
    return {
        "trade_count": n, "net_pf": m["profit_factor"], "gross_pf": gross_pf,
        "return_pct": m["return_pct"], "mdd_pct": m["mdd_pct"],
        "mdd_improved_vs_existing": mdd_improved,
        "expectancy_bps": m["expectancy_bps"], "win_rate": m["win_rate"],
        "payoff_ratio": m["payoff_ratio"], "avg_hold_minutes": m["avg_hold_minutes"],
        "target_hit_ratio": round(tgt / n, 3), "stop_first_ratio": round(stp / n, 3),
        "time_exit_ratio": round(tex / n, 3),
        "slippage_stress": stress,
        "oos_pf": oos.get("oos_pf"), "oos_train_pf": oos.get("train_pf"),
        "rolling_median_pf": roll.get("oos_pf_median"),
        "rolling_positive_ratio": roll.get("positive_window_ratio"),
        "symbol_split": ssplit,
        "cost_verdict": _cost_verdict(gross_pf, m["profit_factor"], stress),
    }


def _combo_verdict(c: dict) -> str:
    if not c.get("trade_count"):
        return "REJECT"
    net = c.get("net_pf") or 0
    oos = c.get("oos_pf") or 0
    s10 = (c.get("slippage_stress") or {}).get("10.0bps") or 0
    ss = c.get("symbol_split") or {}
    both_pos = (ss.get("even_pf") or 0) > 0 and (ss.get("odd_pf") or 0) > 0
    if net < 1.0:
        return "REJECT"
    if oos >= 1.15 and c.get("mdd_improved_vs_existing") and (c.get("expectancy_bps") or 0) > 0 \
            and s10 >= 1.0 and both_pos and c["trade_count"] >= 30:
        return "EXIT_OOS_VALIDATED"
    if oos >= 1.05 and c.get("mdd_improved_vs_existing") and c["trade_count"] >= 30 \
            and (c.get("rolling_positive_ratio") or 0) >= 0.66:
        return "EXIT_CANDIDATE_FOUND"
    if net >= 1.0 and s10 < 1.0:
        return "EXIT_COST_FRAGILE"
    return "EXIT_COST_FRAGILE" if net >= 1.0 else "REJECT"


# ─────────────────────────────────────────────────────────────────────────────
# 오케스트레이터
# ─────────────────────────────────────────────────────────────────────────────


def run_mean_reversion_exit(*, one_min_dir: Path | None = None,
                            five_min_dir: Path | None = None,
                            symbols: Sequence[str] | None = None) -> dict[str, Any]:
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    events, one_by_sym_day, excluded, syms = _collect_events(one_dir, five_dir, symbols)

    total = sum(len(v) for v in events.values())
    if total < 10:
        return _empty(total, syms)

    matrix: dict[str, dict[str, Any]] = {}
    best_per_entry: dict[str, Any] = {}
    for cname, evs in events.items():
        if not evs:
            matrix[cname] = {}
            continue
        # baseline(EXISTING_TREND) mdd for improvement 비교.
        base_trades = _apply_plan(evs, "EXISTING_TREND", one_by_sym_day)
        base_mdd = _metrics(base_trades, "net")["mdd_pct"]
        row: dict[str, Any] = {}
        for plan in _EXIT_PLANS:
            trades = _apply_plan(evs, plan, one_by_sym_day)
            c = _combo_metrics(trades, base_mdd)
            c["verdict"] = _combo_verdict(c)
            row[plan] = c
        matrix[cname] = row
        # 이 entry 의 best exit (net_pf 기준, EXISTING 제외).
        ranked = sorted(
            ((p, v.get("net_pf") or 0) for p, v in row.items() if p != "EXISTING_TREND"),
            key=lambda kv: kv[1], reverse=True)
        if ranked:
            bp = ranked[0][0]
            best_per_entry[cname] = {"exit": bp, "net_pf": row[bp].get("net_pf"),
                                     "oos_pf": row[bp].get("oos_pf"),
                                     "verdict": row[bp].get("verdict")}

    # 전체 verdict.
    all_combos = [(e, p, v) for e, row in matrix.items() for p, v in row.items()
                  if v.get("trade_count")]
    validated = [(e, p) for e, p, v in all_combos if v["verdict"] == "EXIT_OOS_VALIDATED"]
    candidates = [(e, p) for e, p, v in all_combos if v["verdict"] == "EXIT_CANDIDATE_FOUND"]
    fragile = [(e, p) for e, p, v in all_combos if v["verdict"] == "EXIT_COST_FRAGILE"]
    any_net_gt1 = [(e, p) for e, p, v in all_combos if (v.get("net_pf") or 0) >= 1.0]

    verdict, conclusion = _overall_verdict(validated, candidates, fragile, any_net_gt1,
                                           all_combos)

    return {
        "available": True,
        "mode": "mean_reversion_exit_research",
        "is_research_only": IS_RESEARCH_ONLY,
        "data": {"symbols": syms, "excluded": excluded,
                 "entry_event_counts": {c: len(v) for c, v in events.items()}},
        "cost_model": {"round_trip_bps": _ROUND_TRIP_BPS},
        "exit_plans": _EXIT_PLANS,
        "matrix": matrix,
        "best_exit_per_entry": best_per_entry,
        "survivors": [f"{e}+{p}" for e, p in (validated + candidates)],
        "fragile": [f"{e}+{p}" for e, p in fragile],
        "verdict": verdict, "conclusion": conclusion,
        "next_steps": [
            "REJECT 조합은 폐기 — exit 재설계로도 비용 후 PF<1",
            "EXIT_CANDIDATE/EXIT_OOS_VALIDATED 는 별도 PR + 추가 OOS + (조건 충족 시) paper "
            "rehearsal 후보일 뿐, 자동 적용 금지",
            "어떤 entry/exit 조합도 런타임 전략으로 등록/적용되지 않음",
        ],
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. 결과가 좋아도 paper rehearsal "
                      "후보일 뿐이며, exit 구조/진입 후보는 런타임에 자동 적용되지 않습니다. "
                      "수익을 보장하지 않습니다.",
    }


def _overall_verdict(validated, candidates, fragile, any_net_gt1, all_combos):
    if validated:
        return ("EXIT_OOS_VALIDATED",
                [f"OOS PF≥1.15 + MDD 개선 + 10bps 유지 + 종목 분산 조합: {validated} — 단 "
                 "자동 적용 금지, paper rehearsal 은 별도 승인 필요."])
    if candidates:
        return ("EXIT_CANDIDATE_FOUND",
                [f"OOS PF>1.05 + MDD 개선 + rolling 2/3+ 조합: {candidates} — 추가 검증 필요, "
                 "자동 적용 금지."])
    if fragile:
        return ("EXIT_COST_FRAGILE",
                [f"net PF>1 이나 10bps slippage 에서 붕괴/OOS 불안정: {fragile} — 비용 취약, "
                 "실전성 없음."])
    if any_net_gt1:
        return ("PAPER_REHEARSAL_STILL_NOT_READY",
                [f"일부 조합 net PF≥1 이나 표본/비용 민감성 미충족: {any_net_gt1} — paper "
                 "rehearsal 준비 안 됨."])
    # 비용 floor(왕복 31bps) > 소형 목표(30bps) → 구조적 손실. 정직하게 부기.
    floor_note = (f" 핵심: 왕복 비용 {round(_ROUND_TRIP_BPS, 0):.0f}bps 가 소형 목표(+30bps)보다 "
                  "커서 SMALL_T30 은 target 도달조차 net 손실(PF≈0) — 대형주 5분봉의 장중 "
                  "되돌림 크기가 비용 floor 를 넘지 못함. 시간청산(TIME)이 상대적으로 덜 나쁘나 "
                  "(PF~0.4-0.6) 기저 방향성 엣지가 없어 여전히 PF<1.")
    return ("EXIT_REDESIGN_REJECTED",
            ["모든 entry×exit 조합이 비용 후 PF<1 — 평균회귀에 적합한 exit 구조로도 엣지 회복 "
             "실패. 진입 가설 자체에 엣지가 없어 exit 재설계로 구제 불가." + floor_note])


def _empty(n, syms):
    return {"available": False, "verdict": "BACKTEST_INFRA_INCOMPLETE",
            "is_research_only": IS_RESEARCH_ONLY,
            "reason": "INSUFFICIENT_TRADES", "trade_count": n, "symbols": syms,
            "auto_apply_allowed": False, "applied_to_runtime": False,
            "is_live_authorization": False, "no_profit_guarantee": True,
            "contains_secret": False,
            "disclaimer": "연구용 백테스트 — 거래 표본 부족. 자동 적용 안 됨, 실전매매 권고 아님."}
