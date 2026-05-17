"""13개 표준 성과지표 — 기존 `backend/app/backtest/metrics.py` 기반 확장.

요구 지표 (사용자 spec):
- total_return                — raw 누적 수익률
- annualized_return           — 거래기간 환산 연환산
- win_rate                    — 승률 (win / total)
- trade_count                 — 거래 횟수
- profit_factor               — 총이익 / |총손실|
- expectancy                  — 기대값 (평균 거래 PnL ÷ unit)
- max_drawdown                — 최대 낙폭 (절대값)
- avg_trade_pnl               — 평균 거래 PnL
- avg_win                     — 평균 승리 PnL
- avg_loss                    — 평균 손실 PnL (절대값)
- loss_streak                 — 최대 연속 손실
- risk_adjusted_score         — expectancy / max_drawdown proxy
- fee_adjusted_return         — raw_return - 수수료 - 세금
- slippage_adjusted_return    — raw_return - 수수료 - 세금 - 슬리피지

본 모듈은 *순수 함수* — broker / DB / network 의존 0건. JSON 직렬화 가능
(int / float / None / list / dict).

CLAUDE.md 절대 원칙: broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

import math
from typing import Any


# 사용자 spec 의 13 개 필수 지표 — paper_candidate / 리포트 lock 용.
REQUIRED_METRIC_KEYS: tuple[str, ...] = (
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
    "fee_adjusted_return",
    "slippage_adjusted_return",
)


def _safe_float(v: Any, default: float = 0.0) -> float:
    """None / NaN / inf 모두 default. JSON 호환 보장."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _max_consecutive_losses(pnls: list[float]) -> int:
    """최대 연속 손실 거래 수."""
    longest = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _annualize(total_return: float, days: int) -> float:
    """기간 환산 연환산. 거래일 약 252 가정."""
    if days <= 0 or total_return <= -1.0:
        return 0.0
    years = days / 252.0
    if years <= 0:
        return 0.0
    try:
        return (1.0 + total_return) ** (1.0 / years) - 1.0
    except (OverflowError, ValueError):
        return 0.0


def compute_extended_metrics(
    *,
    trades: list[Any],
    initial_cash: int,
    trading_days: int,
    raw_return: float,
    fees_paid: float,
    taxes_paid: float,
    slippage_paid: float,
    max_drawdown: float,
) -> dict[str, Any]:
    """13개 표준 지표를 계산해 dict 반환.

    Args:
        trades: BacktestResult.trades — pnl 필드를 가진 객체 / dict 모두 호환.
        initial_cash: 초기 자본 (KRW).
        trading_days: 백테스트 기간 거래일 수 (annualized 계산용).
        raw_return: 수수료 / 슬리피지 *반영 전* 누적 수익률 (예: 0.123 = 12.3%).
        fees_paid: 누적 수수료 (KRW, 절대값).
        taxes_paid: 누적 세금 (KRW, 절대값).
        slippage_paid: 누적 슬리피지 (KRW, 절대값).
        max_drawdown: 최대 낙폭 (0~1 범위, 양수, 절대값).

    Returns:
        dict — REQUIRED_METRIC_KEYS 13개 키 모두 포함. 거래 0건이거나 분모 0
        같은 edge case 도 모두 안전 처리 (NaN / inf 발생 0건).
    """
    pnls = [_safe_float(getattr(t, "pnl", None) if not isinstance(t, dict) else t.get("pnl"))
            for t in trades]
    trade_count = len(pnls)

    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    win_count  = len(wins)
    win_rate   = (win_count / trade_count) if trade_count > 0 else 0.0

    total_win  = sum(wins)
    total_loss = abs(sum(losses))

    profit_factor = (total_win / total_loss) if total_loss > 0 else (
        float("inf") if total_win > 0 else 0.0
    )
    # JSON 호환: inf → None (실 거래 0건의 분모 0 케이스).
    profit_factor_json: Any = None if profit_factor == float("inf") else profit_factor

    avg_trade_pnl = (sum(pnls) / trade_count) if trade_count > 0 else 0.0
    avg_win  = (total_win / win_count) if win_count > 0 else 0.0
    avg_loss = (total_loss / len(losses)) if losses else 0.0   # absolute

    expectancy = avg_trade_pnl                    # KRW per trade (단순 평균)
    loss_streak = _max_consecutive_losses(pnls)

    fees   = _safe_float(fees_paid)
    taxes  = _safe_float(taxes_paid)
    slip   = _safe_float(slippage_paid)
    initial = max(_safe_float(initial_cash), 1.0)

    fee_adjusted_return      = _safe_float(raw_return) - (fees + taxes) / initial
    slippage_adjusted_return = fee_adjusted_return - slip / initial

    annualized_return = _annualize(_safe_float(raw_return), trading_days)

    max_dd = _safe_float(max_drawdown)
    # risk_adjusted_score: expectancy(KRW) → initial 정규화 후 max_dd 로 나눔.
    if max_dd > 0:
        risk_adjusted_score = (expectancy / initial) / max_dd
    else:
        risk_adjusted_score = 0.0

    return {
        "total_return":             _safe_float(raw_return),
        "annualized_return":        _safe_float(annualized_return),
        "win_rate":                 _safe_float(win_rate),
        "trade_count":              int(trade_count),
        "profit_factor":            profit_factor_json,
        "expectancy":               _safe_float(expectancy),
        "max_drawdown":             _safe_float(max_dd),
        "avg_trade_pnl":            _safe_float(avg_trade_pnl),
        "avg_win":                  _safe_float(avg_win),
        "avg_loss":                 _safe_float(avg_loss),
        "loss_streak":              int(loss_streak),
        "risk_adjusted_score":      _safe_float(risk_adjusted_score),
        "fee_adjusted_return":      _safe_float(fee_adjusted_return),
        "slippage_adjusted_return": _safe_float(slippage_adjusted_return),
    }


def assert_required_keys_present(metrics: dict[str, Any]) -> list[str]:
    """REQUIRED_METRIC_KEYS 누락 키 리스트 반환. 빈 리스트면 OK."""
    return [k for k in REQUIRED_METRIC_KEYS if k not in metrics]
