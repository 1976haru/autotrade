"""실제 1분봉 기반 intrabar 백테스트 오케스트레이터 (CHECKLIST-04 P2, 백테스트 전용).

실제 1분봉(있으면) + 5분봉으로 (A) 5분봉 vs 1분봉 replay 체결 비교, (B) earliest-first
vs composite ranking 비교, (C) 비용/슬리피지 stress, (D) 유동성 penalty, (E) 장세/종목군
attribution, (F) trade replay sample 을 산출한다.

핵심: 좋은 결과를 만들기보다 *가짜 edge 제거*. 1분봉이 없으면 결론을 내지 않고
REALDATA_BACKTEST_NOT_READY + confidence LOW 로 정직하게 보고한다.

본 모듈은 broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import
0건, 실주문 0건, read-only. 연구용 백테스트 — is_live_authorization=False,
no_profit_guarantee=True.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.backtest.intrabar_execution import CostModel, simulate_intrabar_execution
from app.backtest.signal_ranking import (
    SignalCandidate,
    compute_composite_score,
    earliest_first,
    rank_signals,
)


def _four_way(trade_recs: list[dict], *, max_slots: int = 5) -> dict[str, Any]:
    """5m/1m × earliest-first/composite 4-way 비교 — 일자별 슬롯 선택 후 집계.

    earliest: 같은 날 timestamp 빠른 순 max_slots. composite: score 높은 순.
    각 조합에서 선택된 거래의 net_pnl 합계 metric. (장기 수익성 아님 — 민감도.)
    """
    by_day: dict[str, list[dict]] = {}
    for r in trade_recs:
        by_day.setdefault(r["day"], []).append(r)

    def _select(recs: list[dict], key, reverse: bool) -> list[dict]:
        return sorted(recs, key=key, reverse=reverse)[:max_slots]

    sel_ef: list[dict] = []
    sel_comp: list[dict] = []
    for recs in by_day.values():
        sel_ef += _select(recs, key=lambda r: str(r["ts"]), reverse=False)
        sel_comp += _select(recs, key=lambda r: r["score"], reverse=True)

    def _m(recs, field):
        pnls = [r[field] for r in recs]
        ent = [r["entry"] for r in recs]
        return _trade_metrics(pnls, ent)

    return {
        "basic_5m_earliest_first": _m(sel_ef, "pnl5"),
        "basic_5m_composite": _m(sel_comp, "pnl5"),
        "intrabar_1m_earliest_first": _m(sel_ef, "pnl1"),
        "intrabar_1m_composite": _m(sel_comp, "pnl1"),
        "selected_count_earliest_first": len(sel_ef),
        "selected_count_composite": len(sel_comp),
    }

FIVE_MIN_DIR_DEFAULT = Path("data/market/intraday_ohlcv/kis_6m")
ONE_MIN_DIR_DEFAULT = Path("data/market/robust_intraday_1m_subset")
_SYM_RE = re.compile(r"^(\d{6})")
_DEFAULT_SLIPPAGES = (5.0, 7.0, 10.0, 15.0)

# 종목군 (대표 10종목 + 기본).
_SYMBOL_GROUP = {
    "005930": "LARGE_CAP", "000660": "LARGE_CAP", "005380": "LARGE_CAP",
    "000270": "LARGE_CAP", "012330": "LARGE_CAP", "006400": "LARGE_CAP",
    "051910": "LARGE_CAP", "035420": "LARGE_CAP",
    "042700": "MID_CAP", "066570": "MID_CAP",
}


def _parse_ts(v: Any) -> datetime | None:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _bar(b: Any) -> dict[str, Any]:
    g = b.get if isinstance(b, dict) else (lambda k, d=None: getattr(b, k, d))
    return {"ts": _parse_ts(g("timestamp", g("ts"))),
            "open": float(g("open", 0) or 0), "high": float(g("high", 0) or 0),
            "low": float(g("low", 0) or 0), "close": float(g("close", 0) or 0),
            "volume": float(g("volume", 0) or 0)}


def _load_csv(path: Path) -> list[dict]:
    try:
        from app.market_data.intraday_ohlcv import load_intraday_csv
        bars, _q = load_intraday_csv(str(path))
        return [_bar(b) for b in bars]
    except Exception:  # noqa: BLE001
        return []


def _scan(dir_: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if dir_.exists():
        for p in dir_.glob("*.csv"):
            m = _SYM_RE.match(p.stem)
            if m:
                out.setdefault(m.group(1), p)
    return out


def _group_by_day(bars: list[dict]) -> dict[str, list[dict]]:
    days: dict[str, list[dict]] = {}
    for b in bars:
        if b["ts"] is None:
            continue
        days.setdefault(b["ts"].date().isoformat(), []).append(b)
    for d in days.values():
        d.sort(key=lambda x: x["ts"])
    return days


def _day_regime(day_bars: list[dict]) -> str:
    if len(day_bars) < 2:
        return "SIDEWAYS"
    o, c = day_bars[0]["open"], day_bars[-1]["close"]
    if o <= 0:
        return "SIDEWAYS"
    ret = (c - o) / o
    hi = max(b["high"] for b in day_bars)
    lo = min(b["low"] for b in day_bars)
    rng = (hi - lo) / o if o else 0
    if rng > 0.06:
        return "HIGH_VOLATILITY"
    if ret > 0.02:
        return "UPTREND"
    if ret < -0.02:
        return "DOWNTREND"
    return "SIDEWAYS"


def _generate_orb_trade(day5: list[dict], symbol: str) -> dict | None:
    """결정론적 ORB harness — 첫 6봉(09:00~09:30) range 돌파 1회. 연구용."""
    if len(day5) < 8:
        return None
    opening = day5[:6]
    or_high = max(b["high"] for b in opening)
    or_low = min(b["low"] for b in opening)
    if or_high <= or_low:
        return None
    avg_vol = (sum(b["volume"] for b in opening) / len(opening)) or 1.0
    for b in day5[6:]:
        if b["high"] >= or_high * 1.001:  # 돌파
            entry = or_high
            stop = or_low
            target = entry + (entry - or_low)  # 1:1 R
            vol_exp = b["volume"] / avg_vol if avg_vol else 1.0
            return {
                "symbol": symbol, "entry_time": b["ts"].isoformat(),
                "entry_price": entry, "stop_price": stop, "target_price": target,
                "regime": _day_regime(day5), "volume_expansion": round(vol_exp, 2),
                "entry_volume": b["volume"], "avg_open_volume": avg_vol,
            }
    return None


def _trade_metrics(net_pnls: Sequence[float], entries: Sequence[float]) -> dict:
    n = len(net_pnls)
    if n == 0:
        return {"trade_count": 0, "return_pct": None, "profit_factor": None,
                "mdd_pct": None, "win_rate": None, "expectancy": None}
    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    pf = (gp / gl) if gl > 0 else (math.inf if gp > 0 else 0.0)
    # 누적 자금곡선 (per-share 합 / 평균 entry 로 normalize → return %).
    avg_entry = (sum(entries) / len(entries)) if entries else 1.0
    cum = 0.0
    peak = 0.0
    mdd = 0.0
    for p in net_pnls:
        cum += p
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    return {
        "trade_count": n,
        "return_pct": round(sum(net_pnls) / avg_entry / n * 100.0, 4) if avg_entry else None,
        "profit_factor": (round(pf, 3) if pf != math.inf else None),
        "mdd_pct": round(mdd / avg_entry * 100.0, 4) if avg_entry else None,
        "win_rate": round(len(wins) / n, 4),
        "expectancy": round(sum(net_pnls) / n / avg_entry * 1e4, 2) if avg_entry else None,  # bps
    }


def run_realdata_backtest(
    *,
    one_min_dir: Path | None = None,
    five_min_dir: Path | None = None,
    symbols: Sequence[str] | None = None,
    slippage_stress: Sequence[float] = _DEFAULT_SLIPPAGES,
    max_hold_minutes: int = 30,
    low_volume_threshold: float = 0.8,
    align_to_1m: bool = False,
) -> dict[str, Any]:
    """실제 1분봉+5분봉으로 intrabar+ranking 백테스트. 1분봉 없으면 NOT_READY.

    align_to_1m=True: 1분봉이 있는 날짜로 5분봉 거래를 *제한* → replay coverage↑.
    (장기 수익성 아님 — 짧은 구간에서 intrabar 체결 민감도 정밀 비교용.)
    """
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT

    five_map = _scan(five_dir)
    one_map = _scan(one_dir)
    target = list(symbols) if symbols else sorted(set(five_map) | set(one_map))

    with_1m = [s for s in target if s in one_map and s in five_map]
    with_5m_only = [s for s in target if s in five_map and s not in one_map]

    # ── 데이터 없으면 NOT_READY ──
    if not with_1m:
        return _empty_report(one_dir, five_dir, target, with_5m_only)

    five_trades: list[float] = []   # net_pnl (5분봉 fallback)
    one_trades: list[float] = []    # net_pnl (1분봉 replay)
    entries: list[float] = []
    ambiguous = stop_first = replayable = 0
    conf_dist = {"HIGH": 0, "LOW": 0}
    candidates: list[SignalCandidate] = []
    replay_sample: list[dict] = []
    by_regime: dict[str, list[float]] = {}
    by_group: dict[str, list[float]] = {}
    liquidity_flagged = 0
    cost = CostModel(slippage_bps=slippage_stress[0])
    trade_recs: list[dict] = []   # 4-way 비교용 per-trade 기록.
    trading_days: set[str] = set()

    for sym in with_1m:
        bars5 = _load_csv(five_map[sym])
        bars1 = _load_csv(one_map[sym])
        days5 = _group_by_day(bars5)
        days1 = _group_by_day(bars1)
        group = _SYMBOL_GROUP.get(sym, "OTHER")
        for day, d5 in sorted(days5.items()):
            # aligned 모드: 1분봉 있는 날짜만 (replay coverage↑).
            if align_to_1m and day not in days1:
                continue
            t = _generate_orb_trade(d5, sym)
            if t is None:
                continue
            trading_days.add(day)
            d1 = days1.get(day)
            five_r = simulate_intrabar_execution(
                side="BUY", entry_time=t["entry_time"], entry_price=t["entry_price"],
                stop_price=t["stop_price"], target_price=t["target_price"],
                max_hold_minutes=max_hold_minutes, bars_5m=d5, bars_1m=None, cost=cost)
            one_r = simulate_intrabar_execution(
                side="BUY", entry_time=t["entry_time"], entry_price=t["entry_price"],
                stop_price=t["stop_price"], target_price=t["target_price"],
                max_hold_minutes=max_hold_minutes, bars_5m=d5, bars_1m=d1, cost=cost)
            five_trades.append(five_r.net_pnl)
            one_trades.append(one_r.net_pnl)
            entries.append(t["entry_price"])
            conf_dist[one_r.execution_confidence] = conf_dist.get(
                one_r.execution_confidence, 0) + 1
            if one_r.execution_source == "ONE_MINUTE_REPLAY":
                replayable += 1
            if "AMBIGUOUS" in one_r.exit_reason:
                ambiguous += 1
            if "STOP_FIRST" in one_r.exit_reason:
                stop_first += 1
            low_liq = t["volume_expansion"] < low_volume_threshold
            if low_liq:
                liquidity_flagged += 1
            by_regime.setdefault(t["regime"], []).append(one_r.net_pnl)
            by_group.setdefault(group, []).append(one_r.net_pnl)
            cand = SignalCandidate(
                symbol=sym, strategy="ORB", timestamp=t["entry_time"], side="BUY",
                confidence=min(0.95, 0.5 + t["volume_expansion"] * 0.1),
                quality_score=min(100.0, 50.0 + t["volume_expansion"] * 15.0),
                expected_move_bps=abs(t["target_price"] - t["entry_price"])
                / max(1.0, t["entry_price"]) * 1e4,
                estimated_cost_bps=cost.commission_bps * 2 + cost.tax_bps
                + cost.slippage_bps * 2,
                volume_expansion=t["volume_expansion"], liquidity_score=(40.0 if low_liq else 70.0),
                regime_label=t["regime"], symbol_group=group)
            candidates.append(cand)
            trade_recs.append({
                "day": day, "symbol": sym, "ts": t["entry_time"],
                "score": compute_composite_score(cand), "entry": t["entry_price"],
                "pnl5": five_r.net_pnl, "pnl1": one_r.net_pnl})
            if len(replay_sample) < 8:
                replay_sample.append({
                    "symbol": sym, "day": day, "strategy": "ORB",
                    "entry_price": t["entry_price"], "stop_price": t["stop_price"],
                    "target_price": t["target_price"],
                    "exit_price_1m": one_r.exit_price, "exit_reason_1m": one_r.exit_reason,
                    "exit_reason_5m": five_r.exit_reason,
                    "execution_source": one_r.execution_source,
                    "ranking_volume_expansion": t["volume_expansion"],
                    "net_pnl_1m": one_r.net_pnl, "net_pnl_5m": five_r.net_pnl,
                    "regime": t["regime"], "symbol_group": group,
                })

    five_m = _trade_metrics(five_trades, entries)
    one_m = _trade_metrics(one_trades, entries)

    # ── ranking: earliest vs composite (forward return = trade net_pnl proxy) ──
    ef = earliest_first(candidates, max_slots=5)
    ranked = rank_signals(candidates, max_slots=5, max_symbols_per_group=3)

    # ── cost/slippage stress ──
    stress = []
    base_slip_bps = slippage_stress[0]
    for slip in slippage_stress:
        # net 재계산: gross/commission/tax 동일, slippage 항만 비례 변경 (보수적 근사).
        scaled = []
        for npl, e in zip(one_trades, entries):
            base_slip = e * 2 * (base_slip_bps / 1e4)
            new_slip = e * 2 * (slip / 1e4)
            scaled.append(npl + base_slip - new_slip)
        stress.append({"slippage_bps": slip, **_trade_metrics(scaled, entries)})

    total_tr = one_m["trade_count"] or 0
    replay_cov = round(replayable / total_tr, 4) if total_tr else 0.0
    if replay_cov >= 0.60:
        confidence = "HIGH"
    elif replay_cov >= 0.30:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"   # replay coverage 부족 → 1분봉 착시 결론 약함
    verdict = _verdict(with_1m, five_m, one_m, replay_cov, ranked)

    # ── aligned 모드: 1분봉 날짜로 제한 → coverage↑, 4-way 정밀 비교 ──
    four_way = None
    n_days = len(trading_days)
    if align_to_1m:
        four_way = _four_way(trade_recs)
        # 거래일/거래수 임계 — coverage 높아도 기간 짧으면 보수적.
        if n_days < 10:
            verdict = "PERIOD_TOO_SHORT_LOW_CONFIDENCE"
            confidence = "LOW"
        elif n_days < 20:
            verdict = "ALIGNED_INTRABAR_SENSITIVITY_PARTIAL"
            confidence = "MEDIUM"
        elif n_days >= 60 and total_tr >= 100:
            verdict = "INTRABAR_REALDATA_READY"
            confidence = "HIGH"
        elif n_days >= 20 and total_tr >= 50:
            verdict = "ALIGNED_INTRABAR_SENSITIVITY_READY"
            confidence = "HIGH"
        else:
            verdict = "ALIGNED_INTRABAR_SENSITIVITY_PARTIAL"
            confidence = "MEDIUM"

    return {
        "available": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "realdata_backtest",
        "coverage": {
            "one_min_dir": str(one_dir), "five_min_dir": str(five_dir),
            "symbols_with_1m": with_1m, "symbols_with_5m_only": with_5m_only,
            "symbol_count_1m": len(with_1m),
            "replayable_trades": replayable,
            "total_trades": one_m["trade_count"],
            "replay_coverage_pct": round(replay_cov * 100.0, 2),
            "execution_confidence_distribution": conf_dist,
        },
        "execution_5m_vs_1m": {"five_minute": five_m, "intrabar_1m": one_m,
                               "pf_delta": _delta(five_m["profit_factor"], one_m["profit_factor"]),
                               "return_delta": _delta(five_m["return_pct"], one_m["return_pct"])},
        "ranking": {
            "earliest_first_selected": sorted({d["symbol"] for d in ef}),
            "composite_selected": sorted({d["symbol"] for d in ranked.selected}),
            "ranking_differs": ({d["symbol"] for d in ef}
                                != {d["symbol"] for d in ranked.selected}),
            "selected_avg_score": ranked.selected_avg_score,
            "rejected_avg_score": ranked.rejected_avg_score,
            "vetoed_count": ranked.vetoed_count,
        },
        "cost_slippage_stress": stress,
        "liquidity": {"low_volume_threshold": low_volume_threshold,
                      "liquidity_flagged_trades": liquidity_flagged},
        "regime_attribution": {k: _trade_metrics(v, entries) for k, v in by_regime.items()},
        "symbol_group_attribution": {k: _trade_metrics(v, entries) for k, v in by_group.items()},
        "ambiguous_trade_count": ambiguous,
        "conservative_stop_first_count": stop_first,
        "trade_replay_sample": replay_sample,
        "confidence_level": confidence,
        "verdict": verdict,
        "align_to_1m": align_to_1m,
        "trading_days": n_days,
        "aligned_coverage": ({
            "aligned_total_trades": total_tr,
            "one_minute_replayed_count": replayable,
            "coverage_pct": round(replay_cov * 100.0, 2),
            "ambiguous_trade_count": ambiguous,
            "stop_first_count": stop_first,
            "trading_days": n_days,
        } if align_to_1m else None),
        "aligned_comparison": four_way,
        "conclusion": _conclusion(five_m, one_m, ranked, confidence, len(with_1m)),
        "next_steps": [
            "더 많은 종목/기간의 실제 1분봉 확보 → confidence MEDIUM→HIGH",
            "ranking 개선이 거래수 감소 착시인지 trade_count 동반 확인",
            "결과가 좋아도 모의 리허설 후보일 뿐 — 실전 전환은 별도 절차",
        ],
        "disclaimer": "이 결과는 연구용 백테스트이며 실전매매 권고가 아닙니다. 수익을 보장하지 않습니다.",
        "is_live_authorization": False, "is_order_signal": False,
        "auto_apply_allowed": False, "no_profit_guarantee": True, "contains_secret": False,
    }


def _delta(a, b):
    if a is None or b is None:
        return None
    return round(b - a, 4)


def _verdict(with_1m, five_m, one_m, replay_cov, ranked) -> str:
    """replay coverage 기반 정직 판정 — 좋아 보여도 coverage 낮으면 LOW."""
    if not with_1m or replay_cov <= 0:
        return "INTRABAR_REALDATA_NOT_READY"
    if replay_cov < 0.30:
        return "INTRABAR_REALDATA_LOW_CONFIDENCE"
    if replay_cov < 0.60:
        return "INTRABAR_REALDATA_MEDIUM_CONFIDENCE"
    # coverage ≥ 60% — ranking 실측 검증 조건.
    pf5, pf1 = five_m.get("profit_factor"), one_m.get("profit_factor")
    mdd5, mdd1 = five_m.get("mdd_pct"), one_m.get("mdd_pct")
    pf_improve = pf5 is not None and pf1 is not None and pf1 > pf5
    mdd_improve = mdd5 is not None and mdd1 is not None and mdd1 < mdd5
    score_ok = ranked.selected_avg_score > ranked.rejected_avg_score
    enough = (one_m["trade_count"] or 0) >= 100
    if (pf_improve or mdd_improve) and score_ok and enough:
        return "RANKING_REALDATA_VALIDATED"
    return "INTRABAR_REALDATA_READY"


def _conclusion(five_m, one_m, ranked, confidence, n_syms) -> list[str]:
    out = []
    pf5, pf1 = five_m["profit_factor"], one_m["profit_factor"]
    if pf5 is not None and pf1 is not None:
        if pf1 < pf5:
            out.append(f"1분봉 replay 후 PF 하락({pf5}→{pf1}) — 5분봉이 과대평가였을 가능성.")
        elif pf1 > pf5:
            out.append(f"1분봉 replay 후 PF 상승({pf5}→{pf1}).")
        else:
            out.append("5분봉 vs 1분봉 PF 차이 미미.")
    out.append(f"composite 선택 평균 score {ranked.selected_avg_score} vs 버려진 "
               f"{ranked.rejected_avg_score} (differs 여부는 ranking 섹션 참조).")
    out.append(f"결과 신뢰도: {confidence} (1분봉 종목 {n_syms}개) — "
               "표본/종목 적으면 결론 약함.")
    return out


def _empty_report(one_dir, five_dir, target, with_5m_only) -> dict[str, Any]:
    return {
        "available": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "realdata_backtest",
        "coverage": {"one_min_dir": str(one_dir), "five_min_dir": str(five_dir),
                     "symbols_with_1m": [], "symbols_with_5m_only": with_5m_only,
                     "symbol_count_1m": 0, "replayable_trades": 0,
                     "execution_confidence_distribution": {"HIGH": 0, "LOW": 0}},
        "confidence_level": "LOW",
        "verdict": "REALDATA_BACKTEST_NOT_READY",
        "conclusion": [
            "실제 1분봉 subset 데이터가 없어 intrabar 실측 결론을 낼 수 없습니다.",
            "scripts/collect_intrabar_1m_subset.py 를 장중에 실행해 1분봉 확보 필요.",
        ],
        "next_steps": [
            "장중(09:00~15:30) collect_intrabar_1m_subset.py 실행 (read-only, 주문 0건)",
            "data/market/robust_intraday_1m_subset/ 에 1분봉 CSV 확보 후 재실행",
        ],
        "disclaimer": "이 결과는 연구용 백테스트이며 실전매매 권고가 아닙니다. 수익을 보장하지 않습니다.",
        "is_live_authorization": False, "is_order_signal": False,
        "auto_apply_allowed": False, "no_profit_guarantee": True, "contains_secret": False,
    }
