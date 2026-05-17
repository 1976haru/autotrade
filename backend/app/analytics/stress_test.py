"""3-05 — Stress test 시나리오 모듈.

10 시나리오 (CRASH / SURGE / SIDEWAYS / SLIPPAGE_SPIKE / DATA_GAP /
EXECUTION_REJECT / STALE_PRICE / DUPLICATE_SIGNAL / LOW_LIQUIDITY /
CORRELATED_DRAWDOWN) 별로 전략 + 데이터를 *변형* 한 뒤 백테스트를 실행해
전략의 스트레스 저항성을 평가한다.

본 모듈의 설계 원칙:
- **결정론적 변형**: 동일 입력 → 동일 결과 (재현 가능, CI 안전).
- **데이터 변형 vs 비용 가중 vs 행동 카운터** 3 범주로 분리.
- **broker / OrderExecutor / route_order / KIS 주문 API 호출 0건** — 정적
  grep 가드 + 테스트 lock. 본 모듈은 *분석 read-only*.
- 본 verdict 는 *분석 라벨* — paper 운용 / 실거래 활성화 / 자동 promotion
  변경 의미 0건. 3-07 paper 후보 자격 *검토 자료* 일 뿐.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- yfinance / 외부 HTTP / AI SDK import 0건.
- 실거래 / Place Order 호출 0건.
- 안전 flag (ENABLE_LIVE_TRADING / KIS_IS_PAPER) mutate 0건.
- ``StressResult.is_order_signal=False`` / ``auto_apply_allowed=False`` /
  ``is_live_authorization=False`` invariant.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from typing import Any, Sequence

from app.backtest.engine import BacktestEngine
from app.backtest.types import BacktestConfig, Bar


# ─────────────────────────────────────────────────────────────────────────────
# 1. Scenario / verdict enum
# ─────────────────────────────────────────────────────────────────────────────


class StressScenario(StrEnum):
    """10 스트레스 시나리오."""

    CRASH                = "CRASH"
    SURGE                = "SURGE"
    SIDEWAYS             = "SIDEWAYS"
    SLIPPAGE_SPIKE       = "SLIPPAGE_SPIKE"
    DATA_GAP             = "DATA_GAP"
    EXECUTION_REJECT     = "EXECUTION_REJECT"
    STALE_PRICE          = "STALE_PRICE"
    DUPLICATE_SIGNAL     = "DUPLICATE_SIGNAL"
    LOW_LIQUIDITY        = "LOW_LIQUIDITY"
    CORRELATED_DRAWDOWN  = "CORRELATED_DRAWDOWN"


class StressVerdict(StrEnum):
    """4단계 verdict."""

    PASS                = "PASS"
    WARN                = "WARN"
    FAIL                = "FAIL"
    INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Config / thresholds
# ─────────────────────────────────────────────────────────────────────────────


# Default 임계값.
DEFAULT_MIN_TRADE_COUNT     = 5      # 스트레스 변형 후 trade 가 더 적어질 수
                                     # 있으므로 baseline 보다 완화.
DEFAULT_PASS_MAX_DD         = 0.20   # PASS 한도: MDD ≤ 20% (스트레스라 완화).
DEFAULT_WARN_MAX_DD         = 0.15   # WARN 경계: MDD ≤ 15%.
DEFAULT_MIN_EXPECTANCY      = 0.0    # expectancy > 0.

# 시나리오별 변형 강도 (deterministic).
DEFAULT_CRASH_PCT           = 0.08   # 8% 하락 갭.
DEFAULT_SURGE_PCT           = 0.08   # 8% 상승 갭.
DEFAULT_SIDEWAYS_BAND       = 0.005  # ±0.5% 압축.
DEFAULT_SLIPPAGE_SPIKE_BPS  = 30     # default 5 → 30 bps (6배).
DEFAULT_DATA_GAP_RATIO      = 0.14   # 매 7번째 bar 제거 (~14%).
DEFAULT_REJECT_RATIO        = 0.20   # 거래 5개당 1개 reject.
DEFAULT_STALE_RATIO         = 0.10   # 10% bars stale.
DEFAULT_LOW_LIQUIDITY_RATIO = 0.10   # volume * 0.10.


@dataclass(frozen=True)
class StressTestConfig:
    """스트레스 verdict + 변형 강도 설정."""

    min_trade_count:        int   = DEFAULT_MIN_TRADE_COUNT
    pass_max_drawdown:      float = DEFAULT_PASS_MAX_DD
    warn_max_drawdown:      float = DEFAULT_WARN_MAX_DD
    min_expectancy:         float = DEFAULT_MIN_EXPECTANCY

    # 변형 강도.
    crash_pct:              float = DEFAULT_CRASH_PCT
    surge_pct:              float = DEFAULT_SURGE_PCT
    sideways_band:          float = DEFAULT_SIDEWAYS_BAND
    slippage_spike_bps:     int   = DEFAULT_SLIPPAGE_SPIKE_BPS
    data_gap_ratio:         float = DEFAULT_DATA_GAP_RATIO
    reject_ratio:           float = DEFAULT_REJECT_RATIO
    stale_ratio:            float = DEFAULT_STALE_RATIO
    low_liquidity_ratio:    float = DEFAULT_LOW_LIQUIDITY_RATIO

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_trade_count":      self.min_trade_count,
            "pass_max_drawdown":    self.pass_max_drawdown,
            "warn_max_drawdown":    self.warn_max_drawdown,
            "min_expectancy":       self.min_expectancy,
            "crash_pct":            self.crash_pct,
            "surge_pct":            self.surge_pct,
            "sideways_band":        self.sideways_band,
            "slippage_spike_bps":   self.slippage_spike_bps,
            "data_gap_ratio":       self.data_gap_ratio,
            "reject_ratio":         self.reject_ratio,
            "stale_ratio":          self.stale_ratio,
            "low_liquidity_ratio":  self.low_liquidity_ratio,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. 데이터 변형 — deterministic
# ─────────────────────────────────────────────────────────────────────────────


def _scale_bar(bar: Bar, factor: float) -> Bar:
    """OHLC 에 동일 비율 적용 (volume / timestamp 그대로)."""
    return Bar(
        symbol=bar.symbol,
        timestamp=bar.timestamp,
        open=max(1, int(round(bar.open * factor))),
        high=max(1, int(round(bar.high * factor))),
        low=max(1, int(round(bar.low * factor))),
        close=max(1, int(round(bar.close * factor))),
        volume=bar.volume,
    )


def apply_crash(bars: Sequence[Bar], pct: float) -> list[Bar]:
    """CRASH — fold 중간 지점부터 `pct` 하락 갭 + 0.1% 누적 하락 트렌드.

    deterministic: 동일 (bars, pct) → 동일 결과.
    """
    if not bars:
        return []
    n = len(bars)
    mid = n // 2
    out: list[Bar] = []
    for i, b in enumerate(bars):
        if i < mid:
            out.append(b)
            continue
        # 갭 + 추가 누적.
        steps = i - mid
        factor = (1.0 - pct) * ((1.0 - 0.001) ** steps)
        out.append(_scale_bar(b, factor))
    return out


def apply_surge(bars: Sequence[Bar], pct: float) -> list[Bar]:
    """SURGE — 중간 지점부터 `pct` 갭업 + 0.1% 누적 상승."""
    if not bars:
        return []
    n = len(bars)
    mid = n // 2
    out: list[Bar] = []
    for i, b in enumerate(bars):
        if i < mid:
            out.append(b)
            continue
        steps = i - mid
        factor = (1.0 + pct) * ((1.0 + 0.001) ** steps)
        out.append(_scale_bar(b, factor))
    return out


def apply_sideways(bars: Sequence[Bar], band: float) -> list[Bar]:
    """SIDEWAYS — close 를 *전체 평균* 의 ±band 로 압축.

    high / low 도 평균에 가깝게 조정 — 변동성 ↓.
    """
    if not bars:
        return []
    avg = sum(b.close for b in bars) / len(bars)
    out: list[Bar] = []
    for i, b in enumerate(bars):
        # 진동 ±band sin 형태.
        offset = avg * band * math.sin(i * 0.5)
        close = int(round(avg + offset))
        high  = int(round(avg + abs(offset) * 1.2))
        low   = int(round(avg - abs(offset) * 1.2))
        open_ = int(round(avg + offset * 0.8))
        out.append(Bar(
            symbol=b.symbol, timestamp=b.timestamp,
            open=max(1, open_), high=max(1, high),
            low=max(1, low),    close=max(1, close),
            volume=b.volume,
        ))
    return out


def apply_data_gap(bars: Sequence[Bar], ratio: float) -> list[Bar]:
    """DATA_GAP — 약 `ratio` 비율로 bars 제거 (매 N 번째 bar drop).

    `ratio=0.14` ≈ 매 7번째 제거 (n=7).
    """
    if not bars or ratio <= 0:
        return list(bars)
    n = max(2, int(round(1.0 / ratio)))
    return [b for i, b in enumerate(bars) if (i + 1) % n != 0]


def apply_low_liquidity(bars: Sequence[Bar], ratio: float) -> list[Bar]:
    """LOW_LIQUIDITY — volume * ratio."""
    if not bars:
        return []
    factor = max(0.0, ratio)
    return [
        Bar(
            symbol=b.symbol, timestamp=b.timestamp,
            open=b.open, high=b.high, low=b.low, close=b.close,
            volume=max(1, int(b.volume * factor)),
        )
        for b in bars
    ]


def count_stale_bars(bars: Sequence[Bar], ratio: float) -> int:
    """STALE_PRICE — `ratio` 비율의 bar 가 stale 로 카운트 (deterministic).

    실제 timestamp 가공 대신 *counter* 만 반환 — 본 카운터는 verdict 에 영향.
    """
    if not bars or ratio <= 0:
        return 0
    return int(len(bars) * ratio)


def count_duplicate_signals(bars: Sequence[Bar]) -> int:
    """DUPLICATE_SIGNAL — 연속 같은 방향 close-up 카운트 (proxy).

    실제 신호 중복은 strategy 단위에서 발생하지만 본 모듈은 strategy 호출 후
    *결과 metric* 만 분석. 본 함수는 bars 흐름에서 "연속 N 봉 동방향" 발생
    횟수를 proxy 로 카운트 (단순 진단용).
    """
    if not bars:
        return 0
    count = 0
    direction = 0
    streak = 0
    for i in range(1, len(bars)):
        d = 1 if bars[i].close > bars[i - 1].close else (-1 if bars[i].close < bars[i - 1].close else 0)
        if d != 0 and d == direction:
            streak += 1
            if streak >= 3:
                count += 1
        else:
            streak = 0
            direction = d
    return count


def correlated_drawdown_proxy(bars: Sequence[Bar]) -> float:
    """CORRELATED_DRAWDOWN — 단일 symbol 의 max drawdown 을 *proxy* 로 반환.

    실제 상관관계는 다종목 입력 필요 — 본 함수는 단일 symbol drawdown 을
    informational proxy 로 carry.
    """
    if not bars:
        return 0.0
    peak = bars[0].close
    max_dd = 0.0
    for b in bars:
        if b.close > peak:
            peak = b.close
        if peak > 0:
            dd = (peak - b.close) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


# ─────────────────────────────────────────────────────────────────────────────
# 4. Per-trade rejection (EXECUTION_REJECT) — post-process
# ─────────────────────────────────────────────────────────────────────────────


def simulate_rejected_trades(trades: list[Any], ratio: float) -> tuple[list[Any], int]:
    """매 N 번째 trade 를 reject 처리 — 남은 trades + rejected_count 반환.

    deterministic: 동일 ratio → 동일 결과. 본 함수는 list 만 다루며 broker /
    OrderExecutor 와 무관.
    """
    if not trades or ratio <= 0:
        return list(trades), 0
    n = max(2, int(round(1.0 / ratio)))
    rejected = 0
    kept: list[Any] = []
    for i, t in enumerate(trades):
        if (i + 1) % n == 0:
            rejected += 1
            continue
        kept.append(t)
    return kept, rejected


# ─────────────────────────────────────────────────────────────────────────────
# 5. Metrics 계산
# ─────────────────────────────────────────────────────────────────────────────


def _safe_float(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _extract_pnl(t: Any) -> float:
    pnl = getattr(t, "pnl", None)
    if pnl is None and isinstance(t, dict):
        pnl = t.get("pnl")
    return _safe_float(pnl)


def _compute_metrics(*, trades: list[Any], total_return: float, max_drawdown: float,
                     slippage_paid: float) -> dict[str, Any]:
    pnls = [_extract_pnl(t) for t in trades]
    n = len(pnls)
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total_win  = sum(wins)
    total_loss = abs(sum(losses))
    pf_raw = (total_win / total_loss) if total_loss > 0 else (None if total_win == 0 else float("inf"))
    pf_json: Any = None if (pf_raw is None or pf_raw == float("inf")) else _safe_float(pf_raw)

    avg_pnl  = (sum(pnls) / n) if n > 0 else 0.0
    win_rate = (len(wins) / n) if n > 0 else 0.0

    longest = 0
    streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0

    return {
        "trade_count":   int(n),
        "total_return":  _safe_float(total_return),
        "expectancy":    _safe_float(avg_pnl),
        "profit_factor": pf_json,
        "max_drawdown":  _safe_float(max_drawdown),
        "win_rate":      _safe_float(win_rate),
        "loss_streak":   int(longest),
        "slippage_cost": _safe_float(slippage_paid),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. Verdict + score
# ─────────────────────────────────────────────────────────────────────────────


def _classify(
    metrics: dict[str, Any], *,
    rejected: int, stale: int, dup_signals: int,
    config: StressTestConfig,
) -> tuple[StressVerdict, float, list[str]]:
    """4단계 verdict + 0-100 점수 + 사유."""
    reasons: list[str] = []
    trade_count   = int(metrics.get("trade_count", 0) or 0)
    expectancy    = _safe_float(metrics.get("expectancy"))
    max_dd        = _safe_float(metrics.get("max_drawdown"))

    if trade_count < config.min_trade_count:
        reasons.append(f"trade_count={trade_count} < min={config.min_trade_count}")
        return StressVerdict.INSUFFICIENT_DATA, 0.0, reasons

    # Stress score (0-100) — expectancy / drawdown / violations 합산.
    score = 100.0
    if expectancy <= 0:
        score -= 40
        reasons.append(f"expectancy={expectancy:.2f} <= 0")
    if max_dd > config.pass_max_drawdown:
        score -= 30
        reasons.append(f"max_drawdown={max_dd:.4f} > pass_limit={config.pass_max_drawdown}")
    elif max_dd > config.warn_max_drawdown:
        score -= 10
        reasons.append(f"max_drawdown={max_dd:.4f} > warn_limit={config.warn_max_drawdown}")
    if rejected > 0:
        score -= min(20, rejected * 2)
        reasons.append(f"rejected_order_count={rejected}")
    if stale > 0:
        score -= min(15, stale * 1.0)
        reasons.append(f"stale_data_violation_count={stale}")
    if dup_signals > 0:
        # 정보성 — 점수 영향 작게.
        score -= min(5, dup_signals * 0.5)
        reasons.append(f"duplicate_signal_count={dup_signals}")
    score = max(0.0, min(100.0, score))

    # Verdict.
    critical_fail = (
        expectancy <= config.min_expectancy
        or max_dd > config.pass_max_drawdown
    )
    if critical_fail:
        return StressVerdict.FAIL, score, reasons
    moderate_warn = (
        max_dd > config.warn_max_drawdown
        or rejected > 0
        or stale > 0
    )
    if moderate_warn:
        return StressVerdict.WARN, score, reasons
    reasons.append("all_filters_passed")
    return StressVerdict.PASS, score, reasons


# ─────────────────────────────────────────────────────────────────────────────
# 7. StressResult / evaluate_stress
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StressResult:
    """단일 (scenario, strategy, symbol) 평가 결과 — 16 필수 필드."""

    scenario_name:              str
    strategy:                   str
    symbol:                     str
    total_return:               float
    expectancy:                 float
    profit_factor:              Any           # None | float
    max_drawdown:               float
    win_rate:                   float
    trade_count:                int
    loss_streak:                int
    rejected_order_count:       int
    stale_data_violation_count: int
    duplicate_signal_count:     int
    slippage_cost:              float
    stress_score:               float          # 0-100
    stress_verdict:             StressVerdict
    reasons:                    list[str]     = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_name":               self.scenario_name,
            "strategy":                    self.strategy,
            "symbol":                      self.symbol,
            "total_return":                self.total_return,
            "expectancy":                  self.expectancy,
            "profit_factor":               self.profit_factor,
            "max_drawdown":                self.max_drawdown,
            "win_rate":                    self.win_rate,
            "trade_count":                 self.trade_count,
            "loss_streak":                 self.loss_streak,
            "rejected_order_count":        self.rejected_order_count,
            "stale_data_violation_count":  self.stale_data_violation_count,
            "duplicate_signal_count":      self.duplicate_signal_count,
            "slippage_cost":               self.slippage_cost,
            "stress_score":                self.stress_score,
            "stress_verdict":              self.stress_verdict.value,
            "reasons":                     list(self.reasons),
            # 분석 라벨 — 자동 주문 / 자동 적용 / 실거래 허가 의미 0건.
            "is_order_signal":             False,
            "auto_apply_allowed":          False,
            "is_live_authorization":       False,
        }


def _build_strategy(strategy_name: str, params: dict[str, Any]):
    from app.strategies.concrete import build_strategy
    return build_strategy(strategy_name, params=dict(params))


def _apply_scenario_to_bars(
    bars: Sequence[Bar], scenario: StressScenario, config: StressTestConfig,
) -> list[Bar]:
    """데이터 변형 시나리오 적용."""
    if scenario == StressScenario.CRASH:
        return apply_crash(bars, config.crash_pct)
    if scenario == StressScenario.SURGE:
        return apply_surge(bars, config.surge_pct)
    if scenario == StressScenario.SIDEWAYS:
        return apply_sideways(bars, config.sideways_band)
    if scenario == StressScenario.DATA_GAP:
        return apply_data_gap(bars, config.data_gap_ratio)
    if scenario == StressScenario.LOW_LIQUIDITY:
        return apply_low_liquidity(bars, config.low_liquidity_ratio)
    # 그 외 시나리오는 bars 그대로 사용 (cost / counter 측에서 처리).
    return list(bars)


def _apply_scenario_to_btconfig(
    bt_config: BacktestConfig, scenario: StressScenario, config: StressTestConfig,
) -> BacktestConfig:
    """비용 가중 시나리오 — BacktestConfig 만 조정."""
    if scenario == StressScenario.SLIPPAGE_SPIKE:
        return replace(bt_config, slippage_bps=int(config.slippage_spike_bps))
    return bt_config


def evaluate_stress(
    *,
    bars:           Sequence[Bar],
    strategy_name:  str,
    symbol:         str,
    scenario:       StressScenario,
    params:         dict[str, Any] | None = None,
    config:         StressTestConfig | None = None,
    bt_config:      BacktestConfig | None = None,
    initial_cash:   int = 10_000_000,
    quantity:       int = 10,
) -> StressResult:
    """단일 (scenario, strategy, symbol) 스트레스 평가.

    데이터 변형 → 백테스트 실행 → 시나리오별 추가 counter / cost 처리 →
    verdict 분류.
    """
    cfg = config or StressTestConfig()
    pparams = dict(params or {})
    base_bt = bt_config or BacktestConfig(
        execution_model="next_open",
        execution_delay_bars=1,
        slippage_bps=5, commission_bps=15, tax_bps=23,
    )

    # 1) 데이터 변형.
    stressed_bars = _apply_scenario_to_bars(list(bars), scenario, cfg)

    # 2) BacktestConfig 조정 (SLIPPAGE_SPIKE 한정).
    stressed_bt = _apply_scenario_to_btconfig(base_bt, scenario, cfg)

    # 3) 백테스트 실행.
    try:
        strategy = _build_strategy(strategy_name, pparams)
    except Exception:  # noqa: BLE001
        return _fail_result(
            scenario=scenario, strategy_name=strategy_name, symbol=symbol,
            reason="strategy_build_failed",
        )

    try:
        engine = BacktestEngine(initial_cash=initial_cash, quantity=quantity)
        result = engine.run(stressed_bars, strategy, config=stressed_bt)
    except Exception:  # noqa: BLE001
        return _fail_result(
            scenario=scenario, strategy_name=strategy_name, symbol=symbol,
            reason="engine_run_failed",
        )

    trades = list(getattr(result, "trades", []) or [])

    # 4) 시나리오별 post-process counters.
    rejected_count = 0
    stale_count = 0
    dup_count = 0

    if scenario == StressScenario.EXECUTION_REJECT:
        trades, rejected_count = simulate_rejected_trades(trades, cfg.reject_ratio)
    elif scenario == StressScenario.STALE_PRICE:
        stale_count = count_stale_bars(stressed_bars, cfg.stale_ratio)
    elif scenario == StressScenario.DUPLICATE_SIGNAL:
        dup_count = count_duplicate_signals(stressed_bars)
    # CORRELATED_DRAWDOWN: proxy 만 carry (verdict 영향 X — 별도 보고).

    # 5) Metrics — total_return 은 reject 가 영향을 미치므로 단순 재집계.
    # 일관성 위해 trade pnl 합 기준으로 total_return proxy 재계산.
    if scenario == StressScenario.EXECUTION_REJECT and trades:
        # reject 후 단순 추정: 남은 trade 의 sum(pnl) / initial_cash.
        adj_pnl = sum(_extract_pnl(t) for t in trades)
        adj_return = adj_pnl / max(1, initial_cash)
    else:
        adj_return = _safe_float(getattr(result, "total_return", 0.0))

    metrics = _compute_metrics(
        trades=trades,
        total_return=adj_return,
        max_drawdown=_safe_float(getattr(result, "max_drawdown", 0.0)),
        slippage_paid=_safe_float(getattr(result, "slippage_paid", 0.0)),
    )

    verdict, score, reasons = _classify(
        metrics, rejected=rejected_count, stale=stale_count,
        dup_signals=dup_count, config=cfg,
    )

    return StressResult(
        scenario_name=scenario.value,
        strategy=strategy_name,
        symbol=symbol,
        total_return=metrics["total_return"],
        expectancy=metrics["expectancy"],
        profit_factor=metrics["profit_factor"],
        max_drawdown=metrics["max_drawdown"],
        win_rate=metrics["win_rate"],
        trade_count=metrics["trade_count"],
        loss_streak=metrics["loss_streak"],
        rejected_order_count=rejected_count,
        stale_data_violation_count=stale_count,
        duplicate_signal_count=dup_count,
        slippage_cost=metrics["slippage_cost"],
        stress_score=score,
        stress_verdict=verdict,
        reasons=reasons,
    )


def _fail_result(
    *, scenario: StressScenario, strategy_name: str, symbol: str, reason: str,
) -> StressResult:
    """엔진 실패 / strategy build 실패 시 INSUFFICIENT_DATA 로 carry."""
    return StressResult(
        scenario_name=scenario.value,
        strategy=strategy_name,
        symbol=symbol,
        total_return=0.0, expectancy=0.0, profit_factor=None,
        max_drawdown=0.0, win_rate=0.0,
        trade_count=0, loss_streak=0,
        rejected_order_count=0, stale_data_violation_count=0,
        duplicate_signal_count=0, slippage_cost=0.0,
        stress_score=0.0,
        stress_verdict=StressVerdict.INSUFFICIENT_DATA,
        reasons=[reason],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Walk-forward adapter (3-04 결과 입력)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StressCandidateInput:
    """3-04 walk_forward_summary.json 의 단일 result 추출 형태."""

    strategy:  str
    symbol:    str
    params:    dict[str, Any]
    verdict:   str               # 3-04 verdict (HEALTHY / OVERFIT_RISK / ...)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol":   self.symbol,
            "params":   dict(self.params),
            "verdict":  self.verdict,
        }


def read_candidates_from_walk_forward(payload: dict[str, Any]) -> list[StressCandidateInput]:
    """3-04 walk_forward_summary.json payload → candidate 리스트.

    HEALTHY verdict 만 추출 — 권장 진입 조건. malformed 입력에도 raise X.
    """
    out: list[StressCandidateInput] = []
    if not isinstance(payload, dict):
        return out
    results = payload.get("results")
    if not isinstance(results, list):
        return out
    for r in results:
        if not isinstance(r, dict):
            continue
        strategy = r.get("strategy")
        symbol   = r.get("symbol")
        params   = r.get("params")
        verdict  = r.get("verdict")
        if not (isinstance(strategy, str) and isinstance(symbol, str)
                and isinstance(params, dict) and isinstance(verdict, str)):
            continue
        if verdict != "HEALTHY":
            continue
        out.append(StressCandidateInput(
            strategy=strategy, symbol=symbol, params=dict(params), verdict=verdict,
        ))
    return out
