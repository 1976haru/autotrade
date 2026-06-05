"""시간축 × 전략 백테스트 스윕 (체크리스트 작업4 / WF — Paper 분석 전용).

목적: "어느 *시간축*(5m / 30m / 60m / 1d) × 어느 *전략*(ORB / Momentum / Gap /
VWAP + Agent Council)" 조합이 *거래비용을 넘어서* PF 1.2+ 를 내는지 발견한다.

설계 (재사용 우선):
- 신호/forward-return 수집은 `strategy_council_backtest.collect_backtest_trades` 를
  *그대로 재사용* — 전략 로직을 재구현하지 않는다.
- 성과지표는 `app.backtest.metrics` 순수 함수 재사용.
- 거래비용은 `app.backtest.cost_model` (수수료 1.5bps×2 + 매도세 18bps + 슬리피지
  5bps×2 = 왕복 31bps) + 미체결 5%(역선택) — 비용 *전/후* PF 를 둘 다 산출한다.

**본 모듈은 분석 전용이다 — 주문을 생성/전송하지 않으며, 결과만으로 실전 전환을
허가하지 않는다.** broker / OrderExecutor / 단일 주문 라우터 / KIS API / 외부 HTTP
import 0건.

일봉(1d) 주의: ORB / 일중 VWAP / Gap 은 *일중* 전략이라 하루 1봉 데이터에서는
신호가 구조적으로 생성되지 않는다 (opening range / 일중 vwap 부재). 따라서 1d 는
"해당 없음(N/A)" 으로 정직히 표시한다 — 억지로 PF 를 만들지 않는다.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

from app.backtest import metrics
from app.backtest.cost_model import (
    DEFAULT_COST,
    UNFILLED_RATE,
    BacktestCostModel,
    cost_drag_bps,
    round_trip_cost_fraction,
)
from app.backtest.strategy_council_backtest import (
    AGENT_COUNCIL,
    CLOSE_HORIZON,
    SINGLE_STRATEGIES,
    BacktestInput,
    BacktestTrade,
    OHLCVBar,
    collect_backtest_trades,
    load_ohlcv_from_csv,
)

ALL_STRATEGIES: tuple[str, ...] = SINGLE_STRATEGIES + (AGENT_COUNCIL,)

PF_TARGET = 1.2   # DoD: 비용 넘어 PF 1.2+ 면 "유망 후보".

SWEEP_OK = "SWEEP_OK"
SWEEP_NOT_APPLICABLE = "SWEEP_NOT_APPLICABLE"   # 1d 에서 일중 전략 신호 0.
SWEEP_INSUFFICIENT_DATA = "SWEEP_INSUFFICIENT_DATA"

DISCLAIMER_KO = (
    "본 리포트는 Paper 성능 분석 전용입니다. 실제 주문/실전 전환이 아니며, "
    "백테스트 결과만으로 실거래 권한을 부여하지 않습니다. 과거 성과는 미래 "
    "수익을 보장하지 않습니다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 비용 적용 + 지표
# ─────────────────────────────────────────────────────────────────────────────


def _net_records(
    buy_trades: Sequence[BacktestTrade], horizon: str, quantity: int,
    *, cost_fraction: float, unfilled_rate: float,
) -> list[dict[str, Any]]:
    """BUY 신호 풀 → metrics.py 호환 record 리스트 (cost_fraction 차감 적용).

    미체결(unfilled): 역선택(adverse selection) 으로 모델링 — net return 상위
    `unfilled_rate` 비율을 "따라가다 놓친 체결" 로 보고 풀에서 제외한다 (보수적).
    원래 순서를 보존해 MDD / 연속손실 의미를 유지한다.
    """
    pairs = [
        (t, float(t.horizon_returns.get(horizon, 0.0)) - cost_fraction)
        for t in buy_trades
    ]
    if unfilled_rate > 0 and pairs:
        k = int(len(pairs) * unfilled_rate)
        if k > 0:
            top_idx = sorted(range(len(pairs)), key=lambda i: pairs[i][1], reverse=True)[:k]
            drop = set(top_idx)
            pairs = [p for i, p in enumerate(pairs) if i not in drop]
    records: list[dict[str, Any]] = []
    for t, net in pairs:
        pnl = int(round(net * t.signal_price * quantity))
        records.append({
            "pnl": pnl, "entry_price": t.signal_price,
            "quantity": quantity, "exit_ts": t.timestamp, "_net": net,
        })
    return records


def _suite(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    pf = metrics.profit_factor(records)
    sh = metrics.sharpe_ratio(records)
    nets = [float(r["_net"]) for r in records]
    return {
        "trade_count":            n,
        "win_rate":               round(metrics.win_rate(records), 6) if n else None,
        "average_return":         round(sum(nets) / n, 6) if n else None,
        "profit_factor":          round(pf, 4) if pf is not None else None,
        "expectancy":             round(metrics.expectancy(records), 4) if n else None,
        "max_drawdown":           int(metrics.max_drawdown(records)),
        "max_consecutive_losses": int(metrics.max_consecutive_losses(records)),
        "sharpe_ratio":           round(sh, 4) if sh is not None else None,
    }


@dataclass(frozen=True)
class StrategyTimeframeResult:
    strategy:      str
    signal_counts: dict[str, int]
    before_cost:   dict[str, Any]
    after_cost:    dict[str, Any]

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":      self.strategy,
            "signal_counts": dict(self.signal_counts),
            "before_cost":   dict(self.before_cost),
            "after_cost":    dict(self.after_cost),
            "is_order_signal": False,
            "is_live_authorization": False,
        }


@dataclass(frozen=True)
class TimeframeSweepResult:
    timeframe:        str
    horizon:          str
    symbol_count:     int
    bar_count:        int
    bars_per_day_median: float
    applicable:       bool
    reason_code:      str
    note:             str
    strategies:       dict[str, StrategyTimeframeResult]
    council_vs_best_single: dict[str, Any]
    cost_round_trip_bps: float = field(default_factory=cost_drag_bps)
    unfilled_rate:    float = UNFILLED_RATE

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization",
                     "auto_apply_allowed", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (스윕은 분석 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe":           self.timeframe,
            "horizon":             self.horizon,
            "symbol_count":        int(self.symbol_count),
            "bar_count":           int(self.bar_count),
            "bars_per_day_median": self.bars_per_day_median,
            "applicable":          bool(self.applicable),
            "reason_code":         self.reason_code,
            "note":                self.note,
            "strategies":          {k: v.to_dict() for k, v in self.strategies.items()},
            "council_vs_best_single": dict(self.council_vs_best_single),
            "cost_round_trip_bps": self.cost_round_trip_bps,
            "unfilled_rate":       self.unfilled_rate,
            "is_order_signal": False,
            "is_live_authorization": False,
            "auto_apply_allowed": False,
            "contains_secret": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 스윕 실행
# ─────────────────────────────────────────────────────────────────────────────


def _bars_per_day_median(bars: Sequence[OHLCVBar]) -> float:
    from collections import defaultdict
    from datetime import timedelta
    kst = timezone(timedelta(hours=9))
    counts: dict[tuple[str, Any], int] = defaultdict(int)
    for b in bars:
        counts[(b.symbol, b.timestamp.astimezone(kst).date())] += 1
    vals = sorted(counts.values())
    if not vals:
        return 0.0
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return (vals[mid - 1] + vals[mid]) / 2.0


def run_timeframe_sweep(
    timeframe: str, bars: Sequence[OHLCVBar], *,
    horizon: str = CLOSE_HORIZON,
    risk_profile: str = "BALANCED",
    horizons: tuple[int, ...] = (5, 10, 30, 60),
    quantity: int = 1,
    cost_model: BacktestCostModel | None = None,
    unfilled_rate: float = UNFILLED_RATE,
) -> TimeframeSweepResult:
    """한 시간축의 4전략 + Agent Council 백테스트 (비용 전/후)."""
    bars = list(bars)
    symbol_count = len({b.symbol for b in bars})
    bar_count = len(bars)
    bpd = _bars_per_day_median(bars)
    cost_fraction = round_trip_cost_fraction(cost_model or DEFAULT_COST)

    inp = BacktestInput(
        bars=tuple(bars), risk_profile=risk_profile, horizons=horizons,
        quantity=quantity, primary_horizon=horizon,
    )
    horizon = horizon if horizon in inp.horizon_labels() else CLOSE_HORIZON

    collected = collect_backtest_trades(inp)

    strat_results: dict[str, StrategyTimeframeResult] = {}
    pools: dict[str, list[BacktestTrade]] = dict(collected.strat_trades)
    pools[AGENT_COUNCIL] = collected.council_trades

    total_buy = 0
    for name in ALL_STRATEGIES:
        trades = pools.get(name, [])
        counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
        buys: list[BacktestTrade] = []
        for t in trades:
            counts[t.signal] = counts.get(t.signal, 0) + 1
            if t.signal == "BUY":
                buys.append(t)
        total_buy += len(buys)
        before = _suite(_net_records(buys, horizon, quantity, cost_fraction=0.0, unfilled_rate=0.0))
        after = _suite(_net_records(buys, horizon, quantity,
                                    cost_fraction=cost_fraction, unfilled_rate=unfilled_rate))
        strat_results[name] = StrategyTimeframeResult(
            strategy=name, signal_counts=counts, before_cost=before, after_cost=after,
        )

    # 적용 가능성: 일중 전략이 BUY 신호를 전혀 못 만들면 (예: 1d) N/A.
    applicable = total_buy > 0
    if bar_count < 100:
        reason = SWEEP_INSUFFICIENT_DATA
        note = f"bar 수 {bar_count} < 100 — 표본 부족."
    elif not applicable:
        reason = SWEEP_NOT_APPLICABLE
        note = ("일중 전략(ORB/VWAP/Gap)은 하루 1봉(일봉)에서 신호가 구조적으로 "
                "생성되지 않습니다 — 시간축 N/A (억지 PF 없음).")
    else:
        reason = SWEEP_OK
        note = ""

    cvs = _council_vs_best_single(strat_results, horizon)

    return TimeframeSweepResult(
        timeframe=timeframe, horizon=horizon, symbol_count=symbol_count,
        bar_count=bar_count, bars_per_day_median=bpd, applicable=applicable,
        reason_code=reason, note=note, strategies=strat_results,
        council_vs_best_single=cvs,
        cost_round_trip_bps=cost_drag_bps(cost_model or DEFAULT_COST),
        unfilled_rate=unfilled_rate,
    )


def _council_vs_best_single(
    strat_results: dict[str, StrategyTimeframeResult], horizon: str,
) -> dict[str, Any]:
    """Agent Council 이 최우수 단일 전략보다 나은지 (after-cost expectancy)."""
    def _exp(name: str) -> float:
        v = strat_results[name].after_cost.get("expectancy")
        return v if v is not None else float("-inf")

    singles = [s for s in SINGLE_STRATEGIES if s in strat_results]
    ranked = sorted(singles, key=_exp, reverse=True)
    best = ranked[0] if ranked else None
    best_exp = strat_results[best].after_cost.get("expectancy") if best else None
    council_exp = strat_results.get(AGENT_COUNCIL, None)
    council_exp_val = council_exp.after_cost.get("expectancy") if council_exp else None
    better = (
        council_exp_val is not None and best_exp is not None
        and council_exp_val >= best_exp
    )
    delta = (council_exp_val - best_exp) if (council_exp_val is not None and best_exp is not None) else None
    return {
        "metric": "after_cost_expectancy",
        "horizon": horizon,
        "council_expectancy": council_exp_val,
        "best_single_strategy": best,
        "best_single_expectancy": best_exp,
        "council_better_than_best_single": bool(better),
        "expectancy_delta": round(delta, 4) if delta is not None else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 디렉토리 로딩 + 다중 시간축 스윕
# ─────────────────────────────────────────────────────────────────────────────


def load_dir_bars(
    dir_path: str, *, glob_pat: str = "*.csv",
    symbols: set[str] | None = None,
) -> list[OHLCVBar]:
    """디렉토리 내 종목 CSV 들을 OHLCVBar 로 로드 (symbols 지정 시 필터)."""
    bars: list[OHLCVBar] = []
    for f in sorted(glob.glob(os.path.join(dir_path, glob_pat))):
        stem = os.path.splitext(os.path.basename(f))[0]
        sym = stem.replace("_5m", "").replace("_30m", "").replace("_60m", "").replace("_1d", "")
        if symbols is not None and sym not in symbols:
            continue
        try:
            bars.extend(load_ohlcv_from_csv(f))
        except Exception:  # noqa: BLE001
            continue
    return bars


@dataclass(frozen=True)
class SweepMatrix:
    """시간축 × 전략 매트릭스 (여러 TimeframeSweepResult 묶음)."""

    generated_at: str
    results:      dict[str, TimeframeSweepResult]   # timeframe → result
    disclaimer:   str = DISCLAIMER_KO

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False
    contains_secret:       bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization",
                     "auto_apply_allowed", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False")

    def matrix_rows(self) -> list[dict[str, Any]]:
        """(timeframe, strategy) 별 after-cost 핵심 지표 행 — 리포트 표 / 랭킹용."""
        rows: list[dict[str, Any]] = []
        for tf, res in self.results.items():
            for name in ALL_STRATEGIES:
                sr = res.strategies.get(name)
                if sr is None:
                    continue
                bc = sr.before_cost
                ac = sr.after_cost
                rows.append({
                    "timeframe":          tf,
                    "strategy":           name,
                    "applicable":         res.applicable,
                    "buy_count":          sr.signal_counts.get("BUY", 0),
                    "pf_before":          bc.get("profit_factor"),
                    "pf_after":           ac.get("profit_factor"),
                    "win_rate_after":     ac.get("win_rate"),
                    "expectancy_after":   ac.get("expectancy"),
                    "avg_return_after":   ac.get("average_return"),
                    "mdd_after":          ac.get("max_drawdown"),
                    "sharpe_after":       ac.get("sharpe_ratio"),
                    "meets_pf_target":    (ac.get("profit_factor") is not None
                                           and ac.get("profit_factor") >= PF_TARGET
                                           and res.applicable),
                })
        return rows

    def promising_combos(self) -> list[dict[str, Any]]:
        """after-cost PF ≥ 1.2 인 (timeframe, strategy) 조합 — PF 내림차순."""
        rows = [r for r in self.matrix_rows() if r["meets_pf_target"]]
        rows.sort(key=lambda r: (r["pf_after"] or 0.0), reverse=True)
        return rows

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "results": {tf: r.to_dict() for tf, r in self.results.items()},
            "matrix_rows": self.matrix_rows(),
            "promising_combos": self.promising_combos(),
            "pf_target": PF_TARGET,
            "disclaimer": self.disclaimer,
            "is_order_signal": False,
            "is_live_authorization": False,
            "auto_apply_allowed": False,
            "contains_secret": False,
        }


def run_sweep_matrix(
    timeframe_bars: dict[str, Sequence[OHLCVBar]], *,
    horizon: str = CLOSE_HORIZON,
    risk_profile: str = "BALANCED",
    quantity: int = 1,
    cost_model: BacktestCostModel | None = None,
    unfilled_rate: float = UNFILLED_RATE,
) -> SweepMatrix:
    """여러 시간축의 bar 묶음 → 시간축 × 전략 매트릭스."""
    results: dict[str, TimeframeSweepResult] = {}
    for tf, bars in timeframe_bars.items():
        results[tf] = run_timeframe_sweep(
            tf, bars, horizon=horizon, risk_profile=risk_profile,
            quantity=quantity, cost_model=cost_model, unfilled_rate=unfilled_rate,
        )
    return SweepMatrix(
        generated_at=datetime.now(timezone.utc).isoformat(), results=results,
    )


__all__ = [
    "ALL_STRATEGIES", "PF_TARGET",
    "SWEEP_OK", "SWEEP_NOT_APPLICABLE", "SWEEP_INSUFFICIENT_DATA",
    "StrategyTimeframeResult", "TimeframeSweepResult", "SweepMatrix",
    "run_timeframe_sweep", "run_sweep_matrix",
    "load_dir_bars",
]
