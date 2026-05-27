"""Composite ranking 재설계 비교 연구 (CHECKLIST-04, 백테스트 전용).

실데이터 60일·1분봉 aligned 거래에서 9개 *고정* ranking 후보(미래수익 최적화 금지)를
동일 조건으로 비교하고 OOS(시간 분할)로 검증한다. 결과는 *후보*일 뿐 — 런타임/전략/
ranking weight 에 자동 적용하지 않는다.

본 모듈은 broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import
0건, 실주문 0건, read-only. is_live_authorization=False, auto_apply_allowed=False,
no_profit_guarantee=True.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable, Sequence

from app.backtest.intrabar_execution import CostModel, simulate_intrabar_execution
from app.backtest.signal_ranking import SignalCandidate, compute_composite_score
from app.system.intrabar_realdata_backtest import (
    FIVE_MIN_DIR_DEFAULT,
    ONE_MIN_DIR_DEFAULT,
    _SYMBOL_GROUP,
    _generate_orb_trade,
    _group_by_day,
    _load_csv,
    _scan,
    _trade_metrics,
)

_SLOTS = 5
_COST = CostModel(slippage_bps=5.0)
_COST_BPS = _COST.commission_bps * 2 + _COST.tax_bps + _COST.slippage_bps * 2  # 26bps


def collect_aligned_trades(one_dir: Path, five_dir: Path,
                           symbols: Sequence[str] | None = None) -> list[dict]:
    """1분봉 보유 날짜로 제한된 ORB 거래 + feature + 1분봉 net_pnl 수집."""
    five_map = _scan(five_dir)
    one_map = _scan(one_dir)
    syms = list(symbols) if symbols else sorted(set(five_map) & set(one_map))
    out: list[dict] = []
    for sym in syms:
        if sym not in five_map or sym not in one_map:
            continue
        days5 = _group_by_day(_load_csv(five_map[sym]))
        days1 = _group_by_day(_load_csv(one_map[sym]))
        group = _SYMBOL_GROUP.get(sym, "OTHER")
        for day, d5 in sorted(days5.items()):
            if day not in days1:
                continue
            t = _generate_orb_trade(d5, sym)
            if t is None:
                continue
            r = simulate_intrabar_execution(
                side="BUY", entry_time=t["entry_time"], entry_price=t["entry_price"],
                stop_price=t["stop_price"], target_price=t["target_price"],
                max_hold_minutes=30, bars_5m=d5, bars_1m=days1[day], cost=_COST)
            ve = t["volume_expansion"]
            edge_bps = abs(t["target_price"] - t["entry_price"]) / max(1.0, t["entry_price"]) * 1e4
            low_liq = ve < 0.8
            out.append({
                "day": day, "symbol": sym, "ts": t["entry_time"], "regime": t["regime"],
                "group": group, "entry": t["entry_price"], "vol_exp": ve, "low_liq": low_liq,
                "pnl1": r.net_pnl, "win": 1 if r.net_pnl > 0 else 0,
                "confidence": min(0.95, 0.5 + ve * 0.1),
                "quality": min(100.0, 50.0 + ve * 15.0),
                "edge_bps": edge_bps, "net_edge_bps": edge_bps - _COST_BPS,
                "liquidity": 40.0 if low_liq else 70.0,
            })
    return out


def _candidate_for(t: dict) -> SignalCandidate:
    return SignalCandidate(
        symbol=t["symbol"], strategy="ORB", timestamp=t["ts"], side="BUY",
        confidence=t["confidence"], quality_score=t["quality"],
        expected_move_bps=t["edge_bps"], estimated_cost_bps=_COST_BPS,
        volume_expansion=t["vol_exp"], liquidity_score=t["liquidity"],
        regime_label=t["regime"], symbol_group=t["group"])


# ── 9개 고정 ranking 후보 (selector: day_trades → 선택 list, ≤ slots) ──

def _earliest_first(day_trades, slots):
    return sorted(day_trades, key=lambda t: str(t["ts"]))[:slots]


def _current_composite(day_trades, slots):
    return sorted(day_trades, key=lambda t: compute_composite_score(_candidate_for(t)),
                  reverse=True)[:slots]


def _cost_edge_first(day_trades, slots):
    elig = [t for t in day_trades if t["net_edge_bps"] > 0 and not t["low_liq"]]
    return sorted(elig, key=lambda t: t["net_edge_bps"], reverse=True)[:slots]


def _risk_first(day_trades, slots):
    # 변동성 회피: HIGH_VOLATILITY/DOWNTREND + 저유동성 제외, 나머지 earliest.
    elig = [t for t in day_trades
            if t["regime"] not in ("HIGH_VOLATILITY", "DOWNTREND") and not t["low_liq"]]
    return sorted(elig, key=lambda t: str(t["ts"]))[:slots]


def _regime_aware(day_trades, slots):
    # SIDEWAYS/DOWNTREND 제외, UPTREND 우선 + HIGH_VOLATILITY 일부 허용(점수순).
    elig = [t for t in day_trades if t["regime"] in ("UPTREND", "HIGH_VOLATILITY")]
    return sorted(elig, key=lambda t: compute_composite_score(_candidate_for(t)),
                  reverse=True)[:slots]


def _liquidity_first(day_trades, slots):
    elig = [t for t in day_trades if not t["low_liq"]]
    return sorted(elig, key=lambda t: (t["liquidity"], t["vol_exp"]), reverse=True)[:slots]


def _simple_filter_then_earliest(day_trades, slots):
    elig = [t for t in day_trades if t["net_edge_bps"] > 0 and not t["low_liq"]]
    return sorted(elig, key=lambda t: str(t["ts"]))[:slots]


def _top_score_with_min_edge(day_trades, slots):
    elig = [t for t in day_trades if t["net_edge_bps"] >= 0]
    return sorted(elig, key=lambda t: compute_composite_score(_candidate_for(t)),
                  reverse=True)[:slots]


def _no_ranking_risk_veto_only(day_trades, slots):
    # 위험(저유동성)만 제거, 순서 재배열 없음 → earliest.
    elig = [t for t in day_trades if not t["low_liq"]]
    return sorted(elig, key=lambda t: str(t["ts"]))[:slots]


RANKING_CANDIDATES: dict[str, Callable] = {
    "EARLIEST_FIRST": _earliest_first,
    "CURRENT_COMPOSITE": _current_composite,
    "COST_EDGE_FIRST": _cost_edge_first,
    "RISK_FIRST": _risk_first,
    "REGIME_AWARE": _regime_aware,
    "LIQUIDITY_FIRST": _liquidity_first,
    "SIMPLE_FILTER_THEN_EARLIEST": _simple_filter_then_earliest,
    "TOP_SCORE_WITH_MIN_EDGE": _top_score_with_min_edge,
    "NO_RANKING_RISK_VETO_ONLY": _no_ranking_risk_veto_only,
}
# 순위 *재배열* 이 아니라 필터 위주 후보 (filter-only 판정용).
_FILTER_ONLY = {"SIMPLE_FILTER_THEN_EARLIEST", "NO_RANKING_RISK_VETO_ONLY",
                "EARLIEST_FIRST"}
# regime label 은 *일중 종가/범위*(09:30 진입 시점엔 미지)로 계산 → look-ahead 위험.
# regime 을 쓰는 후보는 검증(VALIDATED/CANDIDATE)에서 제외하고 경고로만 표시.
_LOOK_AHEAD = {"CURRENT_COMPOSITE", "REGIME_AWARE", "RISK_FIRST",
               "TOP_SCORE_WITH_MIN_EDGE"}
# look-ahead 없는 *순위 재배열* 후보 (filter 가 아니라 reorder).
_CLEAN_REORDER = {"COST_EDGE_FIRST", "LIQUIDITY_FIRST"}


def _run_candidate(trades: list[dict], selector, days: set[str] | None,
                   slip_bps: float = 5.0) -> dict:
    """후보를 일자별 슬롯 선택 후 집계. days 지정 시 그 구간만(OOS)."""
    by_day: dict[str, list[dict]] = {}
    for t in trades:
        if days is not None and t["day"] not in days:
            continue
        by_day.setdefault(t["day"], []).append(t)
    sel: list[dict] = []
    for d in by_day.values():
        sel += selector(d, _SLOTS)
    # slippage 조정: net_pnl 에서 slippage 항만 비례 변경 (5bps 기준).
    pnls, ents = [], []
    for t in sel:
        adj = t["pnl1"] + t["entry"] * 2 * (5.0 / 1e4) - t["entry"] * 2 * (slip_bps / 1e4)
        pnls.append(adj)
        ents.append(t["entry"])
    m = _trade_metrics(pnls, ents)
    m["selected_count"] = len(sel)
    m["selected_symbols"] = sorted({t["symbol"] for t in sel})
    return m


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)


def _attribution(trades: list[dict]) -> dict[str, Any]:
    """feature vs net_pnl / win 상관 — composite 점수 요소가 실제 수익과 연관 있나."""
    pnls = [t["pnl1"] for t in trades]
    wins = [float(t["win"]) for t in trades]
    feats = {
        "net_edge_bps": [t["net_edge_bps"] for t in trades],
        "confidence": [t["confidence"] for t in trades],
        "quality": [t["quality"] for t in trades],
        "volume_expansion": [t["vol_exp"] for t in trades],
        "liquidity": [t["liquidity"] for t in trades],
        "composite_score": [compute_composite_score(_candidate_for(t)) for t in trades],
    }
    return {f: {"corr_vs_net_pnl": _pearson(v, pnls), "corr_vs_win": _pearson(v, wins)}
            for f, v in feats.items()}


def run_ranking_redesign(*, one_min_dir: Path | None = None,
                         five_min_dir: Path | None = None,
                         symbols: Sequence[str] | None = None,
                         oos_test_fraction: float = 0.34) -> dict[str, Any]:
    """9 후보 비교 + OOS + attribution. read-only, 자동 적용 0건."""
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    trades = collect_aligned_trades(one_dir, five_dir, symbols)

    if len(trades) < 10:
        return {"available": False, "verdict": "RANKING_REJECTED",
                "reason": "INSUFFICIENT_TRADES", "trade_count": len(trades),
                "auto_apply_allowed": False, "is_live_authorization": False,
                "no_profit_guarantee": True, "contains_secret": False,
                "disclaimer": "연구용 백테스트 — 거래 표본 부족. 실전매매 권고 아님."}

    days = sorted({t["day"] for t in trades})
    n_test = max(1, int(len(days) * oos_test_fraction))
    train_days = set(days[:-n_test])
    oos_days = set(days[-n_test:])

    full = {name: _run_candidate(trades, sel, None)
            for name, sel in RANKING_CANDIDATES.items()}
    oos = {name: _run_candidate(trades, sel, oos_days)
           for name, sel in RANKING_CANDIDATES.items()}
    stress = {name: {f"{slip}bps": _run_candidate(trades, sel, oos_days, slip_bps=slip)
                     .get("profit_factor")
                     for slip in (5.0, 7.0, 10.0, 15.0)}
              for name, sel in RANKING_CANDIDATES.items()}

    base = oos["EARLIEST_FIRST"]
    verdict, best, recommendation = _verdict(oos, base, full, stress)

    return {
        "available": True,
        "mode": "ranking_redesign_research",
        "data": {"trade_count": len(trades), "trading_days": len(days),
                 "train_days": len(train_days), "oos_days": len(oos_days),
                 "symbols": sorted({t["symbol"] for t in trades})},
        "current_composite_failure": {
            "oos_pf": oos["CURRENT_COMPOSITE"]["profit_factor"],
            "oos_mdd_pct": oos["CURRENT_COMPOSITE"]["mdd_pct"],
            "vs_earliest_pf": base["profit_factor"],
            "note": "기존 composite 가 earliest-first 대비 PF↓/MDD↑ (재현)",
        },
        "feature_attribution": _attribution(trades),
        "candidates": sorted(RANKING_CANDIDATES.keys()),
        "look_ahead_candidates": sorted(_LOOK_AHEAD),
        "look_ahead_warning": ("regime label 은 일중 종가/범위(09:30 진입 시점엔 미지)로 "
                               "계산되어 look-ahead 위험 — regime 기반 후보(CURRENT_COMPOSITE/"
                               "REGIME_AWARE/RISK_FIRST/TOP_SCORE)는 검증에서 제외하고 경고로만 표시."),
        "full_period": full,
        "oos": oos,
        "slippage_stress_oos": stress,
        "earliest_first_baseline": {"full": full["EARLIEST_FIRST"], "oos": base},
        "best_candidate": best,
        "verdict": verdict,
        "recommendation": recommendation,
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. 어떤 후보도 런타임/전략에 "
                      "자동 적용되지 않습니다. 수익을 보장하지 않습니다.",
        "next_steps": [
            "OOS 검증 후보가 있어도 별도 승인 + 추가 기간 검증 후에만 반영",
            "ORB 전략 자체가 비용 후 PF<1 이면 ranking 이전에 전략 개선 우선",
        ],
    }


def _improved(cand: dict, base: dict) -> tuple[bool, bool, bool]:
    """(pf_up, mdd_down, exp_up) vs baseline. None 은 미개선 취급."""
    def gt(a, b):
        return a is not None and b is not None and a > b
    def lt(a, b):
        return a is not None and b is not None and a < b
    return (gt(cand["profit_factor"], base["profit_factor"]),
            lt(cand["mdd_pct"], base["mdd_pct"]),
            gt(cand["expectancy"], base["expectancy"]))


def _verdict(oos: dict, base: dict, full: dict, stress: dict) -> tuple[str, dict, str]:
    """OOS 기준 판정. look-ahead 후보(regime)는 검증 제외. 자동 적용 안 함.

    핵심: ① regime 기반 후보는 look-ahead 라 신뢰 불가(경고만). ② *clean reorder*
    후보가 filter-only 보다 나아야 reorder 가치 인정. ③ 그렇지 않고 filter 가 baseline
    이상이면 FILTER_ONLY 권고.
    """
    base_pf = base["profit_factor"] or 0

    def stress_ok(name: str) -> bool:
        # 10bps 슬리피지에서도 OOS PF 가 baseline(5bps) 이상이면 robust.
        p10 = (stress.get(name) or {}).get("10.0bps")
        return p10 is not None and p10 >= base_pf

    def symbol_ok(m: dict) -> bool:
        return len(m.get("selected_symbols", [])) >= 5

    # clean reorder 후보 중 완전검증/후보.
    validated, candidates = [], []
    for name in _CLEAN_REORDER:
        m = oos.get(name)
        if not m:
            continue
        pf_up, mdd_down, exp_up = _improved(m, base)
        enough = (m["trade_count"] or 0) >= 30
        if pf_up and mdd_down and exp_up and enough and stress_ok(name) and symbol_ok(m):
            validated.append((name, m))
        elif (pf_up or mdd_down) and enough:
            candidates.append((name, m))

    # filter-only 최선 (clean).
    filt = {n: oos[n] for n in _FILTER_ONLY if n in oos and n != "EARLIEST_FIRST"}
    best_filter_name = max(filt, key=lambda n: (filt[n]["profit_factor"] or 0), default=None)
    best_filter_pf = (filt[best_filter_name]["profit_factor"] or 0) if best_filter_name else 0

    if validated:
        b = max(validated, key=lambda r: (r[1]["profit_factor"] or 0))
        # reorder 가 filter-only 보다도 나아야 reorder 자체 가치 인정.
        if (b[1]["profit_factor"] or 0) > best_filter_pf:
            return ("RANKING_OOS_VALIDATED", {"name": b[0], **b[1]},
                    f"{b[0]}(look-ahead 없음)가 OOS PF/MDD/expectancy 모두 개선 + "
                    "slippage/종목 robust + filter-only 초과. 단 21일 OOS·자동 적용 금지, "
                    "별도 승인 + 추가 기간 검증 필요.")
    if candidates:
        b = max(candidates, key=lambda r: (r[1]["profit_factor"] or 0))
        if (b[1]["profit_factor"] or 0) > best_filter_pf:
            return ("RANKING_CANDIDATE_FOUND", {"name": b[0], **b[1]},
                    f"{b[0]} OOS 개선 후보(look-ahead 없음). 추가 검증 필요, 자동 적용 금지.")
    if best_filter_name and best_filter_pf >= base_pf:
        return ("RANKING_FILTER_ONLY_RECOMMENDED",
                {"name": best_filter_name, **filt[best_filter_name]},
                f"순위 *재배열*은 가치 없음(또는 look-ahead) — 나쁜 신호 제거(필터)만 "
                f"baseline 이상({best_filter_name} PF {best_filter_pf} ≥ earliest {base_pf}). "
                "ranking 비활성화 + RISK_FILTER_ONLY 권고. 자동 적용 금지.")
    return ("RANKING_REJECTED", {"name": "EARLIEST_FIRST", **base},
            "look-ahead 없는 어떤 후보도 OOS 에서 earliest-first 를 개선 못함 — "
            "ranking 비활성화 권고. 자동 적용 금지.")
