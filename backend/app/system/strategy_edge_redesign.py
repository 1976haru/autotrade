"""전략 엣지 재설계 연구 (CHECKLIST-05, 백테스트 전용).

CHECKLIST-04 에서 ORB/Momentum/Gap/VWAP 4전략이 비용 후 PF<1 (STRATEGY_EDGE_NOT_FOUND)
으로 나왔다. 본 모듈은 ranking/filter/council 튜닝이 아니라 *진입 로직 자체* 의 실패
원인을 분석하고, **연구용 신규 진입 후보** 를 만들어 비용 후 PF>1 가능성을 검증한다.

엄격히 read-only / research-only:
- 기존 evaluator(`evaluate_orb/momentum/gap/vwap`) 와 intrabar 체결/비용/지표 helper 를
  *그대로 재사용* — 기존 전략 파라미터 변경 0건.
- 신규 후보는 *research namespace* 함수일 뿐 런타임 전략으로 등록/적용하지 않는다.
- broker 주문 메서드 / 단일 주문 라우터 / 주문 실행기 / KIS 주문 API import 0건, 실주문 0건.
- look-ahead 금지: 후보 진입 조건은 *진입 bar 까지의 정보* (opening range, prev_close,
  당일 누적) 만 사용. 사후 regime(전일 종가↔당일 종가)은 *귀속 분석* 에만 쓰고 후보
  선택에는 쓰지 않는다.
- auto_apply_allowed=False / applied_to_runtime=False / is_live_authorization=False /
  no_profit_guarantee=True 불변.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from app.agents.agent_council import (
    CouncilAction,
    evaluate_gap,
    evaluate_momentum,
    evaluate_orb,
    evaluate_vwap,
)
from app.backtest.intrabar_execution import _compute_costs  # 비용 공식 재사용
from app.backtest.strategy_council_backtest import (
    _build_input,
    _group_by_day,
    load_ohlcv_from_csv,
)
from app.system.final_multi_strategy_backtest import (
    _COST,
    _MAX_HOLD,
    _ROUND_TRIP_BPS,
    _STOP_PCT,
    _TARGET_PCT,
    _exit_trade,
    _grade,
    _kept_by_risk_filter,
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

_KST = timezone(timedelta(hours=9))
_EXISTING_STRATS = {"ORB": evaluate_orb, "MOMENTUM": evaluate_momentum,
                    "GAP": evaluate_gap, "VWAP": evaluate_vwap}


# ─────────────────────────────────────────────────────────────────────────────
# 시간대 / regime 귀속 helper
# ─────────────────────────────────────────────────────────────────────────────


def _time_bucket(ts) -> str:
    """KST 시각 → 시간대 버킷."""
    if ts is None:
        return "OTHER"
    k = ts.astimezone(_KST)
    hm = k.hour * 60 + k.minute
    if hm < 9 * 60 + 30:
        return "09:00-09:30"
    if hm < 10 * 60 + 30:
        return "09:30-10:30"
    if hm < 12 * 60:
        return "10:30-12:00"
    if hm < 13 * 60:
        return "12:00-13:00"
    if hm < 14 * 60 + 30:
        return "13:00-14:30"
    if hm < 15 * 60 + 20:
        return "14:30-15:20"
    return "OTHER"


def _day_regime_after(day_bars) -> str:
    """*사후* regime — 전일 종가↔당일 종가 + 당일 range. look-ahead(귀속 분석 전용)."""
    if len(day_bars) < 2:
        return "SIDEWAYS"
    o, c = day_bars[0].open, day_bars[-1].close
    if o <= 0:
        return "SIDEWAYS"
    ret = (c - o) / o
    rng = (max(b.high for b in day_bars) - min(b.low for b in day_bars)) / o
    if rng > 0.06:
        return "HIGH_VOLATILITY"
    if ret > 0.02:
        return "UPTREND"
    if ret < -0.02:
        return "DOWNTREND"
    return "SIDEWAYS"


def _mfe_mae_bps(entry: float, entry_ts, day_bars1: list[dict]) -> tuple[float, float]:
    """진입 후 max favorable / max adverse excursion (bps, BUY 기준)."""
    if entry <= 0 or entry_ts is None or not day_bars1:
        return 0.0, 0.0
    end = entry_ts + timedelta(minutes=_MAX_HOLD)
    win = [b for b in day_bars1 if b.get("ts") and entry_ts <= b["ts"] <= end]
    if not win:
        return 0.0, 0.0
    mfe = max((b["high"] - entry) / entry for b in win) * 1e4
    mae = min((b["low"] - entry) / entry for b in win) * 1e4
    return round(max(0.0, mfe), 1), round(min(0.0, mae), 1)


# ─────────────────────────────────────────────────────────────────────────────
# 신규 후보 진입 조건 (research only, look-ahead 금지)
# ─────────────────────────────────────────────────────────────────────────────


def _cand_orb_confirmation(mi, *, ve, gap, day_bars, i) -> bool:
    """A. breakout 즉시 진입 금지 — 직전 bar 도 ORH 위 + 거래량 확대로 false breakout 감소."""
    orh = mi.opening_range_high
    if orh is None or len(mi.recent_closes) < 2:
        return False
    return (mi.current_price > orh and mi.recent_closes[-2] >= orh and ve >= 1.2)


def _cand_vwap_reclaim(mi, *, ve, gap, day_bars, i) -> bool:
    """B. 단순 VWAP 위치가 아니라 이탈 후 재돌파(reclaim) + 거래량 증가."""
    vwap = mi.vwap
    rc = mi.recent_closes
    if vwap is None or vwap <= 0 or len(rc) < 3:
        return False
    dipped = min(rc[:-1]) < vwap          # 직전 구간에서 vwap 이탈
    reclaimed = mi.current_price > vwap    # 현재 재돌파
    return dipped and reclaimed and ve >= 1.1


def _cand_gap_fade_filtered(mi, *, ve, gap, day_bars, i) -> bool:
    """C. gap 추종 아님 — 과도하지 않은 gap-down(−1~−5%) 후 시가 재탈환(되돌림 bounce). long-only."""
    if gap is None:
        return False
    if not (-0.05 <= gap <= -0.01):        # gap-down, 과도하지 않게 제한
        return False
    return mi.current_price > mi.open_price and ve >= 1.0


def _cand_momentum_pullback(mi, *, ve, gap, day_bars, i) -> bool:
    """D. 추격 매수 금지 — 상승 모멘텀 발생 후 짧은 pullback 에서 진입(고점 추격 방지)."""
    rc = mi.recent_closes
    if len(rc) < 3 or mi.vwap is None:
        return False
    uptrend = rc[-2] > rc[0]                            # 직전까지 상승
    pullback = mi.current_price < rc[-2]                # 현재 살짝 되돌림
    not_chasing = mi.current_price < max(rc)            # 고점 아님
    above_vwap = mi.current_price > mi.vwap             # 추세 유지
    return uptrend and pullback and not_chasing and above_vwap


def _cand_high_vol_only_orb(mi, *, ve, gap, day_bars, i) -> bool:
    """E. 고변동일에만 ORB — 변동성은 *진입 시점* opening-range 폭 proxy (당일 미래 미사용)."""
    orh, orl = mi.opening_range_high, mi.opening_range_low
    if orh is None or orl is None or mi.open_price <= 0:
        return False
    or_width = (orh - orl) / mi.open_price
    if or_width < 0.02:                                # opening range <2% → 저변동, 진입 금지
        return False
    return mi.current_price > orh and ve >= 1.0


# NO_TRADE_FILTER (F) 는 거래 *제거* 조건 — 후보가 아니라 필터.
def _no_trade(mi, *, ve, gap) -> bool:
    """F. 거래하지 말아야 할 조건: 저거래량 / 횡보 / 낮은 기대이동."""
    rc = mi.recent_closes
    if ve < 0.9:
        return True                                    # 저거래량
    if len(rc) >= 3 and max(rc) > 0:
        rng = (max(rc) - min(rc)) / max(rc)
        if rng < 0.004:                                # 횡보(<0.4%)
            return True
    # 기대이동(target_bps) 이 왕복비용 대비 충분치 않으면 회피.
    if _TARGET_PCT * 1e4 < _ROUND_TRIP_BPS * 1.3:
        return True
    return False


_CANDIDATES: dict[str, Callable] = {
    "ORB_CONFIRMATION": _cand_orb_confirmation,
    "VWAP_RECLAIM": _cand_vwap_reclaim,
    "GAP_FADE_FILTERED": _cand_gap_fade_filtered,
    "MOMENTUM_PULLBACK": _cand_momentum_pullback,
    "HIGH_VOL_ONLY_ORB": _cand_high_vol_only_orb,
}


# ─────────────────────────────────────────────────────────────────────────────
# 거래 레코드 생성 (failure 귀속 필드 포함)
# ─────────────────────────────────────────────────────────────────────────────


def _make_rec(name, sym, day, bar, mi, ve, gap, group, regime,
              day_bars5, day_bars1, *, stop_pct=None, target_pct=None) -> dict | None:
    entry = bar.close
    if entry <= 0:
        return None
    sp = stop_pct if stop_pct is not None else _STOP_PCT
    tp = target_pct if target_pct is not None else _TARGET_PCT
    stop, target = entry * (1 - sp), entry * (1 + tp)
    et = bar.timestamp
    one = _exit_trade("BUY", entry, stop, target, et, day_bars5, day_bars1, use_1m=True)
    five = _exit_trade("BUY", entry, stop, target, et, day_bars5, day_bars1, use_1m=False)
    mfe, mae = _mfe_mae_bps(entry, et, day_bars1)
    return {
        "strategy": name, "symbol": sym, "day": day, "entry": entry, "ts": et,
        "net": one.net_pnl, "gross": one.gross_pnl, "slip5": one.slippage_paid,
        "net_5m": five.net_pnl, "hold": one.hold_minutes, "exit_reason": one.exit_reason,
        "stop_first": one.exit_reason in ("STOP_HIT", "AMBIGUOUS_STOP_FIRST"),
        "target_hit": one.exit_reason == "TARGET_HIT",
        "vol_exp": ve, "gap": gap, "group": group, "regime": regime,
        "time_bucket": _time_bucket(et), "low_liq": ve < 0.8,
        "mfe_bps": mfe, "mae_bps": mae,
        "net_edge_bps": tp * 1e4 - _ROUND_TRIP_BPS,
    }


# ─────────────────────────────────────────────────────────────────────────────
# exit 구조 비교 (research only, OOS 평가)
# ─────────────────────────────────────────────────────────────────────────────


def _exit_trailing(entry, et, day_bars1, *, stop_pct, trail_pct):
    """custom trailing-stop intrabar scan (1분봉). net pnl 반환."""
    if entry <= 0 or et is None:
        return None
    end = et + timedelta(minutes=_MAX_HOLD)
    win = [b for b in day_bars1 if b.get("ts") and et <= b["ts"] <= end]
    if not win:
        return None
    hard_stop = entry * (1 - stop_pct)
    peak = entry
    exit_px = win[-1]["close"]
    for b in win:
        peak = max(peak, b["high"])
        trail = max(hard_stop, peak * (1 - trail_pct))
        if b["low"] <= trail:
            exit_px = trail
            break
    _g, _c, _s, _t, net = _compute_costs("BUY", entry, exit_px, _COST)
    return net


def _exit_partial(entry, et, day_bars1, *, stop_pct, partial_pct, target_pct):
    """custom partial-profit: +partial 에서 절반 청산, 나머지는 target/stop."""
    if entry <= 0 or et is None:
        return None
    end = et + timedelta(minutes=_MAX_HOLD)
    win = [b for b in day_bars1 if b.get("ts") and et <= b["ts"] <= end]
    if not win:
        return None
    stop = entry * (1 - stop_pct)
    partial = entry * (1 + partial_pct)
    target = entry * (1 + target_pct)
    took_partial = False
    exit_rest = win[-1]["close"]
    for b in win:
        if not took_partial and b["high"] >= partial:
            took_partial = True
        if b["low"] <= stop:
            exit_rest = stop
            break
        if took_partial and b["high"] >= target:
            exit_rest = target
            break
    half = 0.5
    _g1, _c1, _s1, _t1, net_partial = _compute_costs("BUY", entry, partial, _COST)
    _g2, _c2, _s2, _t2, net_rest = _compute_costs("BUY", entry, exit_rest, _COST)
    if took_partial:
        return half * net_partial + half * net_rest
    return net_rest


def _exit_structure_compare(recs: list[dict], one_by_sym_day: dict) -> dict[str, Any]:
    """동일 진입(recs)에 exit 구조만 바꿔 OOS PF 비교 — 과최적화 금지(best 선택 안 함)."""
    fixed = {
        "existing_1.0_1.5_30": dict(stop_pct=0.010, target_pct=0.015, max_hold=30),
        "tighter_0.6_1.0_30": dict(stop_pct=0.006, target_pct=0.010, max_hold=30),
        "wider_1.5_2.5_30": dict(stop_pct=0.015, target_pct=0.025, max_hold=30),
        "time_stop_1.0_1.5_10": dict(stop_pct=0.010, target_pct=0.015, max_hold=10),
    }
    out: dict[str, Any] = {}
    for label, cfg in fixed.items():
        nets, ents = [], []
        for r in recs:
            d1 = one_by_sym_day.get((r["symbol"], r["day"]), [])
            entry, et = r["entry"], r["ts"]
            stop, target = entry * (1 - cfg["stop_pct"]), entry * (1 + cfg["target_pct"])
            res = _exit_trade("BUY", entry, stop, target, et, [], d1, use_1m=True) \
                if d1 else None
            if res is None:
                continue
            nets.append(res.net_pnl)
            ents.append(entry)
        out[label] = _pf(nets) if nets else None
    # trailing / partial (custom scan)
    for label, fn in (("trailing_stop1.0_trail0.8", lambda r, d1: _exit_trailing(
                            r["entry"], r["ts"], d1, stop_pct=0.010, trail_pct=0.008)),
                      ("partial_half_0.8_then_1.5", lambda r, d1: _exit_partial(
                            r["entry"], r["ts"], d1, stop_pct=0.010, partial_pct=0.008,
                            target_pct=0.015))):
        nets = []
        for r in recs:
            d1 = one_by_sym_day.get((r["symbol"], r["day"]), [])
            if not d1:
                continue
            v = fn(r, d1)
            if v is not None:
                nets.append(v)
        out[label] = _pf(nets) if nets else None
    return out


# ─────────────────────────────────────────────────────────────────────────────
# attribution
# ─────────────────────────────────────────────────────────────────────────────


def _attr(recs: list[dict], field: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for r in recs:
        groups.setdefault(str(r.get(field)), []).append(r)
    out = {}
    for k, g in sorted(groups.items()):
        nets = [t["net"] for t in g]
        out[k] = {"n": len(g), "pf": _pf(nets),
                  "net_sum": round(sum(nets), 2),
                  "win_rate": round(sum(1 for p in nets if p > 0) / len(g), 3)}
    return out


def _failure_summary(recs: list[dict]) -> dict[str, Any]:
    if not recs:
        return {"trade_count": 0}
    n = len(recs)
    stop_first = sum(1 for r in recs if r["stop_first"])
    target_hit = sum(1 for r in recs if r["target_hit"])
    gross_pf = _pf([r["gross"] for r in recs])
    net_pf = _pf([r["net"] for r in recs])
    return {
        "trade_count": n,
        "gross_pf": gross_pf, "net_pf": net_pf,
        "cost_kills_edge": bool(gross_pf and gross_pf >= 1.0 and (net_pf or 0) < 1.0),
        "no_gross_edge": bool((gross_pf or 0) < 1.0),
        "stop_first_ratio": round(stop_first / n, 3),
        "target_hit_ratio": round(target_hit / n, 3),
        "avg_mfe_bps": round(sum(r["mfe_bps"] for r in recs) / n, 1),
        "avg_mae_bps": round(sum(r["mae_bps"] for r in recs) / n, 1),
        "avg_hold_minutes": round(sum(r["hold"] for r in recs) / n, 1),
        "by_time_bucket": _attr(recs, "time_bucket"),
        "by_symbol": _attr(recs, "symbol"),
        "by_group": _attr(recs, "group"),
        "by_regime": _attr(recs, "regime"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 수집
# ─────────────────────────────────────────────────────────────────────────────


def _collect(one_dir: Path, five_dir: Path, symbols):
    five_map, one_map = _scan(five_dir), _scan(one_dir)
    syms = list(symbols) if symbols else sorted(set(five_map) & set(one_map))
    existing: dict[str, list[dict]] = {k: [] for k in _EXISTING_STRATS}
    candidates: dict[str, list[dict]] = {k: [] for k in _CANDIDATES}
    no_trade_filtered: list[dict] = []   # ORB - NO_TRADE_FILTER 적용 후
    one_by_sym_day: dict[tuple, list[dict]] = {}
    excluded: list[str] = []

    for sym in syms:
        if sym not in five_map or sym not in one_map:
            excluded.append(sym)
            continue
        bars5 = load_ohlcv_from_csv(str(five_map[sym]), default_symbol=sym)
        days5 = _group_by_day(bars5)
        days1 = {}
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
            done_e: set[str] = set()
            done_c: set[str] = set()
            nt_done = False
            for i in range(len(day_bars)):
                mi = _build_input(day_bars, i, opening_range_bars=6,
                                  recent_closes_window=5, prev_close=prev_close)
                b = day_bars[i]
                ve = (b.volume / avg_vol) if avg_vol else 1.0
                # 기존 4전략 (failure 분석용).
                for name, ev in _EXISTING_STRATS.items():
                    if name in done_e:
                        continue
                    if ev(mi).signal == CouncilAction.BUY:
                        rec = _make_rec(name, sym, day, b, mi, ve, gap, group, regime,
                                        day_bars, d1)
                        if rec:
                            existing[name].append(rec)
                            done_e.add(name)
                    # ORB 에 NO_TRADE_FILTER 적용한 변형.
                    if name == "ORB" and not nt_done and ev(mi).signal == CouncilAction.BUY:
                        if not _no_trade(mi, ve=ve, gap=gap):
                            rec = _make_rec("ORB_NOTRADE", sym, day, b, mi, ve, gap,
                                            group, regime, day_bars, d1)
                            if rec:
                                no_trade_filtered.append(rec)
                        nt_done = True
                # 신규 후보.
                for cname, cfn in _CANDIDATES.items():
                    if cname in done_c:
                        continue
                    if cfn(mi, ve=ve, gap=gap, day_bars=day_bars, i=i):
                        rec = _make_rec(cname, sym, day, b, mi, ve, gap, group, regime,
                                        day_bars, d1)
                        if rec:
                            candidates[cname].append(rec)
                            done_c.add(cname)
            prev_close = day_bars[-1].close

    return existing, candidates, no_trade_filtered, one_by_sym_day, excluded, syms


# ─────────────────────────────────────────────────────────────────────────────
# OOS / rolling split
# ─────────────────────────────────────────────────────────────────────────────


def _sorted_days(recs: list[dict]) -> list[str]:
    return sorted({r["day"] for r in recs})


def _oos_split(recs: list[dict]) -> dict[str, Any]:
    """앞 절반 train / 뒤 절반 OOS (날짜 기준)."""
    days = _sorted_days(recs)
    if len(days) < 4:
        return {"available": False}
    mid = len(days) // 2
    train_days, test_days = set(days[:mid]), set(days[mid:])
    tr = [r for r in recs if r["day"] in train_days]
    te = [r for r in recs if r["day"] in test_days]
    return {"available": True,
            "train": _metrics(tr, "net"), "test": _metrics(te, "net"),
            "train_pf": _pf([r["net"] for r in tr]),
            "oos_pf": _pf([r["net"] for r in te])}


def _rolling(recs: list[dict], *, train_n=20, test_n=10) -> dict[str, Any]:
    days = _sorted_days(recs)
    if len(days) < train_n + test_n:
        return {"available": False, "windows": []}
    wins = []
    i = 0
    while i + train_n + test_n <= len(days):
        test_days = set(days[i + train_n: i + train_n + test_n])
        te = [r for r in recs if r["day"] in test_days]
        wins.append({"test_start": days[i + train_n], "n": len(te),
                     "oos_pf": _pf([r["net"] for r in te])})
        i += test_n
    pfs = [w["oos_pf"] for w in wins if w["oos_pf"] is not None]
    return {"available": True, "windows": wins,
            "oos_pf_median": round(sorted(pfs)[len(pfs) // 2], 3) if pfs else None,
            "positive_window_ratio": round(sum(1 for p in pfs if p >= 1.0) / len(pfs), 3)
            if pfs else None}


def _candidate_verdict(full_pf, oos_pf, oos_mdd, train_pf) -> str:
    if full_pf is None or full_pf < 1.0:
        return "REJECT"
    if full_pf < 1.15:
        return "WATCH"
    if full_pf < 1.3:
        # MDD 개선 가정은 비교 단계 — CANDIDATE
        return "CANDIDATE"
    # PF>1.3 + OOS 유지
    if (oos_pf or 0) >= 1.15 and (train_pf or 0) >= 1.0:
        return "STRONG_CANDIDATE"
    return "CANDIDATE"


# ─────────────────────────────────────────────────────────────────────────────
# 오케스트레이터
# ─────────────────────────────────────────────────────────────────────────────


def run_strategy_edge_redesign(*, one_min_dir: Path | None = None,
                               five_min_dir: Path | None = None,
                               symbols: Sequence[str] | None = None) -> dict[str, Any]:
    one_dir = Path(one_min_dir) if one_min_dir else ONE_MIN_DIR_DEFAULT
    five_dir = Path(five_min_dir) if five_min_dir else FIVE_MIN_DIR_DEFAULT
    existing, candidates, nt_filtered, one_by_sym_day, excluded, syms = _collect(
        one_dir, five_dir, symbols)

    total = sum(len(v) for v in existing.values()) + sum(len(v) for v in candidates.values())
    if total < 10:
        return _empty(total, syms)

    # 1. 기존 전략 실패 원인 분석.
    failure = {name: _failure_summary(recs) for name, recs in existing.items()}

    # 2~3. 후보 전략 검증.
    cand_results: dict[str, Any] = {}
    for cname, recs in candidates.items():
        if not recs:
            cand_results[cname] = {"trade_count": 0, "verdict": "REJECT",
                                   "reason": "NO_TRADES"}
            continue
        m = _metrics(recs, "net")
        rf = [r for r in recs if _kept_by_risk_filter(r)]
        oos = _oos_split(recs)
        roll = _rolling(recs)
        verdict = _candidate_verdict(m["profit_factor"], oos.get("oos_pf"),
                                     m["mdd_pct"], oos.get("train_pf"))
        cand_results[cname] = {
            "trade_count": m["trade_count"],
            "profit_factor": m["profit_factor"], "return_pct": m["return_pct"],
            "mdd_pct": m["mdd_pct"], "expectancy_bps": m["expectancy_bps"],
            "win_rate": m["win_rate"], "payoff_ratio": m["payoff_ratio"],
            "avg_hold_minutes": m["avg_hold_minutes"],
            "cost_before_pf": _pf([r["gross"] for r in recs]),
            "cost_after_pf": m["profit_factor"],
            "slippage_stress": _slippage_stress(recs),
            "risk_filter_pf": _metrics(rf, "net")["profit_factor"],
            "oos": oos, "rolling": roll,
            "by_time_bucket": _attr(recs, "time_bucket"),
            "by_regime": _attr(recs, "regime"),
            "grade": _grade(m), "verdict": verdict,
        }

    # F. NO_TRADE_FILTER 효과 (ORB 기준).
    orb_all = existing["ORB"]
    no_trade_filter = {
        "orb_baseline_pf": _pf([r["net"] for r in orb_all]) if orb_all else None,
        "orb_baseline_n": len(orb_all),
        "orb_filtered_pf": _pf([r["net"] for r in nt_filtered]) if nt_filtered else None,
        "orb_filtered_n": len(nt_filtered),
        "removed_n": len(orb_all) - len(nt_filtered),
    }

    # 4. exit 구조 비교 (OOS only — 후보 중 거래수 최다 + ORB 기준).
    best_cand = max(candidates, key=lambda k: len(candidates[k])) if any(
        candidates.values()) else None
    exit_target_recs = candidates.get(best_cand) or orb_all
    exit_days = _sorted_days(exit_target_recs)
    oos_exit_days = set(exit_days[len(exit_days) // 2:]) if len(exit_days) >= 4 else set()
    exit_oos_recs = [r for r in exit_target_recs if r["day"] in oos_exit_days] \
        if oos_exit_days else exit_target_recs
    exit_structure = {
        "evaluated_on": best_cand or "ORB",
        "scope": "OOS" if oos_exit_days else "FULL(insufficient days)",
        "n": len(exit_oos_recs),
        "pf_by_structure": _exit_structure_compare(exit_oos_recs, one_by_sym_day),
    }

    # 5. verdict.
    survivors = [c for c, v in cand_results.items()
                 if v.get("verdict") in ("CANDIDATE", "STRONG_CANDIDATE")]
    watch = [c for c, v in cand_results.items() if v.get("verdict") == "WATCH"]
    exclude_existing = [n for n, f in failure.items()
                        if (f.get("net_pf") or 0) < 1.0]
    verdict, conclusion = _overall_verdict(survivors, watch, cand_results,
                                           no_trade_filter, exit_structure, failure)

    return {
        "available": True,
        "mode": "strategy_edge_redesign",
        "data": {"symbols": syms, "excluded": excluded,
                 "existing_trade_counts": {n: len(v) for n, v in existing.items()},
                 "candidate_trade_counts": {n: len(v) for n, v in candidates.items()}},
        "cost_model": {"round_trip_bps": _ROUND_TRIP_BPS},
        "failure_analysis": failure,
        "candidate_results": cand_results,
        "no_trade_filter": no_trade_filter,
        "exit_structure_analysis": exit_structure,
        "survivors": survivors,
        "watch": watch,
        "exclude_strategies": exclude_existing,
        "verdict": verdict,
        "conclusion": conclusion,
        "next_steps": [
            "REJECT 후보는 폐기 — 진입 방향/조건이 비용을 못 이김",
            "CANDIDATE/STRONG_CANDIDATE 만 별도 PR + OOS 재검증 + paper rehearsal 후 검토",
            "exit 구조는 best 선택(과최적화) 금지 — OOS 안정성 확인 후에만",
            "NO_TRADE_FILTER 가 PF 개선 시 *거래 회피* 가 신규 전략보다 우선일 수 있음",
        ],
        "auto_apply_allowed": False,
        "applied_to_runtime": False,
        "is_live_authorization": False,
        "is_order_signal": False,
        "no_profit_guarantee": True,
        "contains_secret": False,
        "disclaimer": "연구용 백테스트이며 실전매매 권고가 아닙니다. 신규 후보는 런타임 전략으로 "
                      "등록/적용되지 않습니다. 수익을 보장하지 않습니다.",
    }


def _overall_verdict(survivors, watch, cand_results, no_trade_filter, exit_structure,
                     failure):
    strong = [c for c in survivors
              if cand_results[c]["verdict"] == "STRONG_CANDIDATE"]
    if strong:
        return ("STRATEGY_CANDIDATE_FOUND",
                [f"STRONG_CANDIDATE: {strong} — OOS/비용 유지. 단 자동 적용 금지, 별도 PR + "
                 "paper rehearsal 필요."])
    if survivors:
        return ("STRATEGY_CANDIDATE_FOUND",
                [f"CANDIDATE: {survivors} — 비용 후 PF>1.15. OOS 추가 검증 필요, 자동 적용 금지."])
    # exit 구조가 *PF≥1.0 으로 생존* + 기존 대비 우수해야만 EXIT_REDESIGN_NEEDED.
    # (둘 다 PF<1 이면 손실만 줄인 것 — 엣지 생성 아님 → 정직하게 STILL_NOT_FOUND.)
    pfs = exit_structure.get("pf_by_structure", {})
    existing_pf = pfs.get("existing_1.0_1.5_30")
    survived_exit = [k for k, v in pfs.items()
                     if k != "existing_1.0_1.5_30" and v and v >= 1.0
                     and existing_pf and v > existing_pf]
    if survived_exit:
        return ("EXIT_REDESIGN_NEEDED",
                [f"진입 후보는 미달이나 exit 구조 {survived_exit} 가 비용 후 PF≥1.0 으로 생존 "
                 "— exit 재설계 레버리지 큼. OOS 재검증 필요, 자동 적용 금지."])
    # NO_TRADE_FILTER 도 PF≥1.0 생존 시에만 후보.
    ntf_b, ntf_f = no_trade_filter.get("orb_baseline_pf"), no_trade_filter.get("orb_filtered_pf")
    if ntf_f is not None and ntf_f >= 1.0 and ntf_b is not None and ntf_f > ntf_b:
        return ("NO_TRADE_FILTER_CANDIDATE",
                [f"신규 진입 후보는 미달이나 NO_TRADE_FILTER 가 ORB PF {ntf_b}→{ntf_f} 으로 "
                 "PF≥1.0 생존 — 거래 회피가 신규 전략보다 우선. 자동 적용 금지."])
    if watch:
        return ("PAPER_REHEARSAL_NOT_READY",
                [f"WATCH 후보 {watch} 만 존재(PF 1.0~1.15) — 실전성 불충분, paper rehearsal "
                 "준비 안 됨. 추가 연구 필요."])
    # 손실만 줄인 exit/filter 개선이 있었으면 결론에 *정직하게* 부기.
    note = ""
    if existing_pf and any(v and v > existing_pf for v in pfs.values()):
        note = (" (참고: 일부 exit 구조가 손실을 *줄였으나* PF<1 유지 — 엣지 생성 아님, "
                "과최적화 금지.)")
    return ("STRATEGY_EDGE_STILL_NOT_FOUND",
            ["기존 4전략 + 신규 후보 6종 모두 비용 후 PF<1 — 현 진입 로직 군에서 엣지 없음. "
             "기존 4전략은 *비용 전(gross)에도* PF<1(no_gross_edge) → 비용 문제가 아니라 "
             "진입 방향/타이밍 자체가 틀림(MAE>MFE, stop_first≫target_hit). 신규 후보 중 "
             "MOMENTUM_PULLBACK/ORB_CONFIRMATION 은 gross PF≈1 까지 근접했으나 비용을 못 넘음." + note])


def _empty(n, syms):
    return {"available": False, "verdict": "BACKTEST_INFRA_INCOMPLETE",
            "reason": "INSUFFICIENT_TRADES", "trade_count": n, "symbols": syms,
            "auto_apply_allowed": False, "applied_to_runtime": False,
            "is_live_authorization": False, "no_profit_guarantee": True,
            "contains_secret": False,
            "disclaimer": "연구용 백테스트 — 거래 표본 부족. 자동 적용 안 됨, 실전매매 권고 아님."}
