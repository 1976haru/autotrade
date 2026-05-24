"""#46 / 6-01: 4전략 + Agent Council 백테스트 (Paper 성능 분석 전용).

ORB / Momentum / Gap / VWAP 4 전략의 vote 와 Agent Council 의 final_action 을
*과거 OHLCV 데이터* 로 평가해, 전략별 / Council 별 성과지표를 산출한다.

**본 모듈은 *성능 검증용 백테스트* 다 — 실제 주문을 생성/전송하지 않으며,
백테스트 결과만으로 실전 전환(live promotion)을 허가하지 않는다.**

핵심 설계:
- 각 bar 에서 `StrategyMarketInput` 을 구성해 4 전략 evaluator + `run_agent_council`
  을 *그대로 재사용* (deterministic, broker 호출 0건).
- 신호 시점(close) 대비 *forward return* 을 horizon(5/10/30/60 bar + 당일 close)
  별로 계산해 BUY/SELL/HOLD 로 라벨링.
- **SELL 은 신규 숏으로 해석하지 않는다** — "보유 청산 신호 / 하락 방향 판단
  평가" 로 *분리* 표시(directional hit-rate). BUY 손익 풀에 섞지 않는다.
- 성과지표(win_rate / 평균수익·손실 / 손익비 / profit_factor / MDD / 연속손실 /
  expectancy)는 `app.backtest.metrics` 의 순수 함수를 재사용.
- market_regime / time_phase 별 버킷 + Agent Council vs best single 비교.

CLAUDE.md 가드 (정적 grep 으로 lock):
- broker / OrderExecutor / 단일 주문 라우터 / paper_trader / KIS 어댑터 import 0건.
- broker 주문/취소 호출 0건 (place·cancel·route 경로 미사용).
- 외부 HTTP / AI SDK (anthropic/openai/httpx/requests) import 0건.
- `BacktestReport.is_order_signal=False` / `is_live_authorization=False` /
  `auto_apply_allowed=False` / `contains_secret=False` 불변.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from app.agents.agent_council import (
    StrategyMarketInput,
    evaluate_gap,
    evaluate_momentum,
    evaluate_orb,
    evaluate_vwap,
    run_agent_council,
)
from app.backtest.metrics import (
    avg_loss,
    avg_win,
    expectancy,
    max_consecutive_losses,
    max_drawdown,
    profit_factor,
    win_rate,
)

_KST = timezone(timedelta(hours=9))

SINGLE_STRATEGIES: tuple[str, ...] = ("ORB", "MOMENTUM", "GAP", "VWAP")
AGENT_COUNCIL = "AGENT_COUNCIL"

# horizon 라벨(= bar offset). "close" 는 당일 마지막 bar 까지 보유.
DEFAULT_HORIZON_BARS: tuple[int, ...] = (5, 10, 30, 60)
CLOSE_HORIZON = "close"

# reason_code.
BACKTEST_OK = "BACKTEST_OK"
BACKTEST_INSUFFICIENT_DATA = "BACKTEST_INSUFFICIENT_DATA"
BACKTEST_NO_SIGNALS = "BACKTEST_NO_SIGNALS"

DISCLAIMER_KO = (
    "본 리포트는 Paper 성능 분석 전용입니다. 실제 주문/실전 전환이 아니며, "
    "백테스트 결과만으로 실거래 권한을 부여하지 않습니다. 과거 성과는 미래 "
    "수익을 보장하지 않습니다."
)


# ─────────────────────────────────────────────────────────────────────────────
# 입력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OHLCVBar:
    """백테스트 입력 bar — OHLCV + 선택 메타(vwap / market_regime / time_phase)."""

    symbol:        str
    timestamp:     datetime
    open:          float
    high:          float
    low:           float
    close:         float
    volume:        float
    vwap:          float | None = None
    market_regime: str | None = None
    time_phase:    str | None = None
    gap_pct:       float | None = None


@dataclass(frozen=True)
class BacktestInput:
    """백테스트 실행 입력."""

    bars:                 tuple[OHLCVBar, ...]
    risk_profile:         str = "BALANCED"
    horizons:             tuple[int, ...] = DEFAULT_HORIZON_BARS
    quantity:             int = 1
    opening_range_bars:   int = 3
    recent_closes_window: int = 5
    primary_horizon:      str = CLOSE_HORIZON
    # SELL 평가 모드 — 보유 청산/하락 방향 *판단* 평가용 (신규 숏 아님).
    council_eval_held_position: bool = True

    def horizon_labels(self) -> tuple[str, ...]:
        return tuple(f"h{h}" for h in self.horizons) + (CLOSE_HORIZON,)


# ─────────────────────────────────────────────────────────────────────────────
# 출력 DTO
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BacktestTrade:
    """한 신호 시점의 평가 결과 — *주문이 아니다*."""

    symbol:           str
    timestamp:        str            # ISO
    strategy:         str            # ORB / MOMENTUM / GAP / VWAP / AGENT_COUNCIL
    signal:           str            # BUY / SELL / HOLD
    signal_price:     float
    horizon_returns:  dict[str, float]
    primary_return:   float
    market_regime:    str
    time_phase:       str
    confidence:       float
    score:            float
    # council 전용 carry (단일 전략은 비움).
    selected_strategies: tuple[str, ...] = ()
    quality_score:    float = 0.0

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol":              self.symbol,
            "timestamp":           self.timestamp,
            "strategy":            self.strategy,
            "signal":              self.signal,
            "signal_price":        round(float(self.signal_price), 4),
            "horizon_returns":     {k: round(float(v), 6) for k, v in self.horizon_returns.items()},
            "primary_return":      round(float(self.primary_return), 6),
            "market_regime":       self.market_regime,
            "time_phase":          self.time_phase,
            "confidence":          round(float(self.confidence), 4),
            "score":               round(float(self.score), 2),
            "selected_strategies": list(self.selected_strategies),
            "quality_score":       round(float(self.quality_score), 2),
            "is_order_signal":     False,
            "is_live_authorization": False,
        }


def _metric_suite(buy_trades: Sequence[BacktestTrade], horizon: str, quantity: int) -> dict[str, Any]:
    """BUY 신호의 forward-return 풀에서 성과지표 산출 (metrics.py 재사용)."""
    records = []
    rets: list[float] = []
    for t in buy_trades:
        ret = float(t.horizon_returns.get(horizon, 0.0))
        rets.append(ret)
        pnl = int(round(ret * t.signal_price * quantity))
        records.append({
            "pnl": pnl,
            "entry_price": t.signal_price,
            "quantity": quantity,
            "exit_ts": t.timestamp,
        })
    n = len(records)
    aw = avg_win(records)
    al = avg_loss(records)
    payoff = (aw / abs(al)) if al != 0 else None
    return {
        "trade_count":           n,
        "win_rate":              round(win_rate(records), 6) if n else None,
        "average_return":        round(sum(rets) / n, 6) if n else None,
        "average_win":           round(aw, 4) if n else None,
        "average_loss":          round(al, 4) if n else None,
        "payoff_ratio":          round(payoff, 4) if payoff is not None else None,
        "profit_factor":         (round(pf, 4) if (pf := profit_factor(records)) is not None else None),
        "max_drawdown":          int(max_drawdown(records)),
        "max_consecutive_losses": int(max_consecutive_losses(records)),
        "expectancy":            round(expectancy(records), 4) if n else None,
    }


def _bucket(buy_trades: Sequence[BacktestTrade], key: str, horizon: str) -> dict[str, dict[str, Any]]:
    """market_regime / time_phase 별 win_rate + count + 평균수익 버킷."""
    groups: dict[str, list[BacktestTrade]] = {}
    for t in buy_trades:
        bk = getattr(t, key) or "UNKNOWN"
        groups.setdefault(bk, []).append(t)
    out: dict[str, dict[str, Any]] = {}
    for bk, items in sorted(groups.items()):
        rets = [float(i.horizon_returns.get(horizon, 0.0)) for i in items]
        wins = sum(1 for r in rets if r > 0)
        out[bk] = {
            "count":          len(items),
            "win_rate":       round(wins / len(items), 6) if items else None,
            "average_return": round(sum(rets) / len(rets), 6) if rets else None,
        }
    return out


@dataclass(frozen=True)
class StrategyBacktestResult:
    """단일 전략(또는 Council)의 백테스트 성과 — 분석 전용."""

    strategy:        str
    signal_counts:   dict[str, int]            # {BUY, SELL, HOLD}
    by_horizon:      dict[str, dict[str, Any]]  # horizon → metric suite (BUY 풀)
    primary_horizon: str
    # primary horizon headline (BUY 풀):
    win_rate:                float | None
    average_return:          float | None
    average_win:             float | None
    average_loss:            float | None
    payoff_ratio:            float | None
    profit_factor:           float | None
    max_drawdown:            int
    max_consecutive_losses:  int
    expectancy:              float | None
    # SELL 은 보유 청산/하락 방향 판단 평가 (신규 숏 아님).
    sell_direction:  dict[str, Any]
    by_market_regime: dict[str, dict[str, Any]]
    by_time_phase:    dict[str, dict[str, Any]]
    reason_code:      str = BACKTEST_OK

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":                self.strategy,
            "signal_counts":           dict(self.signal_counts),
            "by_horizon":              {k: dict(v) for k, v in self.by_horizon.items()},
            "primary_horizon":         self.primary_horizon,
            "win_rate":                self.win_rate,
            "average_return":          self.average_return,
            "average_win":             self.average_win,
            "average_loss":            self.average_loss,
            "payoff_ratio":            self.payoff_ratio,
            "profit_factor":           self.profit_factor,
            "max_drawdown":            self.max_drawdown,
            "max_consecutive_losses":  self.max_consecutive_losses,
            "expectancy":              self.expectancy,
            "sell_direction":          dict(self.sell_direction),
            "by_market_regime":        {k: dict(v) for k, v in self.by_market_regime.items()},
            "by_time_phase":           {k: dict(v) for k, v in self.by_time_phase.items()},
            "reason_code":             self.reason_code,
            "is_order_signal":         False,
            "is_live_authorization":   False,
        }


@dataclass(frozen=True)
class CouncilBacktestResult:
    """Agent Council final_action 백테스트 성과 + 단일 전략 대비 carry."""

    performance:            StrategyBacktestResult
    final_action_counts:    dict[str, int]
    avg_confidence:         float | None
    avg_quality_score:      float | None
    selected_strategies_freq: dict[str, int]

    is_order_signal:       bool = False
    is_live_authorization: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "performance":              self.performance.to_dict(),
            "final_action_counts":      dict(self.final_action_counts),
            "avg_confidence":           self.avg_confidence,
            "avg_quality_score":        self.avg_quality_score,
            "selected_strategies_freq": dict(self.selected_strategies_freq),
            "is_order_signal":          False,
            "is_live_authorization":    False,
        }


@dataclass(frozen=True)
class BacktestReport:
    """4전략 + Agent Council 백테스트 종합 리포트 — Paper 성능 분석 전용."""

    generated_at:    str
    symbol_count:    int
    bar_count:       int
    risk_profile:    str
    horizon_labels:  tuple[str, ...]
    primary_horizon: str
    strategies:      dict[str, StrategyBacktestResult]
    council:         CouncilBacktestResult | None
    comparison:      dict[str, Any]
    reason_code:     str
    insufficient_data: bool

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    auto_apply_allowed:    bool = False
    contains_secret:       bool = False
    disclaimer:            str = DISCLAIMER_KO

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_live_authorization",
                     "auto_apply_allowed", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (백테스트는 분석 전용)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at":     self.generated_at,
            "symbol_count":     int(self.symbol_count),
            "bar_count":        int(self.bar_count),
            "risk_profile":     self.risk_profile,
            "horizon_labels":   list(self.horizon_labels),
            "primary_horizon":  self.primary_horizon,
            "strategies":       {k: v.to_dict() for k, v in self.strategies.items()},
            "council":          self.council.to_dict() if self.council else None,
            "comparison":       dict(self.comparison),
            "reason_code":      self.reason_code,
            "insufficient_data": bool(self.insufficient_data),
            "is_order_signal":       False,
            "is_live_authorization": False,
            "auto_apply_allowed":    False,
            "contains_secret":       False,
            "disclaimer":            self.disclaimer,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 입력 로딩
# ─────────────────────────────────────────────────────────────────────────────


def _parse_ts(raw: Any) -> datetime:
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_ohlcv_from_records(
    records: Iterable[dict[str, Any]], *, default_symbol: str = "TEST",
) -> list[OHLCVBar]:
    """dict 레코드 → OHLCVBar 리스트. 중복 (symbol, timestamp) 제거, 시간순 정렬."""
    seen: set[tuple[str, str]] = set()
    bars: list[OHLCVBar] = []
    for r in records:
        sym = str(r.get("symbol") or default_symbol).strip()
        ts_raw = r.get("timestamp")
        if ts_raw is None:
            continue
        try:
            ts = _parse_ts(ts_raw)
        except (ValueError, TypeError):
            continue
        key = (sym, ts.isoformat())
        if key in seen:
            continue
        try:
            o = float(r["open"])
            h = float(r["high"])
            lo = float(r["low"])
            c = float(r["close"])
            vol = float(r.get("volume", 0) or 0)
        except (KeyError, ValueError, TypeError):
            continue
        seen.add(key)

        def _optf(k):
            v = r.get(k)
            try:
                return float(v) if v not in (None, "") else None
            except (ValueError, TypeError):
                return None

        def _opts(k):
            v = r.get(k)
            return str(v).strip().upper() if v not in (None, "") else None

        bars.append(OHLCVBar(
            symbol=sym, timestamp=ts, open=o, high=h, low=lo, close=c, volume=vol,
            vwap=_optf("vwap"), market_regime=_opts("market_regime"),
            time_phase=_opts("time_phase"), gap_pct=_optf("gap_pct"),
        ))
    bars.sort(key=lambda b: (b.symbol, b.timestamp))
    return bars


def load_ohlcv_from_csv(path: str, *, default_symbol: str = "TEST") -> list[OHLCVBar]:
    """CSV → OHLCVBar 리스트. 헤더: timestamp,open,high,low,close,volume[,vwap,...]."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(r) for r in reader]
    # symbol 컬럼이 없으면 파일명 stem 을 default 로.
    if rows and not any("symbol" in r for r in rows):
        import os
        default_symbol = os.path.splitext(os.path.basename(path))[0] or default_symbol
    return load_ohlcv_from_records(rows, default_symbol=default_symbol)


# ─────────────────────────────────────────────────────────────────────────────
# bar → StrategyMarketInput 구성
# ─────────────────────────────────────────────────────────────────────────────


def _time_phase_for(ts: datetime) -> str:
    kst = ts.astimezone(_KST)
    minutes = kst.hour * 60 + kst.minute
    if minutes < 9 * 60:
        return "PRE_MARKET"
    if minutes < 9 * 60 + 30:
        return "OPENING_RANGE"
    if minutes < 11 * 60 + 30:
        return "MORNING"
    if minutes < 13 * 60 + 30:
        return "MIDDAY"
    if minutes < 15 * 60 + 30:
        return "CLOSING"
    return "AFTER_MARKET"


def _derive_regime(recent_closes: Sequence[float]) -> str:
    if len(recent_closes) < 3 or recent_closes[0] <= 0:
        return "UNKNOWN"
    ret = (recent_closes[-1] - recent_closes[0]) / recent_closes[0]
    if ret > 0.01:
        return "TREND_UP"
    if ret < -0.01:
        return "TREND_DOWN"
    return "SIDEWAYS"


def _intraday_vwap(day_bars: Sequence[OHLCVBar], upto_idx: int) -> float | None:
    num = 0.0
    den = 0.0
    for b in day_bars[: upto_idx + 1]:
        typical = (b.high + b.low + b.close) / 3.0
        num += typical * b.volume
        den += b.volume
    if den <= 0:
        return None
    return num / den


# ─────────────────────────────────────────────────────────────────────────────
# 백테스트 실행
# ─────────────────────────────────────────────────────────────────────────────


def _group_by_day(bars: Sequence[OHLCVBar]) -> list[list[OHLCVBar]]:
    """(symbol, KST date) 별로 그룹핑 — 일중 horizon / opening range 기준."""
    groups: dict[tuple[str, Any], list[OHLCVBar]] = {}
    order: list[tuple[str, Any]] = []
    for b in bars:
        key = (b.symbol, b.timestamp.astimezone(_KST).date())
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(b)
    return [groups[k] for k in order]


def _forward_returns(
    day_bars: Sequence[OHLCVBar], i: int, horizons: Sequence[int],
) -> tuple[dict[str, float], int]:
    """bar i 의 forward return (horizon bar offset + 당일 close). day_end 로 clamp."""
    base = day_bars[i].close
    day_end = len(day_bars) - 1
    out: dict[str, float] = {}
    if base <= 0:
        return out, day_end
    for h in horizons:
        tgt = min(i + h, day_end)
        out[f"h{h}"] = (day_bars[tgt].close - base) / base
    out[CLOSE_HORIZON] = (day_bars[day_end].close - base) / base
    return out, day_end


def _build_input(
    day_bars: Sequence[OHLCVBar], i: int, *, opening_range_bars: int,
    recent_closes_window: int, prev_close: float | None,
) -> StrategyMarketInput:
    b = day_bars[i]
    # opening range — 당일 첫 N bar 의 high/low (N bar 확정 후에만 제공).
    orh = orl = None
    if i >= opening_range_bars - 1:
        window = day_bars[:opening_range_bars]
        orh = max(x.high for x in window)
        orl = min(x.low for x in window)
    # recent closes (전체 시계열 아닌 당일 기준, window).
    start = max(0, i - recent_closes_window + 1)
    recent = tuple(x.close for x in day_bars[start: i + 1])
    vwap = b.vwap if b.vwap is not None else _intraday_vwap(day_bars, i)
    regime = b.market_regime or _derive_regime(recent)
    return StrategyMarketInput(
        symbol=b.symbol,
        current_price=b.close,
        prev_close=prev_close,
        open_price=day_bars[0].open,
        vwap=vwap,
        opening_range_high=orh,
        opening_range_low=orl,
        recent_closes=recent,
        current_volume=b.volume,
        avg_volume=(sum(x.volume for x in day_bars[: i + 1]) / (i + 1)) if i >= 0 else None,
        market_regime=regime,
        regime_decision="ALLOW",
    )


def run_strategy_council_backtest(inp: BacktestInput) -> BacktestReport:
    """4전략 vote + Agent Council final_action 을 과거 데이터로 평가."""
    now_iso = datetime.now(timezone.utc).isoformat()
    bars = list(inp.bars)
    symbols = {b.symbol for b in bars}
    horizon_labels = inp.horizon_labels()
    primary = inp.primary_horizon if inp.primary_horizon in horizon_labels else CLOSE_HORIZON

    # 데이터 부족 판정: 최소 (opening_range + recent + 가장 큰 horizon + 1).
    min_needed = inp.opening_range_bars + inp.recent_closes_window + (max(inp.horizons) if inp.horizons else 1) + 1
    if len(bars) < min_needed:
        return _empty_report(now_iso, len(symbols), len(bars), inp, horizon_labels,
                             primary, BACKTEST_INSUFFICIENT_DATA, insufficient=True)

    days = _group_by_day(bars)
    # 전략별 trade 수집 + council trade 수집.
    strat_trades: dict[str, list[BacktestTrade]] = {s: [] for s in SINGLE_STRATEGIES}
    council_trades: list[BacktestTrade] = []
    council_confidences: list[float] = []
    council_qualities: list[float] = []
    council_final_counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    selected_freq: dict[str, int] = {s: 0 for s in SINGLE_STRATEGIES}

    prev_day_close: dict[str, float | None] = {}
    for day_bars in days:
        sym = day_bars[0].symbol
        prev_close = prev_day_close.get(sym)
        day_end = len(day_bars) - 1
        for i in range(len(day_bars)):
            if i >= day_end:
                break  # 마지막 bar 는 forward return 없음 — 신호 생성 안 함.
            fwd, _ = _forward_returns(day_bars, i, inp.horizons)
            if not fwd:
                continue
            mi = _build_input(
                day_bars, i, opening_range_bars=inp.opening_range_bars,
                recent_closes_window=inp.recent_closes_window, prev_close=prev_close,
            )
            tphase = day_bars[i].time_phase or _time_phase_for(day_bars[i].timestamp)
            ts_iso = day_bars[i].timestamp.isoformat()

            # 1) 4 전략 vote.
            for strat, ev in (("ORB", evaluate_orb), ("MOMENTUM", evaluate_momentum),
                              ("GAP", evaluate_gap), ("VWAP", evaluate_vwap)):
                vote = ev(mi)
                strat_trades[strat].append(BacktestTrade(
                    symbol=sym, timestamp=ts_iso, strategy=strat,
                    signal=vote.signal.value, signal_price=day_bars[i].close,
                    horizon_returns=fwd, primary_return=fwd.get(primary, 0.0),
                    market_regime=mi.market_regime, time_phase=tphase,
                    confidence=vote.confidence, score=vote.score,
                ))

            # 2) Agent Council final_action (held_position 은 directional 평가용).
            decision = run_agent_council(
                mi, risk_profile=inp.risk_profile,
                held_position=inp.council_eval_held_position,
            )
            fa = decision.final_action.value
            council_final_counts[fa] = council_final_counts.get(fa, 0) + 1
            council_confidences.append(float(decision.confidence))
            council_qualities.append(float(decision.quality_score))
            for s in decision.selected_strategies:
                if s in selected_freq:
                    selected_freq[s] += 1
            council_trades.append(BacktestTrade(
                symbol=sym, timestamp=ts_iso, strategy=AGENT_COUNCIL,
                signal=fa, signal_price=day_bars[i].close,
                horizon_returns=fwd, primary_return=fwd.get(primary, 0.0),
                market_regime=mi.market_regime, time_phase=tphase,
                confidence=float(decision.confidence), score=float(decision.quality_score),
                selected_strategies=tuple(decision.selected_strategies),
                quality_score=float(decision.quality_score),
            ))
        prev_day_close[sym] = day_bars[day_end].close

    strategies = {
        s: evaluate_strategy_vote_performance(strat_trades[s], strategy=s,
                                              primary_horizon=primary,
                                              horizon_labels=horizon_labels,
                                              quantity=inp.quantity)
        for s in SINGLE_STRATEGIES
    }
    council = evaluate_agent_council_performance(
        council_trades, primary_horizon=primary, horizon_labels=horizon_labels,
        quantity=inp.quantity, final_action_counts=council_final_counts,
        confidences=council_confidences, qualities=council_qualities,
        selected_freq=selected_freq,
    )
    comparison = _compare_council_vs_single(strategies, council, primary)

    total_signals = sum(len(v) for v in strat_trades.values()) + len(council_trades)
    reason = BACKTEST_OK if total_signals > 0 else BACKTEST_NO_SIGNALS

    return BacktestReport(
        generated_at=now_iso, symbol_count=len(symbols), bar_count=len(bars),
        risk_profile=inp.risk_profile, horizon_labels=horizon_labels,
        primary_horizon=primary, strategies=strategies, council=council,
        comparison=comparison, reason_code=reason, insufficient_data=False,
    )


def evaluate_strategy_vote_performance(
    trades: Sequence[BacktestTrade], *, strategy: str, primary_horizon: str,
    horizon_labels: Sequence[str], quantity: int,
) -> StrategyBacktestResult:
    """한 전략의 신호 모음 → 성과지표(BUY 풀) + SELL 방향 hit-rate + 버킷."""
    counts = {"BUY": 0, "SELL": 0, "HOLD": 0}
    buys: list[BacktestTrade] = []
    sells: list[BacktestTrade] = []
    for t in trades:
        counts[t.signal] = counts.get(t.signal, 0) + 1
        if t.signal == "BUY":
            buys.append(t)
        elif t.signal == "SELL":
            sells.append(t)

    by_horizon = {h: _metric_suite(buys, h, quantity) for h in horizon_labels}
    headline = by_horizon.get(primary_horizon, _metric_suite(buys, primary_horizon, quantity))

    # SELL = 보유 청산/하락 방향 판단 평가 (신규 숏 아님): 가격 하락이면 적중.
    sell_rets = [float(t.horizon_returns.get(primary_horizon, 0.0)) for t in sells]
    sell_hits = sum(1 for r in sell_rets if r < 0)
    sell_direction = {
        "interpretation": "보유 청산 신호 / 하락 방향 판단 평가 (신규 숏 아님)",
        "count":            len(sells),
        "down_hit_count":   sell_hits,
        "down_hit_rate":    round(sell_hits / len(sells), 6) if sells else None,
        "average_forward_return": round(sum(sell_rets) / len(sell_rets), 6) if sell_rets else None,
    }

    reason = BACKTEST_OK if (counts["BUY"] + counts["SELL"]) > 0 else BACKTEST_NO_SIGNALS
    return StrategyBacktestResult(
        strategy=strategy, signal_counts=counts, by_horizon=by_horizon,
        primary_horizon=primary_horizon,
        win_rate=headline["win_rate"], average_return=headline["average_return"],
        average_win=headline["average_win"], average_loss=headline["average_loss"],
        payoff_ratio=headline["payoff_ratio"], profit_factor=headline["profit_factor"],
        max_drawdown=headline["max_drawdown"],
        max_consecutive_losses=headline["max_consecutive_losses"],
        expectancy=headline["expectancy"],
        sell_direction=sell_direction,
        by_market_regime=_bucket(buys, "market_regime", primary_horizon),
        by_time_phase=_bucket(buys, "time_phase", primary_horizon),
        reason_code=reason,
    )


def evaluate_agent_council_performance(
    trades: Sequence[BacktestTrade], *, primary_horizon: str,
    horizon_labels: Sequence[str], quantity: int, final_action_counts: dict[str, int],
    confidences: Sequence[float], qualities: Sequence[float],
    selected_freq: dict[str, int],
) -> CouncilBacktestResult:
    perf = evaluate_strategy_vote_performance(
        trades, strategy=AGENT_COUNCIL, primary_horizon=primary_horizon,
        horizon_labels=horizon_labels, quantity=quantity,
    )
    return CouncilBacktestResult(
        performance=perf,
        final_action_counts=dict(final_action_counts),
        avg_confidence=round(sum(confidences) / len(confidences), 4) if confidences else None,
        avg_quality_score=round(sum(qualities) / len(qualities), 2) if qualities else None,
        selected_strategies_freq=dict(selected_freq),
    )


def _compare_council_vs_single(
    strategies: dict[str, StrategyBacktestResult],
    council: CouncilBacktestResult, primary: str,
) -> dict[str, Any]:
    """Agent Council 이 단일 전략보다 나은지 (expectancy 우선) 비교."""
    def _exp(v):
        return v if v is not None else float("-inf")

    ranked = sorted(
        ((s, r.expectancy) for s, r in strategies.items()),
        key=lambda kv: _exp(kv[1]), reverse=True,
    )
    best_strategy, best_exp = ranked[0] if ranked else (None, None)
    council_exp = council.performance.expectancy
    council_better = (
        council_exp is not None and best_exp is not None
        and council_exp >= best_exp
    )
    delta = (council_exp - best_exp) if (council_exp is not None and best_exp is not None) else None
    return {
        "metric":                "expectancy",
        "primary_horizon":       primary,
        "council_expectancy":    council_exp,
        "council_win_rate":      council.performance.win_rate,
        "best_single_strategy":  best_strategy,
        "best_single_expectancy": best_exp,
        "council_better_than_best_single": bool(council_better),
        "expectancy_delta":      round(delta, 4) if delta is not None else None,
        "ranking": [{"strategy": s, "expectancy": e} for s, e in ranked],
        "note": ("Agent Council 이 단일 전략보다 우월한지의 *백테스트 비교*이며, "
                 "실전 전환 근거가 아닙니다."),
    }


def _empty_report(now_iso, symbol_count, bar_count, inp, horizon_labels, primary,
                  reason, *, insufficient) -> BacktestReport:
    empty_strats = {
        s: StrategyBacktestResult(
            strategy=s, signal_counts={"BUY": 0, "SELL": 0, "HOLD": 0},
            by_horizon={h: _metric_suite([], h, inp.quantity) for h in horizon_labels},
            primary_horizon=primary, win_rate=None, average_return=None,
            average_win=None, average_loss=None, payoff_ratio=None, profit_factor=None,
            max_drawdown=0, max_consecutive_losses=0, expectancy=None,
            sell_direction={"interpretation": "보유 청산 신호 / 하락 방향 판단 평가 (신규 숏 아님)",
                            "count": 0, "down_hit_count": 0, "down_hit_rate": None,
                            "average_forward_return": None},
            by_market_regime={}, by_time_phase={}, reason_code=reason,
        ) for s in SINGLE_STRATEGIES
    }
    return BacktestReport(
        generated_at=now_iso, symbol_count=symbol_count, bar_count=bar_count,
        risk_profile=inp.risk_profile, horizon_labels=horizon_labels,
        primary_horizon=primary, strategies=empty_strats, council=None,
        comparison={"council_better_than_best_single": False, "reason_code": reason},
        reason_code=reason, insufficient_data=insufficient,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 요약 / 리포트 렌더
# ─────────────────────────────────────────────────────────────────────────────


def summarize_backtest_report(report: BacktestReport) -> dict[str, Any]:
    """리포트 → JSON-safe dict (스크립트 / API 출력용)."""
    return report.to_dict()


def _fmt(v: Any, *, pct: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v*100:.2f}%" if pct else f"{v:.4f}"
    return str(v)


def render_markdown_report(report: BacktestReport) -> str:
    """리포트 → Markdown 문자열. '수익 보장' / '실전 전환 승인' 문구 0건."""
    lines: list[str] = []
    lines.append("# 4전략 + Agent Council 백테스트 리포트 (#46 / 6-01)")
    lines.append("")
    lines.append(f"> {report.disclaimer}")
    lines.append("")
    lines.append(f"- 생성: {report.generated_at}")
    lines.append(f"- 종목 수: {report.symbol_count} · bar 수: {report.bar_count}")
    lines.append(f"- risk_profile: {report.risk_profile} · primary horizon: {report.primary_horizon}")
    lines.append(f"- reason_code: {report.reason_code}")
    lines.append("")

    if report.insufficient_data:
        lines.append("## ⚠️ 데이터 부족")
        lines.append("백테스트에 필요한 최소 bar 수가 부족합니다 "
                     f"(`{report.reason_code}`).")
        return "\n".join(lines) + "\n"

    lines.append("## 전략별 성과 (primary horizon, BUY 신호 풀)")
    lines.append("")
    lines.append("| 전략 | BUY | SELL | HOLD | 승률 | 평균수익 | 손익비 | PF | MDD | 연속손실 | expectancy |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in SINGLE_STRATEGIES:
        r = report.strategies[s]
        c = r.signal_counts
        lines.append(
            f"| {s} | {c.get('BUY',0)} | {c.get('SELL',0)} | {c.get('HOLD',0)} | "
            f"{_fmt(r.win_rate, pct=True)} | {_fmt(r.average_return, pct=True)} | "
            f"{_fmt(r.payoff_ratio)} | {_fmt(r.profit_factor)} | {r.max_drawdown} | "
            f"{r.max_consecutive_losses} | {_fmt(r.expectancy)} |"
        )
    if report.council:
        cp = report.council.performance
        cc = cp.signal_counts
        lines.append(
            f"| **{AGENT_COUNCIL}** | {cc.get('BUY',0)} | {cc.get('SELL',0)} | "
            f"{cc.get('HOLD',0)} | {_fmt(cp.win_rate, pct=True)} | "
            f"{_fmt(cp.average_return, pct=True)} | {_fmt(cp.payoff_ratio)} | "
            f"{_fmt(cp.profit_factor)} | {cp.max_drawdown} | "
            f"{cp.max_consecutive_losses} | {_fmt(cp.expectancy)} |"
        )
    lines.append("")

    lines.append("## SELL 신호 (보유 청산 / 하락 방향 판단 평가 — 신규 숏 아님)")
    lines.append("")
    lines.append("| 전략 | SELL 수 | 하락 적중 | 하락 적중률 | 평균 forward return |")
    lines.append("|---|---|---|---|---|")
    for s in SINGLE_STRATEGIES:
        sd = report.strategies[s].sell_direction
        lines.append(f"| {s} | {sd['count']} | {sd['down_hit_count']} | "
                     f"{_fmt(sd['down_hit_rate'], pct=True)} | {_fmt(sd['average_forward_return'], pct=True)} |")
    lines.append("")

    if report.council:
        comp = report.comparison
        lines.append("## Agent Council vs 단일 전략 비교")
        lines.append("")
        lines.append(f"- 비교 지표: {comp.get('metric')} (horizon={comp.get('primary_horizon')})")
        lines.append(f"- Council expectancy: {_fmt(comp.get('council_expectancy'))}")
        lines.append(f"- 최우수 단일 전략: {comp.get('best_single_strategy')} "
                     f"(expectancy={_fmt(comp.get('best_single_expectancy'))})")
        better = comp.get("council_better_than_best_single")
        lines.append(f"- **Council 이 최우수 단일 전략보다 우월?** {'예' if better else '아니오'} "
                     f"(Δ={_fmt(comp.get('expectancy_delta'))})")
        lines.append(f"- final_action 분포: {report.council.final_action_counts}")
        lines.append(f"- 평균 confidence: {_fmt(report.council.avg_confidence)} · "
                     f"평균 quality_score: {_fmt(report.council.avg_quality_score)}")
        lines.append(f"- selected_strategies 빈도: {report.council.selected_strategies_freq}")
        lines.append("")
        lines.append(f"> {comp.get('note', '')}")
    lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "SINGLE_STRATEGIES", "AGENT_COUNCIL",
    "BACKTEST_OK", "BACKTEST_INSUFFICIENT_DATA", "BACKTEST_NO_SIGNALS",
    "OHLCVBar", "BacktestInput", "BacktestTrade",
    "StrategyBacktestResult", "CouncilBacktestResult", "BacktestReport",
    "load_ohlcv_from_records", "load_ohlcv_from_csv",
    "run_strategy_council_backtest", "evaluate_strategy_vote_performance",
    "evaluate_agent_council_performance", "summarize_backtest_report",
    "render_markdown_report",
]
