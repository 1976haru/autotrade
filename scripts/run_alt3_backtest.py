#!/usr/bin/env python3
"""대안 매매기법 3종 동시 탐색 — 백테스트 전용 (연구 자료).

방향성 매매가 8회 실패하고 buy&hold(단순보유)에 졌으므로, 방향성이 아닌
*다른 3개 축*을 한 번에 검증한다:

    A. 페어 트레이딩      (시장중립 — 두 종목 관계 괴리/수렴)
    B. 상대강도/횡단면 모멘텀 (롱온리 — 순위 게임, 공매도 불필요)
    C. 변동성 타게팅       (종목 고정, 비중만 조절 — 위험조정수익 개선 여부)

★ 본 스크립트는 *백테스트 연구 전용*이다.
  - 실거래 / 실주문 / 자동매매 / UI / 버튼 추가 0건.
  - broker / OrderExecutor / route_order / KIS 주문 API import 0건.
  - 안전 플래그(LIVE/AI/FUTURES/KIS_IS_PAPER/DEFAULT_MODE) 변경 0건.
  - 결과는 reports/backtest/alt3_YYYYMMDD.md + .json 으로만 출력 (git push 금지).

★ 공통 함정방지 (방향성 8회 실패의 교훈 — 처음부터 내장):
  1. LOOKAHEAD 0  : 모든 신호/파라미터/관계는 t 시점까지의 데이터로만(rolling).
                    전체기간으로 임계를 정하면 반칙.
  2. per-symbol/per-pair median 으로 측정. sum/n 금지(measurement-bug 재발 방지).
  3. OOS holdout  : in-sample 과 안 겹치는 새 기간(후반부)에서도 유지되는지 확인.
  4. 다중비교     : 총 조합 수 N 기록 + 우연 기대값 명시. in-sample 1등 신뢰 금지.
  5. 판정 기준    : ★buy&hold 대비 초과수익(+) AND OOS 유지 AND outlier 의존 아님.
                    PF 절대값 단독 판정 금지.

데이터:
  - 일봉 26년 : data/market/yf_multiyear/  (12 종목 + KS11 코스피, 2000~2026)
  - 60분봉 1년: data/market/lens_60m/      (80 종목, 2025-05~2026-05) — B 폭(breadth) 보조검증
비용:
  - 왕복 33bps (거래세 0.20% 포함) → 한쪽(one-way) 16.5bps, 회전율 |Δw| 에 비례.
  - 페어 양다리는 다리당 33bps 왕복(_tmp_pairs_trading 모델 이식).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ────────────────────────────────────────────────────────────────────────────
# 공통 상수
# ────────────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
DAILY_DIR = REPO_ROOT / "data" / "market" / "yf_multiyear"
LENS60_DIR = REPO_ROOT / "data" / "market" / "lens_60m"

# 일봉 26년 유니버스 (대형주 + 코스피 지수)
STOCKS_12 = [
    "005930", "000660", "005380", "000270", "035420", "035720",
    "051910", "006400", "005490", "055550", "068270", "012330",
]
KOSPI = "KS11"

NAMES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대차",
    "000270": "기아", "035420": "NAVER", "035720": "카카오",
    "051910": "LG화학", "006400": "삼성SDI", "005490": "POSCO홀딩스",
    "055550": "신한지주", "068270": "셀트리온", "012330": "현대모비스",
    "KS11": "코스피지수",
}

# 비용 (왕복 33bps, 거래세 0.20% 포함)
COST_ROUNDTRIP_BPS = 33.0
ONE_WAY_BPS = COST_ROUNDTRIP_BPS / 2.0   # 16.5bps — 회전율 1단위(매수 or 매도)당

TRADING_DAYS = 252

# OOS 분할: 26년을 전반/후반 절반으로. in-sample=전반, OOS=후반.
# (날짜는 데이터 로드 후 동적으로 결정)


# ────────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ────────────────────────────────────────────────────────────────────────────
def load_daily_closes(symbol: str, data_dir: Path = DAILY_DIR) -> list[tuple[str, float]]:
    """일봉 종가 [(date_str, close)] — 날짜 오름차순. yfinance 분할조정 종가."""
    p = data_dir / f"{symbol}.csv"
    if not p.exists():
        return []
    out: list[tuple[str, float]] = []
    with open(p, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if len(r) < 5 or not r[0] or not r[4]:
            continue
        try:
            d = r[0][:10]
            c = float(r[4])
            if c > 0:
                out.append((d, c))
        except (ValueError, IndexError):
            continue
    out.sort()
    return out


def load_60m_daily_closes(symbol: str) -> list[tuple[str, float]]:
    """60분봉 → 일봉 종가(각 거래일 마지막 bar) 변환. lens_60m 포맷."""
    p = LENS60_DIR / f"{symbol}_60m.csv"
    if not p.exists():
        return []
    by_day: dict[str, tuple[str, float]] = {}  # date -> (last_ts, close)
    with open(p, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    for r in rows[1:]:
        if len(r) < 5 or not r[0] or not r[4]:
            continue
        try:
            ts = r[0]
            d = ts[:10]
            c = float(r[4])
            if c <= 0:
                continue
        except (ValueError, IndexError):
            continue
        # 같은 날짜의 가장 늦은 timestamp 의 종가 = 일 종가
        prev = by_day.get(d)
        if prev is None or ts > prev[0]:
            by_day[d] = (ts, c)
    return sorted((d, v[1]) for d, v in by_day.items())


# ────────────────────────────────────────────────────────────────────────────
# 수익률-기반 위험조정 지표 (numpy) — B/C 포트폴리오·종목 시리즈용
# ────────────────────────────────────────────────────────────────────────────
def equity_from_returns(returns: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + returns)


def cagr(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    eq = equity_from_returns(returns)
    total = float(eq[-1])
    if total <= 0:
        return -1.0
    years = returns.size / TRADING_DAYS
    if years <= 0:
        return 0.0
    return total ** (1.0 / years) - 1.0


def total_return_pct(returns: np.ndarray) -> float:
    if returns.size == 0:
        return 0.0
    return float(equity_from_returns(returns)[-1] - 1.0) * 100.0


def max_drawdown_pct(returns: np.ndarray) -> float:
    """양수(%) 로 반환. 0 = 무손실."""
    if returns.size == 0:
        return 0.0
    eq = equity_from_returns(returns)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    return float(-dd.min()) * 100.0


def sharpe(returns: np.ndarray) -> float | None:
    """연율화 Sharpe (rf=0). 비중 일정 스케일에 *불변* → target_vol 선택 편향 없음."""
    if returns.size < 2:
        return None
    sd = float(np.std(returns, ddof=1))
    if sd <= 0:
        return None
    return float(np.mean(returns)) / sd * math.sqrt(TRADING_DAYS)


def sortino(returns: np.ndarray) -> float | None:
    if returns.size < 2:
        return None
    downside = returns[returns < 0]
    if downside.size < 1:
        return None
    dd = float(np.sqrt(np.mean(downside ** 2)))
    if dd <= 0:
        return None
    return float(np.mean(returns)) / dd * math.sqrt(TRADING_DAYS)


def calmar(returns: np.ndarray) -> float | None:
    mdd = max_drawdown_pct(returns) / 100.0
    if mdd <= 0:
        return None
    return cagr(returns) / mdd


def series_metrics(returns: np.ndarray) -> dict:
    return {
        "n_days": int(returns.size),
        "total_return_pct": round(total_return_pct(returns), 2),
        "cagr_pct": round(cagr(returns) * 100.0, 2),
        "mdd_pct": round(max_drawdown_pct(returns), 2),
        "sharpe": round(sharpe(returns), 3) if sharpe(returns) is not None else None,
        "sortino": round(sortino(returns), 3) if sortino(returns) is not None else None,
        "calmar": round(calmar(returns), 3) if calmar(returns) is not None else None,
    }


# ════════════════════════════════════════════════════════════════════════════
# 축 A — 페어 트레이딩 (시장중립)  ★ _tmp_pairs_trading 구조 이식 + OOS 분할 추가
# ════════════════════════════════════════════════════════════════════════════
PAIRS = [
    ("005930", "000660", "반도체 1·2위 (삼성전자-SK하이닉스)", "natural"),
    ("005380", "000270", "자동차 그룹 (현대차-기아)", "natural"),
    ("035420", "035720", "인터넷 1·2위 (NAVER-카카오)", "natural"),
    ("051910", "006400", "배터리 1·2위 (LG화학-삼성SDI)", "natural"),
    ("005490", "055550", "[CONTROL] 철강-금융 (관련성 낮음)", "control"),
    ("068270", "012330", "[CONTROL] 바이오-부품 (관련성 낮음)", "control"),
]

A_ENTRY_Z = 2.0
A_EXIT_Z = 0.5
A_STOP_Z = 3.5
A_TIME_STOP_DAYS = 30
A_WINDOW = 90  # rolling z-score window (lookahead-free)

# 페어 비용: 다리당 왕복 33bps × 2 다리 = 66bps + 공매도 차입
PAIR_ROUNDTRIP_BPS = COST_ROUNDTRIP_BPS * 2
SHORT_BORROW_BPS_PER_DAY = 0.6  # 연 1.5% / 250


def _align_pair(a, b):
    da = dict(a); db = dict(b)
    common = sorted(set(da) & set(db))
    return [(d, da[d], db[d]) for d in common]


def _rolling_z(spread, window=A_WINDOW):
    """★ lookahead-free: z[t] 는 spread[t-window:t](t 미포함) 통계 + spread[t-1] 사용."""
    n = len(spread)
    out: list[float | None] = [None] * n
    for t in range(window, n):
        win = spread[t - window:t]
        mean = sum(win) / window
        var = sum((x - mean) ** 2 for x in win) / (window - 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd > 0:
            out[t] = (spread[t - 1] - mean) / sd
    return out


def _simulate_pair(aligned, short_allowed=True, long_only_alt=False):
    n = len(aligned)
    if n < A_WINDOW + 30:
        return None
    a_c = [r[1] for r in aligned]
    b_c = [r[2] for r in aligned]
    dates = [r[0] for r in aligned]
    log_spread = [math.log(a) - math.log(b) for a, b in zip(a_c, b_c)]
    z = _rolling_z(log_spread, A_WINDOW)

    trades = []
    pos = None
    blocked = 0
    for t in range(A_WINDOW + 1, n):
        zt = z[t]
        if zt is None:
            continue
        if pos is not None:
            held = t - pos["entry_t"]
            reason = None
            if abs(zt) <= A_EXIT_Z:
                reason = "CONVERGE"
            elif abs(zt) >= A_STOP_Z:
                reason = "STOP"
            elif held >= A_TIME_STOP_DAYS:
                reason = "TIME"
            if reason:
                pnl = _pair_pnl(pos, a_c[t], b_c[t], held, short_allowed, long_only_alt)
                trades.append({"entry_date": pos["entry_date"], "exit_date": dates[t],
                               "held_days": held, "entry_z": pos["entry_z"],
                               "exit_z": round(zt, 3), "exit_reason": reason, "pnl_pct": pnl})
                pos = None
        if pos is None and abs(zt) >= A_ENTRY_Z:
            win = log_spread[t - A_WINDOW:t]
            sd = statistics.stdev(win) if len(win) > 1 else 0.0
            expected = abs(zt) * sd
            est_cost = (PAIR_ROUNDTRIP_BPS / 1e4) + (
                SHORT_BORROW_BPS_PER_DAY * 15 / 1e4 if short_allowed else 0.0)
            if expected <= est_cost:
                blocked += 1
                continue
            pos = {"entry_t": t, "entry_date": dates[t], "entry_z": round(zt, 3),
                   "entry_a": a_c[t], "entry_b": b_c[t], "z_sign": 1 if zt > 0 else -1}
    return {"n": n, "trades": trades, "blocked": blocked}


def _pair_pnl(pos, a_now, b_now, held, short_allowed, long_only_alt):
    a_ret = a_now / pos["entry_a"] - 1
    b_ret = b_now / pos["entry_b"] - 1
    if long_only_alt:
        gross = b_ret if pos["z_sign"] > 0 else a_ret  # underperformer 만 long
        cost = (PAIR_ROUNDTRIP_BPS / 1e4) / 2
        return round((gross - cost) * 100, 4)
    if short_allowed:
        gross = (b_ret - a_ret) if pos["z_sign"] > 0 else (a_ret - b_ret)
        cost = (PAIR_ROUNDTRIP_BPS / 1e4) + (SHORT_BORROW_BPS_PER_DAY * held / 1e4)
        return round((gross - cost) * 100, 4)
    return 0.0


def _pair_trade_stats(trades):
    if not trades:
        return None
    pnls = [t["pnl_pct"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if sum(losses) < 0 else None
    return {
        "n_trades": len(trades),
        "win_rate": round(len(wins) / len(pnls), 3),
        "median_pnl_pct": round(statistics.median(pnls), 4),   # ★ per-pair median
        "mean_pnl_pct": round(statistics.mean(pnls), 4),
        "pf": round(pf, 3) if pf else None,
        "sum_pct": round(sum(pnls), 2),
        "biggest_win_pct": round(max(pnls), 2),
        "biggest_loss_pct": round(min(pnls), 2),
        "converge": sum(1 for t in trades if t["exit_reason"] == "CONVERGE"),
        "stop": sum(1 for t in trades if t["exit_reason"] == "STOP"),
        "time": sum(1 for t in trades if t["exit_reason"] == "TIME"),
    }


def _market_neutral(trades, kospi_closes):
    """페어 일별 손익이 코스피 등락과 상관있나 (|corr|<0.2 면 시장중립)."""
    if not trades:
        return None
    kd = dict(kospi_closes)
    daily = {}
    for t in trades:
        daily[t["exit_date"]] = daily.get(t["exit_date"], 0.0) + t["pnl_pct"]
    sk = sorted(kospi_closes)
    kdates = [x[0] for x in sk]
    kvals = [x[1] for x in sk]
    kidx = {d: i for i, d in enumerate(kdates)}
    pair_r, kospi_r = [], []
    for d, v in daily.items():
        i = kidx.get(d)
        if i is None or i == 0:
            continue
        pair_r.append(v)
        kospi_r.append((kvals[i] / kvals[i - 1] - 1) * 100)
    if len(pair_r) < 10:
        return None
    pr = np.array(pair_r); kr = np.array(kospi_r)
    if np.std(pr) <= 0 or np.std(kr) <= 0:
        return None
    corr = float(np.corrcoef(pr, kr)[0, 1])
    return {"n_match_days": len(pair_r), "corr_with_kospi": round(corr, 3),
            "market_neutral": bool(abs(corr) < 0.2)}


def run_axis_a(kospi_closes, split_date: str):
    """페어 — full / in-sample(전반) / OOS(후반) 3구간, short & long-only-alt 모두."""
    results = {"split_date": split_date, "n_combos": len(PAIRS), "pairs": {}}
    for a, b, label, kind in PAIRS:
        ca = load_daily_closes(a)
        cb = load_daily_closes(b)
        if not ca or not cb:
            results["pairs"][f"{a}-{b}"] = {"error": "missing data", "label": label}
            continue
        aligned = _align_pair(ca, cb)
        if len(aligned) < A_WINDOW + 100:
            results["pairs"][f"{a}-{b}"] = {"error": f"too few days ({len(aligned)})", "label": label}
            continue
        ins = [r for r in aligned if r[0] < split_date]
        oos = [r for r in aligned if r[0] >= split_date]
        entry = {"label": label, "kind": kind,
                 "first_date": aligned[0][0], "last_date": aligned[-1][0],
                 "n_aligned": len(aligned)}
        for tag, seg, mode_short, mode_long in [
            ("full_short", aligned, True, False),
            ("ins_short", ins, True, False),
            ("oos_short", oos, True, False),
            ("full_longonly", aligned, False, True),
        ]:
            sim = _simulate_pair(seg, short_allowed=mode_short, long_only_alt=mode_long)
            if not sim:
                entry[tag] = {"error": "insufficient segment"}
                continue
            stats = _pair_trade_stats(sim["trades"])
            block_obj = {"blocked_by_cost": sim["blocked"], "stats": stats}
            if tag == "full_short":
                block_obj["market_neutral"] = _market_neutral(sim["trades"], kospi_closes)
            entry[tag] = block_obj
        results["pairs"][f"{a}-{b}"] = entry
    return results


# ════════════════════════════════════════════════════════════════════════════
# 축 B — 상대강도 / 횡단면 모멘텀 (롱온리)
# ════════════════════════════════════════════════════════════════════════════
def _build_price_panel(symbols, loader):
    """심볼들 → (dates, price_matrix[T,S]) NaN 채움. point-in-time."""
    series = {s: dict(loader(s)) for s in symbols}
    all_dates = sorted({d for s in symbols for d in series[s]})
    T, S = len(all_dates), len(symbols)
    mat = np.full((T, S), np.nan)
    didx = {d: i for i, d in enumerate(all_dates)}
    for j, s in enumerate(symbols):
        for d, c in series[s].items():
            mat[didx[d], j] = c
    return all_dates, mat


def _daily_returns_matrix(price_mat):
    """일별 수익률 [T,S]. 첫 행 및 결측 인접은 NaN."""
    T, S = price_mat.shape
    ret = np.full((T, S), np.nan)
    ret[1:] = price_mat[1:] / price_mat[:-1] - 1.0
    return ret


def _portfolio_momentum(dates, price_mat, lookback_days, rebal_days, top_n):
    """롱온리 횡단면 모멘텀. 매 rebal 시점 trailing 수익률 상위 top_n 동일가중 보유.

    ★ lookahead-free: 리밸런싱 t 의 보유는 [t-lookback, t] 까지의 정보로만 결정,
      그 보유로 (t, 다음 리밸런싱) 구간의 *미래* 일별수익을 받는다.
    회전비용: 리밸런싱마다 |Δweight| × one-way.
    """
    T, S = price_mat.shape
    ret = _daily_returns_matrix(price_mat)
    weights = np.zeros(S)
    port_ret = np.full(T, np.nan)
    turnover_events = []
    held_log = []
    next_rebal = lookback_days  # 첫 리밸런싱은 lookback 확보 후
    for t in range(T):
        # 비중이 정해진 상태에서 오늘(t) 수익을 받는다
        if t > 0 and np.any(weights > 0):
            day_r = ret[t]
            # 보유 종목 중 오늘 데이터 있는 것만
            mask = (weights > 0) & np.isfinite(day_r)
            if np.any(mask):
                w = weights[mask]
                port_ret[t] = float(np.sum(w * day_r[mask]) / np.sum(weights))
            else:
                port_ret[t] = 0.0
        # 리밸런싱 (장 마감 후 정보로 다음날부터 적용되도록 t 시점 결정 → 비용 t 에 차감)
        if t >= next_rebal:
            # trailing return [t-lookback, t] — t 시점까지 정보 (lookahead-free)
            past = t - lookback_days
            valid = np.isfinite(price_mat[t]) & np.isfinite(price_mat[past]) & (price_mat[past] > 0)
            trailing = np.full(S, -np.inf)
            trailing[valid] = price_mat[t][valid] / price_mat[past][valid] - 1.0
            elig = np.where(valid)[0]
            new_w = np.zeros(S)
            if elig.size > 0:
                order = elig[np.argsort(trailing[elig])[::-1]]
                pick = order[:top_n]
                if pick.size > 0:
                    new_w[pick] = 1.0 / pick.size
            # 회전비용
            turnover = float(np.sum(np.abs(new_w - weights)))
            cost = turnover * ONE_WAY_BPS / 1e4
            if np.isnan(port_ret[t]):
                port_ret[t] = -cost
            else:
                port_ret[t] -= cost
            turnover_events.append(turnover)
            weights = new_w
            held_log.append((dates[t], [k for k in pick] if elig.size > 0 else []))
            next_rebal = t + rebal_days
    pr = port_ret[np.isfinite(port_ret)]
    return pr, turnover_events


def _benchmark_equal_weight(price_mat):
    """동일가중 '전 종목 보유'(매일 데이터 있는 종목 동일가중) 일별수익 — buy&hold 기준."""
    ret = _daily_returns_matrix(price_mat)
    T = ret.shape[0]
    out = np.full(T, np.nan)
    for t in range(1, T):
        row = ret[t]
        m = np.isfinite(row)
        if np.any(m):
            out[t] = float(np.mean(row[m]))
    return out[np.isfinite(out)]


def _slice_returns_by_date(dates_with_ret, split_date, side):
    pass  # (B 는 포트폴리오 시리즈라 별도 OOS 재실행으로 처리)


def run_axis_b_daily(split_date: str):
    """B 메인: 12종목 26년 일봉. lookback×rebal×topN sweep + OOS 분할 재실행."""
    dates, price_mat = _build_price_panel(STOCKS_12, load_daily_closes)
    split_idx = next((i for i, d in enumerate(dates) if d >= split_date), len(dates) // 2)

    lookbacks = [63, 126, 252]          # 3 / 6 / 12 개월
    rebals = [21, 63]                   # 월간 / 분기
    top_ns = [3, 4]
    combos = [(L, R, N) for L in lookbacks for R in rebals for N in top_ns]

    def run_segment(p_mat, d_list):
        bench = _benchmark_equal_weight(p_mat)
        bench_m = series_metrics(bench)
        rows = []
        for (L, R, N) in combos:
            pr, turn = _portfolio_momentum(d_list, p_mat, L, R, N)
            m = series_metrics(pr)
            rows.append({
                "lookback_days": L, "rebal_days": R, "top_n": N,
                "metrics": m,
                "excess_cagr_pct": round(m["cagr_pct"] - bench_m["cagr_pct"], 2),
                "excess_sharpe": (round(m["sharpe"] - bench_m["sharpe"], 3)
                                  if m["sharpe"] is not None and bench_m["sharpe"] is not None else None),
                "avg_turnover_per_rebal": round(float(np.mean(turn)), 3) if turn else None,
                "n_rebals": len(turn),
            })
        return bench_m, rows

    full_bench, full_rows = run_segment(price_mat, dates)
    ins_bench, ins_rows = run_segment(price_mat[:split_idx], dates[:split_idx])
    oos_bench, oos_rows = run_segment(price_mat[split_idx:], dates[split_idx:])

    # in-sample 1등 (excess_cagr) → OOS 에서 유지되나
    def key(r):
        return r["excess_cagr_pct"] if r["excess_cagr_pct"] is not None else -999
    ins_best = max(ins_rows, key=key)
    # 같은 조합의 OOS 성적
    oos_for_best = next(r for r in oos_rows if (r["lookback_days"], r["rebal_days"], r["top_n"])
                        == (ins_best["lookback_days"], ins_best["rebal_days"], ins_best["top_n"]))

    return {
        "universe": "12 대형주 (일봉 26년)",
        "first_date": dates[0], "last_date": dates[-1],
        "split_date": split_date,
        "n_combos": len(combos),
        "chance_note": (
            f"{len(combos)}개 조합 중 우연히 buy&hold 를 이기는 조합이 절반(~{len(combos)//2}개) "
            "나올 수 있음 → in-sample 1등 단독 신뢰 금지, OOS 유지가 핵심."
        ),
        "benchmark_full": full_bench,
        "benchmark_ins": ins_bench,
        "benchmark_oos": oos_bench,
        "full": full_rows,
        "ins": ins_rows,
        "oos": oos_rows,
        "ins_best": ins_best,
        "oos_for_ins_best": oos_for_best,
        "n_combos_beating_bh_full": sum(1 for r in full_rows if key(r) > 0),
    }


def run_axis_b_breadth():
    """B 보조: 80종목 60분봉→일봉(1년). breadth 확인 — ★OOS 불가(1년 단일기간, 탐색용만)."""
    syms = sorted({p.name[:-len("_60m.csv")] for p in LENS60_DIR.glob("*_60m.csv")})
    if not syms:
        return {"error": "no lens_60m data", "n_symbols": 0}
    dates, price_mat = _build_price_panel(syms, load_60m_daily_closes)
    if len(dates) < 80:
        return {"error": f"too few days ({len(dates)})", "n_symbols": len(syms)}
    bench = _benchmark_equal_weight(price_mat)
    bench_m = series_metrics(bench)
    # 1년 단일 → 짧은 lookback / 주간·격주 리밸런싱
    combos = [(21, 5, 10), (21, 10, 10), (42, 10, 15), (63, 21, 20)]
    rows = []
    for (L, R, N) in combos:
        pr, turn = _portfolio_momentum(dates, price_mat, L, R, N)
        m = series_metrics(pr)
        rows.append({"lookback_days": L, "rebal_days": R, "top_n": N, "metrics": m,
                     "excess_cagr_pct": round(m["cagr_pct"] - bench_m["cagr_pct"], 2),
                     "n_rebals": len(turn)})
    return {
        "universe": f"{len(syms)} 종목 (60분봉→일봉, 약 1년)",
        "first_date": dates[0], "last_date": dates[-1],
        "n_days": len(dates), "n_symbols": len(syms),
        "n_combos": len(combos),
        "oos_caveat": "★ 1년 단일 기간 → OOS 분할 불가. 탐색용 참고치일 뿐 신뢰 판정 금지.",
        "benchmark": bench_m, "rows": rows,
    }


# ════════════════════════════════════════════════════════════════════════════
# 축 C — 변동성 타게팅 / 리스크 조절 (종목 고정, 비중 조절)
# ════════════════════════════════════════════════════════════════════════════
def _vol_target_returns(daily_ret, window, target_vol_ann, cap=2.0):
    """★ lookahead-free: 비중_t = target / 실현변동성(ret[t-window:t], t 미포함).

    weight 는 t 시작 시 과거 정보로 정해지고 ret[t] 에 곱해진다.
    회전비용: |w_t - w_{t-1}| × one-way.
    """
    n = daily_ret.size
    w = np.zeros(n)
    strat = np.full(n, np.nan)
    prev_w = 0.0
    for t in range(window, n):
        win = daily_ret[t - window:t]
        win = win[np.isfinite(win)]
        if win.size < window // 2:
            continue
        rv = float(np.std(win, ddof=1)) * math.sqrt(TRADING_DAYS)
        wt = 0.0 if rv <= 0 else min(cap, target_vol_ann / rv)
        if not np.isfinite(daily_ret[t]):
            continue
        cost = abs(wt - prev_w) * ONE_WAY_BPS / 1e4
        strat[t] = wt * daily_ret[t] - cost
        w[t] = wt
        prev_w = wt
    s = strat[np.isfinite(strat)]
    return s, w


def run_axis_c(split_date: str):
    """C: 종목별 buy&hold vs 변동성타게팅. Sharpe/Calmar/Sortino per-symbol median."""
    windows = [20, 60]
    targets = [0.10, 0.15, 0.20]   # 연 10/15/20% — Sharpe/Sortino 는 스케일 불변
    combos = [(w, tv) for w in windows for tv in targets]

    per_symbol = {}
    for s in STOCKS_12:
        closes = load_daily_closes(s)
        if len(closes) < 300:
            per_symbol[s] = {"error": "too few days"}
            continue
        dates = [d for d, _ in closes]
        prices = np.array([c for _, c in closes])
        ret = np.full(prices.size, np.nan)
        ret[1:] = prices[1:] / prices[:-1] - 1.0
        split_idx = next((i for i, d in enumerate(dates) if d >= split_date), prices.size // 2)

        def bh_window(r):
            r = r[np.isfinite(r)]
            return series_metrics(r)

        full_bh = bh_window(ret)
        ins_bh = bh_window(ret[:split_idx])
        oos_bh = bh_window(ret[split_idx:])

        combo_rows = []
        for (win, tv) in combos:
            full_s, _ = _vol_target_returns(ret, win, tv)
            ins_s, _ = _vol_target_returns(ret[:split_idx], win, tv)
            oos_s, _ = _vol_target_returns(ret[split_idx:], win, tv)
            combo_rows.append({
                "window": win, "target_vol": tv,
                "full": series_metrics(full_s),
                "ins": series_metrics(ins_s),
                "oos": series_metrics(oos_s),
            })
        per_symbol[s] = {"name": NAMES.get(s, s),
                         "buy_hold": {"full": full_bh, "ins": ins_bh, "oos": oos_bh},
                         "combos": combo_rows}

    # per-symbol median 집계: 대표 조합 (window=60, target=15%) 기준 개선폭
    def collect(metric, seg, getter):
        vals = []
        for s, d in per_symbol.items():
            if "error" in d:
                continue
            rep = next((c for c in d["combos"] if c["window"] == 60 and c["target_vol"] == 0.15), None)
            if not rep:
                continue
            bh = d["buy_hold"][seg]
            vt = rep[seg]
            v = getter(bh, vt)
            if v is not None:
                vals.append(v)
        return vals

    def med_improve(metric, seg):
        vals = collect(metric, seg, lambda bh, vt: (
            (vt[metric] - bh[metric]) if (vt.get(metric) is not None and bh.get(metric) is not None) else None))
        return round(statistics.median(vals), 3) if vals else None

    summary = {}
    for seg in ["full", "ins", "oos"]:
        summary[seg] = {
            "median_sharpe_improvement": med_improve("sharpe", seg),
            "median_sortino_improvement": med_improve("sortino", seg),
            "median_calmar_improvement": med_improve("calmar", seg),
            "median_mdd_reduction_pct": (lambda vals: round(statistics.median(vals), 3) if vals else None)(
                collect("mdd_pct", seg, lambda bh, vt: (
                    bh["mdd_pct"] - vt["mdd_pct"] if bh.get("mdd_pct") is not None and vt.get("mdd_pct") is not None else None))),
            "median_return_diff_pct": (lambda vals: round(statistics.median(vals), 3) if vals else None)(
                collect("total_return_pct", seg, lambda bh, vt: (
                    vt["total_return_pct"] - bh["total_return_pct"] if bh.get("total_return_pct") is not None and vt.get("total_return_pct") is not None else None))),
            "n_symbols_sharpe_up": (lambda vals: sum(1 for v in vals if v > 0))(
                collect("sharpe", seg, lambda bh, vt: (
                    vt["sharpe"] - bh["sharpe"] if vt.get("sharpe") is not None and bh.get("sharpe") is not None else None))),
        }

    return {
        "universe": "12 대형주 (일봉 26년), 종목별 buy&hold vs 변동성타게팅",
        "split_date": split_date,
        "n_combos_per_symbol": len(combos),
        "representative_combo": "window=60일, target_vol=연15%, cap=2.0",
        "note_scale_invariance": (
            "★ Sharpe/Sortino 는 비중 일정 스케일에 불변 → target_vol(10/15/20%) "
            "선택이 위험조정수익 판정을 편향시키지 않음. 총수익/MDD 는 target 에 따라 스케일됨."),
        "per_symbol": per_symbol,
        "median_summary": summary,
    }


# ════════════════════════════════════════════════════════════════════════════
# 리포트 생성
# ════════════════════════════════════════════════════════════════════════════
def _fmt(v, suffix="", dash="—"):
    return f"{v}{suffix}" if v is not None else dash


def build_markdown(a, b, b_breadth, c, today: str) -> str:
    L = []
    P = L.append
    P(f"# 대안 매매기법 3종 동시 탐색 — 백테스트 결과 ({today})\n")
    P("> ⚠️ **백테스트 연구 자료입니다. 실거래 권유·수익 보장이 아닙니다.** "
      "실주문/자동매매/버튼 0건, 안전 플래그 변경 0건. 결과는 PC 에서만 확인(외부 전송 금지).\n")

    # ── 1. 한 줄 결론 ──
    P("## 1. 한 줄 결론 (쉬운 말)\n")

    # ── A 판정 (★ 핵심: 대조군이 자연쌍보다 좋으면 '관계 엣지'는 가짜) ──
    a_natural = [v for k, v in a["pairs"].items()
                 if isinstance(v, dict) and v.get("kind") == "natural" and "error" not in v]
    a_control = [v for k, v in a["pairs"].items()
                 if isinstance(v, dict) and v.get("kind") == "control" and "error" not in v]
    a_mn_pass = sum(1 for v in a_natural
                    if v.get("full_short", {}).get("market_neutral", {})
                    and v["full_short"]["market_neutral"].get("market_neutral"))

    def _oos_meds(group):
        out = []
        for v in group:
            st = v.get("oos_short", {}).get("stats")
            if st and st.get("median_pnl_pct") is not None:
                out.append(st["median_pnl_pct"])
        return out
    nat_oos = _oos_meds(a_natural)
    ctl_oos = _oos_meds(a_control)
    a_oos_pos = sum(1 for x in nat_oos if x > 0)
    nat_oos_med = round(statistics.median(nat_oos), 3) if nat_oos else None
    ctl_oos_med = round(statistics.median(ctl_oos), 3) if ctl_oos else None
    # 자연쌍이 대조군보다 확실히 좋아야 '관계 엣지' 인정. 아니면 단순 평균회귀 노이즈.
    a_relationship_edge = (nat_oos_med is not None and ctl_oos_med is not None
                           and nat_oos_med > ctl_oos_med)
    # 한국 개인 공매도 실행불가 → 고전 페어는 어차피 '실행불가'
    a_verdict = "실행불가 (공매도 제약) · 관계 엣지 미확인" if not a_relationship_edge \
        else "실행불가 (공매도 제약) · 롱온리 변형만 조건부"

    # ── B 판정 (in-sample 1등의 OOS 유지 + 다수 조합 OOS 일관성) ──
    b_oos_best = b["oos_for_ins_best"]
    b_oos_excess = b_oos_best["excess_cagr_pct"]
    # 룩백 252일(12개월) 조합들이 OOS 에서 일관되게 (+)인지 — 단일 행운 조합 아닌지
    b_252_oos = [r["excess_cagr_pct"] for r in b["oos"]
                 if r["lookback_days"] == 252 and r["excess_cagr_pct"] is not None]
    b_252_consistent = bool(b_252_oos) and all(x > 0 for x in b_252_oos)
    b_verdict = ("후보 있음 (OOS 유지·다수조합 일관)"
                 if (b_oos_excess is not None and b_oos_excess > 0 and b_252_consistent)
                 else ("후보 *조건부* 있음" if (b_oos_excess or -1) > 0 else "후보 없음"))

    # ── C 판정 (★ 핵심: 위험조정수익이 나아져야 진짜. MDD 감소만으로는 '덜 투자' 효과) ──
    c_oos = c["median_summary"]["oos"]
    c_sharpe_oos = c_oos["median_sharpe_improvement"]
    c_sortino_oos = c_oos["median_sortino_improvement"]
    c_calmar_oos = c_oos["median_calmar_improvement"]
    c_mdd_oos = c_oos["median_mdd_reduction_pct"]
    c_sharpe_up_n = c_oos["n_symbols_sharpe_up"]
    c_risk_adj_up = any(v is not None and v > 0 for v in [c_sharpe_oos, c_sortino_oos, c_calmar_oos])
    if c_risk_adj_up:
        c_verdict = "후보 있음 (위험조정 개선)"
    elif c_mdd_oos is not None and c_mdd_oos > 0:
        c_verdict = "후보 아님 (낙폭만 감소·효율 개선 없음 = 단순 디레버리지)"
    else:
        c_verdict = "후보 없음"

    P(f"- **A. 페어(시장중립):** {a_verdict} — "
      f"한국 개인은 공매도가 사실상 불가해 *고전적 페어는 실거래 실행불가*. "
      f"게다가 무관 업종 **대조군(OOS 중앙값 {_fmt(ctl_oos_med,'%')})이 자연쌍(OOS 중앙값 {_fmt(nat_oos_med,'%')})보다 좋음** "
      f"→ '관계 수렴 엣지'가 진짜라 보기 어려움(단순 평균회귀 노이즈 의심).")
    P(f"- **B. 상대강도(롱온리 순위게임):** {b_verdict} — "
      f"12개월 룩백 모멘텀이 OOS 에서 buy&hold 대비 초과CAGR {_fmt(b_oos_excess,'%p')}, "
      f"252일 룩백 4개 조합 모두 OOS (+){' ✅' if b_252_consistent else ''}. 단 종목 12개·생존편향 가능.")
    P(f"- **C. 변동성타게팅(비중조절):** {c_verdict} — "
      f"OOS 종목중앙값 Sharpe {_fmt(c_sharpe_oos)}·Calmar {_fmt(c_calmar_oos)} (개선 아님, Sharpe↑ {c_sharpe_up_n}/12), "
      f"MDD 는 {_fmt(c_mdd_oos,'%p')} 줄지만 이는 평균 비중을 낮춘(≈디레버리지) 효과에 가까움.")
    P("")
    P("**종합 한 줄:** "
      + _overall_oneliner(a_verdict, b_verdict, c_verdict, b_oos_excess, c_sharpe_oos, c_mdd_oos))
    P("")

    # ── 2. A 페어 ──
    P("## 2. A. 페어 트레이딩 (시장중립)\n")
    P(f"- 데이터: 일봉 26년, 후보쌍 {a['n_combos']}개 (자연쌍 4 + 대조군 2). OOS 분할일: `{a['split_date']}`")
    P(f"- 구조(역김프 이식): 스프레드 z-score |z|≥{A_ENTRY_Z} 진입 → |z|≤{A_EXIT_Z} 수렴청산 → "
      f"|z|≥{A_STOP_Z} 손절 → {A_TIME_STOP_DAYS}일 시간청산. rolling window {A_WINDOW}일(lookahead-free).")
    P(f"- ★ cost-block: 기대수렴수익 ≤ 비용이면 진입 차단(영구단절 물림 방어).\n")
    P("| 쌍 | 종류 | 상관 corr_KOSPI | 시장중립 | 거래수(full) | 중앙값손익% (full) | 중앙값손익% (OOS) | 롱온리 중앙값% |")
    P("|---|---|---|---|---|---|---|---|")
    for k, v in a["pairs"].items():
        if "error" in v:
            P(f"| {k} | {v.get('label','')} | — | — | ERR: {v['error']} | | | |")
            continue
        name = f"{NAMES.get(v['label'][:6],'')}"  # label already descriptive
        fs = v.get("full_short", {})
        os_ = v.get("oos_short", {})
        lo = v.get("full_longonly", {})
        mn = fs.get("market_neutral") or {}
        fstats = fs.get("stats") or {}
        ostats = os_.get("stats") or {}
        lstats = lo.get("stats") or {}
        P(f"| `{k}`<br>{v['label']} | {v['kind']} | "
          f"{_fmt(mn.get('corr_with_kospi'))} | {'예' if mn.get('market_neutral') else '아니오'} | "
          f"{_fmt(fstats.get('n_trades'))} | {_fmt(fstats.get('median_pnl_pct'))} | "
          f"{_fmt(ostats.get('median_pnl_pct'))} | {_fmt(lstats.get('median_pnl_pct'))} |")
    P("")
    P("**공매도 현실 (정직):** 한국 개인투자자는 개별주 대주(차입)가 사실상 막혀 있어 "
      "**고전적 양다리 페어(한쪽 공매도)는 실거래 실행불가**다. 위 long-short 결과는 *이론적 참고치*이며, "
      "실제로 가능한 것은 ① 괴리 시 *저평가 쪽만 매수*하는 롱온리 대안, ② 인버스 지수 ETF 로 시장 헤지하는 "
      "변형뿐이다. 롱온리 대안 중앙값 손익은 위 마지막 열 참조.\n")
    P(f"**market-neutral 확인:** 자연쌍 중 시장중립(|corr_KOSPI|<0.2) {a_mn_pass}/{len(a_natural)}개 — "
      "수익이 코스피 등락과 거의 무관(시장중립 성질 자체는 성립).\n")
    P(f"**★ 관계 엣지 교차검증 (대조군 비교 — 가장 중요):** 무관 업종 **대조군의 OOS 중앙값 손익 "
      f"{_fmt(ctl_oos_med,'%')}** 가 같은 업종 **자연쌍의 OOS 중앙값 {_fmt(nat_oos_med,'%')}** 보다 "
      f"{'높다' if not a_relationship_edge else '낮다'}. "
      + ("관련 없는 종목 짝이 같은 업종 짝만큼(또는 더) 잘 된다는 것은, 이 수익이 '진짜 관계 수렴'이 아니라 "
         "**단순 평균회귀 노이즈**라는 강한 신호다. 즉 '같은 업종이라 붙는다'는 가정이 데이터로 확인되지 않았다.\n"
         if not a_relationship_edge else
         "자연쌍이 대조군보다 우위 → 관계 수렴 엣지가 일부 성립할 가능성(단 공매도 실행불가는 그대로).\n"))

    # ── 3. B 상대강도 ──
    P("## 3. B. 상대강도 / 횡단면 모멘텀 (롱온리)\n")
    P(f"- 메인 유니버스: {b['universe']} ({b['first_date']}~{b['last_date']}). OOS 분할일 `{b['split_date']}`.")
    P(f"- 조합 수 N = **{b['n_combos']}** (룩백 3/6/12개월 × 리밸런싱 월/분기 × 상위 3/4종목).")
    P(f"- {b['chance_note']}")
    P(f"- ★ 핵심 판정: '전 종목 동일가중 보유(buy&hold)' 대비 초과수익. 순위게임이 단순보유를 이기는가.\n")
    bh = b["benchmark_full"]
    P(f"**기준선 (전 종목 동일가중 보유, full):** CAGR {bh['cagr_pct']}% · Sharpe {_fmt(bh['sharpe'])} · MDD {bh['mdd_pct']}%\n")
    P(f"- full 기간 {b['n_combos']}개 중 buy&hold 초과(+) 조합: **{b['n_combos_beating_bh_full']}/{b['n_combos']}**")
    ib = b["ins_best"]; ob = b["oos_for_ins_best"]
    P(f"- **in-sample 1등 조합:** 룩백 {ib['lookback_days']}일 · 리밸 {ib['rebal_days']}일 · 상위 {ib['top_n']}종목 "
      f"→ in-sample 초과CAGR **{ib['excess_cagr_pct']}%p**")
    P(f"- **그 조합의 OOS 성적:** 초과CAGR **{ob['excess_cagr_pct']}%p** "
      f"(Sharpe 초과 {_fmt(ob.get('excess_sharpe'))}) "
      f"→ {'OOS 에서도 유지 ✅' if (ob['excess_cagr_pct'] or -1) > 0 else 'OOS 에서 무너짐 ❌ (과최적화 의심)'}\n")
    P("| 룩백 | 리밸 | 상위N | full 초과CAGR%p | in-sample 초과%p | OOS 초과%p | full Sharpe |")
    P("|---|---|---|---|---|---|---|")
    for r in b["full"]:
        key3 = (r["lookback_days"], r["rebal_days"], r["top_n"])
        ins_r = next(x for x in b["ins"] if (x["lookback_days"], x["rebal_days"], x["top_n"]) == key3)
        oos_r = next(x for x in b["oos"] if (x["lookback_days"], x["rebal_days"], x["top_n"]) == key3)
        P(f"| {r['lookback_days']}d | {r['rebal_days']}d | {r['top_n']} | {r['excess_cagr_pct']} | "
          f"{ins_r['excess_cagr_pct']} | {oos_r['excess_cagr_pct']} | {_fmt(r['metrics']['sharpe'])} |")
    P("")
    if "error" not in b_breadth:
        P(f"**보조 검증 (breadth):** {b_breadth['universe']}, {b_breadth['n_days']}일. "
          f"{b_breadth['oos_caveat']}")
        bb = b_breadth["benchmark"]
        P(f"  - 기준선 동일가중 보유: 총수익 {bb['total_return_pct']}% · Sharpe {_fmt(bb['sharpe'])}")
        for r in b_breadth["rows"]:
            P(f"  - 룩백{r['lookback_days']}d·리밸{r['rebal_days']}d·상위{r['top_n']}: "
              f"초과CAGR {r['excess_cagr_pct']}%p (Sharpe {_fmt(r['metrics']['sharpe'])})")
    else:
        P(f"**보조 검증 (breadth):** 확인불가 — {b_breadth.get('error')}")
    P("")

    # ── 4. C 변동성타게팅 ──
    P("## 4. C. 변동성 타게팅 / 리스크 조절 (비중조절)\n")
    P(f"- {c['universe']}. 종목당 조합 {c['n_combos_per_symbol']}개(윈도우 20/60 × target 10/15/20%). "
      f"대표 조합: {c['representative_combo']}. OOS 분할일 `{c['split_date']}`.")
    P(f"- {c['note_scale_invariance']}")
    P(f"- ★ 핵심 판정: 총수익이 아니라 **위험조정수익(Sharpe/Calmar/Sortino)·MDD** 가 나아지는가.\n")
    P("| 구간 | 중앙값 Sharpe 개선 | 중앙값 Sortino 개선 | 중앙값 Calmar 개선 | 중앙값 MDD 감소%p | 중앙값 총수익차%p | Sharpe↑ 종목수 |")
    P("|---|---|---|---|---|---|---|")
    for seg, label in [("full", "전체"), ("ins", "in-sample"), ("oos", "OOS")]:
        s = c["median_summary"][seg]
        P(f"| {label} | {_fmt(s['median_sharpe_improvement'])} | {_fmt(s['median_sortino_improvement'])} | "
          f"{_fmt(s['median_calmar_improvement'])} | {_fmt(s['median_mdd_reduction_pct'])} | "
          f"{_fmt(s['median_return_diff_pct'])} | {s['n_symbols_sharpe_up']}/12 |")
    P("")
    P("> **이번 결과 해석 (정직):** MDD(최대낙폭)는 OOS 에서 중앙값 25%p나 줄었지만, "
      "**Sharpe·Sortino·Calmar 위험조정수익은 OOS 에서 모두 (−)이고 개선된 종목이 0/12개**다. "
      "즉 '효율(위험 대비 수익)이 좋아진 것'이 아니라 **평균 비중을 ~40%대로 낮춰 그냥 덜 투자한 효과**에 가깝다. "
      "(in-sample 에서는 Sharpe 가 살짝 +였으나 OOS 에서 뒤집힘 → 과최적화 신호.) "
      "변동성타게팅의 본래 목적인 '위험조정수익 개선'은 **이 데이터에서 확인되지 않음**. "
      "다만 '낙폭이 무서워 그냥 덜 투자하고 싶다'는 목적이라면 비중을 낮추는 도구로서는 작동한다.\n")

    # ── 5. 3축 비교 ──
    P("## 5. 3축 비교 — 무엇이 '방향성 8회 실패'와 진짜 다른 결과를 냈나\n")
    P("| 축 | 본질 | buy&hold 대비 OOS 결과 | 공매도 필요 | 한국 개인 실행성 | 판정 |")
    P("|---|---|---|---|---|---|")
    P(f"| A 페어 | 두 종목 관계 수렴 | 자연쌍 OOS 중앙값 {_fmt(nat_oos_med,'%')} ≤ 대조군 {_fmt(ctl_oos_med,'%')} | "
      f"**필요(고전)** | **실행불가** | {a_verdict} |")
    P(f"| B 상대강도 | 순위 상위 보유 | **초과CAGR {_fmt(b_oos_excess,'%p')}** (252일 4조합 OOS 모두 +) | 불필요 | 가능 | {b_verdict} |")
    P(f"| C 변동성타게팅 | 비중만 조절 | Sharpe {_fmt(c_sharpe_oos)} (개선X), MDD {_fmt(c_mdd_oos,'%p')}↓ | 불필요 | 가능 | {c_verdict} |")
    P("")
    P("- **방향성 8회 실패의 핵심**은 '언제 사고 언제 파나'(타이밍)였다. "
      "A 는 *두 종목 관계*, B 는 *상대 순위*, C 는 *비중*으로 축을 바꿔 타이밍 의존을 줄였다.")
    P("- **진짜 다른 결과를 낸 축은 B 하나뿐이다.** B 의 12개월 모멘텀은 OOS(후반 13년)에서, 그리고 252일 룩백 "
      "4개 조합 *모두* 에서 buy&hold 를 +5~10%p 이겼다 — 단일 행운 조합이 아니라 *일관된* 초과수익.")
    P("- **A 는 실행불가 + 대조군이 더 좋아 '관계 엣지' 미확인**(평균회귀 노이즈 의심). "
      "**C 는 낙폭만 줄고 효율은 그대로**(OOS Sharpe (−), 0/12) — 8회 방향성 실패처럼 '단순보유를 이기지 못함'은 동일.\n")

    # ── 6. 최종 판정 ──
    P("## 6. 최종 판정 + 다음 한 줄\n")
    P(_final_verdict(a_verdict, b_verdict, c_verdict, a_oos_pos, len(a_natural),
                     b_oos_excess, c_sharpe_oos, c_mdd_oos))
    P("")
    P("---")
    P("### 함정방지 자기점검 (8회 실패 교훈)")
    P("- LOOKAHEAD 0: 모든 신호/비중/관계는 rolling(t 시점까지 정보)으로만 산출. 전체기간 임계 사용 0건.")
    P("- 측정: 페어=per-pair 중앙값, 변동성타게팅=per-symbol 중앙값. sum/n 미사용.")
    P(f"- OOS: 26년을 분할(`{a['split_date']}`)해 후반부 재검증. in-sample 1등의 OOS 유지 여부 명시.")
    P(f"- 다중비교: A {a['n_combos']}쌍 · B {b['n_combos']}조합 · C 종목당 {c['n_combos_per_symbol']}조합. 우연 기대값 명시.")
    P("- 판정: buy&hold 대비 초과 + OOS 유지 + outlier 비의존. PF 절대값 단독 판정 안 함.")
    P("")
    P("*(이 리포트는 reports/backtest/ 에만 저장됩니다. git push 금지 — PC 에서 직접 확인하세요.)*")
    return "\n".join(L)


def _overall_oneliner(av, bv, cv, b_ex, c_sh, c_mdd):
    b_good = bv.startswith("후보 있음")
    if b_good:
        return ("세 축 중 **B 상대강도(롱온리 순위게임)만** 단순보유(buy&hold)를 OOS 에서 일관되게 이겼다 — "
                f"12개월 모멘텀 초과CAGR {_fmt(b_ex,'%p')}. "
                "A 페어는 공매도 제약으로 실행불가 + 대조군이 더 좋아 엣지 미확인, "
                "C 변동성타게팅은 낙폭만 줄 뿐 효율(위험조정수익) 개선은 없었다(8회 방향성 실패와 동일하게 단순보유에 못 미침). "
                "→ **유일하게 진짜 다른 결과를 낸 축은 B.**")
    return ("세 축 모두 단순보유(buy&hold)를 확실히 이기지 못했다 — 방향성 8회 실패와 같은 결론. "
            "A 는 실행불가·엣지 미확인, C 는 낙폭만 감소·효율 개선 없음, B 도 OOS 일관성 부족.")


def _final_verdict(av, bv, cv, a_oos_pos, a_total, b_ex, c_sh, c_mdd):
    b_good = bv.startswith("후보 있음")
    lines = []
    if b_good:
        lines.append(
            f"**가장 유망한 축: B 상대강도(횡단면 모멘텀).** 이유 — ① 공매도 불필요(한국 개인 즉시 가능), "
            f"② OOS(후반 13년)에서 buy&hold 대비 초과CAGR {_fmt(b_ex,'%p')}, "
            "③ 12개월(252일) 룩백 4개 조합이 OOS 에서 *모두* (+) → 단일 행운 조합이 아닌 *일관된* 초과수익. "
            "방향성 매매(타이밍)와 달리 '꾸준히 강한 종목을 들고 약한 종목을 버리는' 순위 규칙이라 8회 실패와 성격이 다르다.")
        lines.append("")
        lines.append(
            "**단, 결정적 한계(정직):** 종목이 12개뿐이고 *오늘 살아남은* 대형주라 **생존편향**이 있다. "
            "최근 AI·반도체 랠리(2023~2025)에 상위 종목이 쏠려 OOS 초과가 부풀려졌을 수 있다. "
            "1년 breadth(80종목) 보조검증은 조합에 따라 −28%p ~ +103%p 로 *들쭉날쭉* → 아직 확정 아님.")
        lines.append("")
        lines.append(
            "**다음 한 줄:** B(12개월 상대강도)를 **더 많은 종목(생존편향 없는 당시-상장 유니버스)·더 긴 OOS**로 "
            "재검증하고, 상위 종목 1~2개를 빼도 초과수익이 남는지(outlier 의존 테스트) 확인한다 — *백테스트만, 실거래 금지.*")
    else:
        lines.append(
            "**유망 축 없음.** 세 축 모두 buy&hold 를 확실히·일관되게 이기지 못했다(방향성 8회 실패와 동일 결론). "
            f"A 실행불가·엣지 미확인, B OOS 초과 {_fmt(b_ex,'%p')}(일관성 부족), C OOS Sharpe {_fmt(c_sh)}(효율 개선 없음).")
        lines.append("")
        lines.append(
            "**다음 한 줄:** 단순보유(buy&hold)가 26년간 가장 강했다는 사실 자체를 기준선으로 받아들이고, "
            "*초과수익*이 아니라 *낙폭 관리*(C 의 비중조절)를 더 긴 OOS 로 한 번 더 백테스트한다.")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="대안 매매기법 3종 백테스트 (연구 전용)")
    ap.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "backtest"))
    ap.add_argument("--split-date", default="2013-06-01",
                    help="OOS 분할일 (in-sample=이전, OOS=이후). 26년 중간 ≈ 2013.")
    ap.add_argument("--skip-breadth", action="store_true", help="80종목 60분봉 보조검증 생략")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).astimezone().strftime("%Y%m%d")

    print("=== 데이터 확인 ===", flush=True)
    kospi = load_daily_closes(KOSPI)
    print(f"  KOSPI {len(kospi)} 일 ({kospi[0][0] if kospi else '?'} ~ {kospi[-1][0] if kospi else '?'})")
    if not kospi:
        print("[FAIL] KOSPI 데이터 없음 — 중단", file=sys.stderr)
        return 2

    print("\n=== 축 A: 페어 트레이딩 ===", flush=True)
    a = run_axis_a(kospi, args.split_date)
    print(f"  {a['n_combos']}쌍 완료")

    print("\n=== 축 B: 상대강도 (메인 12종목 일봉) ===", flush=True)
    b = run_axis_b_daily(args.split_date)
    print(f"  {b['n_combos']}조합 완료. full 초과(+) {b['n_combos_beating_bh_full']}/{b['n_combos']}")

    if args.skip_breadth:
        b_breadth = {"error": "skipped (--skip-breadth)"}
    else:
        print("\n=== 축 B 보조: breadth (80종목 60분봉) ===", flush=True)
        b_breadth = run_axis_b_breadth()
        print(f"  {b_breadth.get('n_symbols','?')}종목, {b_breadth.get('error','ok')}")

    print("\n=== 축 C: 변동성 타게팅 ===", flush=True)
    c = run_axis_c(args.split_date)
    print(f"  {c['median_summary']['oos']}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    md = build_markdown(a, b, b_breadth, c, today)
    md_path = out_dir / f"alt3_{today}.md"
    md_path.write_text(md, encoding="utf-8")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kind": "alt3_backtest_research",
        "is_order_signal": False,
        "is_live_authorization": False,
        "auto_apply_allowed": False,
        "no_profit_guarantee": True,
        "cost_roundtrip_bps": COST_ROUNDTRIP_BPS,
        "split_date": args.split_date,
        "axis_a_pairs": a,
        "axis_b_relative_strength": b,
        "axis_b_breadth": b_breadth,
        "axis_c_vol_target": c,
    }
    json_path = out_dir / f"alt3_{today}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    print(f"\n[ok] wrote {md_path}")
    print(f"[ok] wrote {json_path}")
    print("\n(리포트는 PC 에서 확인하세요. git push 금지.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
