"""3-06 — 성과 지표 표준화 단일 진실 모듈.

백테스트 (3-02), parameter optimization (3-03), walk-forward (3-04), stress
test (3-05), 그리고 향후 paper 운용 단계까지 *모두 동일한 14개 지표 키* 를
사용한다. metric drift 위험을 줄이기 위해 본 모듈만 유지보수하면 된다.

14 필수 지표 (`PERFORMANCE_METRIC_KEYS`):
- total_return                — raw 누적 수익률 (소수, 0.123 = 12.3%)
- annualized_return           — 거래기간 환산 연환산
- win_rate                    — 승률 (win count / trade count)
- trade_count                 — 거래 횟수 (정수)
- profit_factor               — 총이익 / |총손실| (None | float, JSON 안전)
- expectancy                  — 평균 거래 PnL (KRW per trade)
- max_drawdown                — 최대 낙폭 (0~1 절대값)
- avg_trade_pnl               — expectancy 와 동일 (별칭 — 호환성)
- avg_win                     — 평균 승리 PnL
- avg_loss                    — 평균 손실 PnL (절대값)
- loss_streak                 — 최대 연속 손실 횟수
- risk_adjusted_score         — (expectancy / initial_cash) / max_drawdown
- sharpe_like_score           — pseudo-Sharpe (mean(pnl) / std(pnl), √(n) scale)
- fee_adjusted_return         — total_return - (fees + taxes) / initial_cash
- slippage_adjusted_return    — fee_adjusted_return - slippage / initial_cash

사용자 spec 의 14번째 지표 "sharpe_like_score 또는 risk_adjusted_score" — *둘 다*
포함 (운영자가 선택해서 사용). 즉 출력 dict 는 15 키 (14 spec + 1 호환 alias).

처리 정책:
- **빈 거래** (trade_count=0) → 모든 키 안전 기본값 (0 / None) — 예외 raise 0건.
- **손실 없음** (total_loss=0) — profit_factor:
    - total_win=0 → 0.0 (의미: 거래 0건 또는 0 PnL)
    - total_win>0 → None (JSON 호환, *무한* 표현 차단). 사람이 읽는 비율
      대신 별도 카운터로 판단.
- **max_drawdown** — equity curve 인자 우선. 없으면 누적 PnL 기준 estimate.
- **expectancy** — sum(pnls) / trade_count (산술 평균).
- **loss_streak** — 음수 PnL 연속 카운트의 최대값.
- **수수료 / 슬리피지** — total_return 의 *raw* 와 *fee_adjusted* / *slippage
  _adjusted* 를 모두 carry — 운영자가 비용 영향 비교 가능.
- **JSON 직렬화** — 모든 값은 int / float / None — NaN / inf 발견 시 None / 0.0
  으로 클램프.

본 모듈은 broker / OrderExecutor / route_order import 0건 — *순수 함수 only*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence


# ─────────────────────────────────────────────────────────────────────────────
# 1. 표준 키 — 단일 진실
# ─────────────────────────────────────────────────────────────────────────────


# 사용자 spec 의 14 키. ``avg_trade_pnl`` 는 expectancy 와 동일 (호환성 별칭).
PERFORMANCE_METRIC_KEYS: tuple[str, ...] = (
    "total_return",
    "annualized_return",
    "win_rate",
    "trade_count",
    "profit_factor",
    "expectancy",
    "max_drawdown",
    "avg_trade_pnl",
    "avg_win",
    "avg_loss",
    "loss_streak",
    "risk_adjusted_score",
    "sharpe_like_score",
    "fee_adjusted_return",
    "slippage_adjusted_return",
)


# 빈 거래 처리 — INSUFFICIENT_DATA 판정용 임계값 (운영자 override 가능).
DEFAULT_MIN_TRADE_COUNT_FOR_FULL = 10   # 미만이면 INSUFFICIENT_DATA 권장.

# Annualized 계산용 거래일 수 (한국 주식 가정 ≈ 252).
TRADING_DAYS_PER_YEAR = 252


# ─────────────────────────────────────────────────────────────────────────────
# 2. 안전 변환 helper
# ─────────────────────────────────────────────────────────────────────────────


def safe_float(v: Any, default: float = 0.0) -> float:
    """None / NaN / inf 모두 default 로 변환 — JSON 호환 보장."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def extract_pnl(trade: Any) -> float:
    """trade.pnl 또는 trade["pnl"] 안전 추출. None 이면 0.0."""
    if trade is None:
        return 0.0
    pnl = getattr(trade, "pnl", None)
    if pnl is None and isinstance(trade, dict):
        pnl = trade.get("pnl")
    if pnl is None:
        # net_pnl 호환 (Trade.net_pnl property 는 pnl 과 동일).
        pnl = getattr(trade, "net_pnl", None)
    return safe_float(pnl)


# ─────────────────────────────────────────────────────────────────────────────
# 3. 보조 계산 helper
# ─────────────────────────────────────────────────────────────────────────────


def compute_max_drawdown(pnls: Sequence[float]) -> float:
    """누적 PnL 곡선 기준 최대 낙폭 (0~1 절대값).

    equity curve 가 직접 제공되면 ``compute_max_drawdown_from_equity`` 사용.
    여기서는 *상대* drawdown — initial_cash 미반영. 외부 caller 가 별도
    스케일링 필요.
    """
    if not pnls:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += safe_float(p)
        if cum > peak:
            peak = cum
        # peak 가 음수이면 분모 0 가까이 → 보수적으로 절대값 사용.
        # 정의: drawdown 은 peak 부터의 하락 비율.
        if peak > 0:
            dd = (peak - cum) / peak
            if dd > max_dd:
                max_dd = dd
    return max(0.0, min(1.0, max_dd))


def compute_max_drawdown_from_equity(equity_curve: Sequence[float]) -> float:
    """equity curve (account balance series) 기준 최대 낙폭."""
    if not equity_curve:
        return 0.0
    peak = float(equity_curve[0])
    max_dd = 0.0
    for v in equity_curve:
        f = safe_float(v)
        if f > peak:
            peak = f
        if peak > 0:
            dd = (peak - f) / peak
            if dd > max_dd:
                max_dd = dd
    return max(0.0, min(1.0, max_dd))


def compute_loss_streak(pnls: Sequence[float]) -> int:
    """최대 연속 손실 횟수 (음수 PnL 연속)."""
    longest = 0
    streak = 0
    for p in pnls:
        if safe_float(p) < 0:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
    return longest


def compute_profit_factor(pnls: Sequence[float]) -> Any:
    """총이익 / |총손실|.

    정책:
    - total_loss > 0 → 비율 반환 (float).
    - total_loss == 0 AND total_win > 0 → None (무한 회피, JSON 안전).
    - total_loss == 0 AND total_win == 0 → 0.0 (거래 0건 또는 모두 0).
    """
    wins  = sum(safe_float(p) for p in pnls if safe_float(p) > 0)
    losses = abs(sum(safe_float(p) for p in pnls if safe_float(p) < 0))
    if losses > 0:
        return safe_float(wins / losses)
    return None if wins > 0 else 0.0


def compute_sharpe_like_score(pnls: Sequence[float]) -> float:
    """Pseudo-Sharpe: mean(pnl) / std(pnl) × √N.

    엄밀한 Sharpe (무위험 수익률 / 일별 returns) 와 다름 — 거래 PnL 기반의
    근사. 표본 < 2 또는 std=0 이면 0.0.
    """
    n = len(pnls)
    if n < 2:
        return 0.0
    floats = [safe_float(p) for p in pnls]
    mean = sum(floats) / n
    var = sum((p - mean) ** 2 for p in floats) / (n - 1)
    if var <= 0:
        return 0.0
    std = math.sqrt(var)
    return safe_float((mean / std) * math.sqrt(n))


def annualize_return(total_return: float, trading_days: int) -> float:
    """기간 환산 연환산 — 거래일 252 가정."""
    if trading_days <= 0 or total_return <= -1.0:
        return 0.0
    years = trading_days / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return 0.0
    try:
        return safe_float((1.0 + total_return) ** (1.0 / years) - 1.0)
    except (OverflowError, ValueError):
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 4. 표준 metrics 산출
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricsInput:
    """compute_performance_metrics input — 호환성 보존용 dataclass.

    명시적 dataclass 로 받으면 caller (백테스트 / 최적화 / 스트레스) 가
    인자 시그니처에 의존 안 함.
    """

    trades:           list[Any]
    initial_cash:     int
    trading_days:     int
    raw_total_return: float = 0.0
    fees_paid:        float = 0.0
    taxes_paid:       float = 0.0
    slippage_paid:    float = 0.0
    max_drawdown:     float | None = None        # None 이면 PnL 기반 estimate
    equity_curve:     list[float] = field(default_factory=list)


def compute_performance_metrics(
    *,
    trades:           Sequence[Any],
    initial_cash:     int,
    trading_days:     int,
    raw_total_return: float = 0.0,
    fees_paid:        float = 0.0,
    taxes_paid:       float = 0.0,
    slippage_paid:    float = 0.0,
    max_drawdown:     float | None = None,
    equity_curve:     Sequence[float] | None = None,
) -> dict[str, Any]:
    """14 표준 지표 산출 — JSON 직렬화 가능 dict.

    Args:
        trades: pnl 필드를 가진 객체 / dict 시퀀스.
        initial_cash: 초기 자본 (KRW).
        trading_days: 백테스트 거래일 수 (annualized 계산).
        raw_total_return: 수수료 / 슬리피지 *반영 전* 누적 수익률.
        fees_paid / taxes_paid / slippage_paid: 누적 비용 (KRW, 절대값).
        max_drawdown: 사전 계산값 (None 이면 equity_curve 또는 PnL 기반).
        equity_curve: account balance series — 있으면 우선.

    Returns:
        15 키 dict (14 spec + avg_trade_pnl alias) — JSON 호환.

    빈 거래 / 손실 없는 경우 모두 안전 처리 (예외 raise 0건).
    """
    pnls = [extract_pnl(t) for t in trades]
    trade_count = len(pnls)

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_count = len(wins)

    win_rate = (win_count / trade_count) if trade_count > 0 else 0.0
    total_win  = sum(wins)
    total_loss = abs(sum(losses))

    avg_trade_pnl = (sum(pnls) / trade_count) if trade_count > 0 else 0.0
    avg_win  = (total_win / win_count) if win_count > 0 else 0.0
    avg_loss = (total_loss / len(losses)) if losses else 0.0   # 절대값

    expectancy = avg_trade_pnl                                  # KRW per trade

    profit_factor = compute_profit_factor(pnls)
    loss_streak = compute_loss_streak(pnls)
    sharpe_like = compute_sharpe_like_score(pnls)

    # max_drawdown — 우선순위: 인자 → equity_curve → pnl-cumulative.
    if max_drawdown is not None:
        mdd = max(0.0, min(1.0, safe_float(max_drawdown)))
    elif equity_curve:
        mdd = compute_max_drawdown_from_equity(equity_curve)
    else:
        mdd = compute_max_drawdown(pnls)

    initial = max(safe_float(initial_cash), 1.0)
    fees    = safe_float(fees_paid)
    taxes   = safe_float(taxes_paid)
    slip    = safe_float(slippage_paid)
    raw_ret = safe_float(raw_total_return)

    fee_adjusted_return      = raw_ret - (fees + taxes) / initial
    slippage_adjusted_return = fee_adjusted_return - slip / initial

    # risk_adjusted_score — expectancy 를 initial 로 정규화 후 max_dd 로 나눔.
    if mdd > 0:
        risk_adjusted_score = (expectancy / initial) / mdd
    else:
        risk_adjusted_score = 0.0

    annualized = annualize_return(raw_ret, int(trading_days))

    return {
        "total_return":             safe_float(raw_ret),
        "annualized_return":        safe_float(annualized),
        "win_rate":                 safe_float(win_rate),
        "trade_count":              int(trade_count),
        "profit_factor":            profit_factor,        # None | float
        "expectancy":               safe_float(expectancy),
        "max_drawdown":             safe_float(mdd),
        "avg_trade_pnl":            safe_float(avg_trade_pnl),   # alias of expectancy
        "avg_win":                  safe_float(avg_win),
        "avg_loss":                 safe_float(avg_loss),
        "loss_streak":              int(loss_streak),
        "risk_adjusted_score":      safe_float(risk_adjusted_score),
        "sharpe_like_score":        safe_float(sharpe_like),
        "fee_adjusted_return":      safe_float(fee_adjusted_return),
        "slippage_adjusted_return": safe_float(slippage_adjusted_return),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. 검증 / INSUFFICIENT_DATA helper
# ─────────────────────────────────────────────────────────────────────────────


def assert_required_keys_present(metrics: dict[str, Any]) -> list[str]:
    """``PERFORMANCE_METRIC_KEYS`` 누락 키 리스트 반환. 빈 리스트면 OK."""
    return [k for k in PERFORMANCE_METRIC_KEYS if k not in metrics]


def is_insufficient_data(
    metrics: dict[str, Any],
    *,
    min_trade_count: int = DEFAULT_MIN_TRADE_COUNT_FOR_FULL,
) -> bool:
    """trade_count < min_trade_count → INSUFFICIENT_DATA 권장.

    본 helper 는 *advisory* — 호출자 (verdict 분류기) 가 최종 결정.
    """
    return int(metrics.get("trade_count", 0) or 0) < int(min_trade_count)


def safe_empty_metrics(*, initial_cash: int = 1) -> dict[str, Any]:
    """빈 거래 시 안전 기본값 — 모든 키 0 / None.

    `compute_performance_metrics(trades=[])` 와 동일한 결과를 호출자 친화적
    helper 로 노출.
    """
    return compute_performance_metrics(
        trades=[], initial_cash=initial_cash, trading_days=0,
    )
