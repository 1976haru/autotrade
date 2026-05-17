"""3-02 백테스트 결과 verdict 분류기 — 4단계.

사용자 spec 의 필터 기준 (3-02 범위):
- trade_count < 10            → INSUFFICIENT_DATA
- profit_factor < 1.10        → LOW_QUALITY
- max_drawdown > 15%          → HIGH_DRAWDOWN
- 모든 기준 통과              → BACKTEST_PASS

우선순위 (가장 엄격한 거부 사유가 먼저):
    INSUFFICIENT_DATA > HIGH_DRAWDOWN > LOW_QUALITY > BACKTEST_PASS

3-07 (paper_candidate_config) 의 PAPER_CANDIDATE verdict 는 *별개 PR*. 본 PR
에서는 BACKTEST_PASS 까지만 — paper 운용 후보 export 는 수행하지 않는다.

CLAUDE.md 절대 원칙:
- 본 verdict 는 *분류 라벨* 일 뿐 — 자동 적용 / 주문 트리거 0건.
- BACKTEST_PASS 라벨은 *백테스트 기준 통과* 를 의미. paper 운용 / 실거래
  활성화 / 자동 promotion 어떤 것도 의미하지 않는다.
- broker / OrderExecutor / route_order import 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BacktestVerdict(StrEnum):
    """4단계 verdict — 단어 그대로 리포트 / JSON 에 emit. BUY/SELL/HOLD 0개."""

    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    LOW_QUALITY       = "LOW_QUALITY"
    HIGH_DRAWDOWN     = "HIGH_DRAWDOWN"
    BACKTEST_PASS     = "BACKTEST_PASS"


# 필터 임계값 — CLI 인자로 override 가능.
DEFAULT_MIN_TRADE_COUNT     = 10
DEFAULT_MIN_PROFIT_FACTOR   = 1.10
DEFAULT_MAX_DRAWDOWN_PCT    = 0.15   # 15%


@dataclass(frozen=True)
class FilterThresholds:
    """필터 임계값 묶음."""

    min_trade_count:        int   = DEFAULT_MIN_TRADE_COUNT
    min_profit_factor:      float = DEFAULT_MIN_PROFIT_FACTOR
    max_drawdown_pct:       float = DEFAULT_MAX_DRAWDOWN_PCT

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_trade_count":   self.min_trade_count,
            "min_profit_factor": self.min_profit_factor,
            "max_drawdown_pct":  self.max_drawdown_pct,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """단일 backtest run 의 verdict + 사유."""

    verdict:         BacktestVerdict
    reasons:         list[str]
    used_thresholds: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":         self.verdict.value,
            "reasons":         list(self.reasons),
            "used_thresholds": dict(self.used_thresholds),
            # 본 verdict 는 분류 라벨 — 주문 신호 / 자동 적용 아님.
            "is_order_signal":      False,
            "auto_apply_allowed":   False,
            "is_live_authorization": False,
        }


def classify_backtest_metrics(
    metrics: dict[str, Any],
    *,
    thresholds: FilterThresholds | None = None,
) -> ClassificationResult:
    """backtest 지표 dict 를 보고 4단계 verdict 분류.

    Args:
        metrics: 백테스트 결과 metric dict — 최소 ``trade_count`` /
            ``profit_factor`` / ``max_drawdown`` 키 필요.
        thresholds: 운영자 override.

    Returns:
        ClassificationResult — JSON 직렬화 가능.
    """
    th = thresholds or FilterThresholds()
    reasons: list[str] = []

    trade_count   = int(metrics.get("trade_count", 0) or 0)
    pf_raw        = metrics.get("profit_factor")
    profit_factor = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    max_dd        = float(metrics.get("max_drawdown", 0.0) or 0.0)

    # 1) INSUFFICIENT_DATA — 거래 수 부족.
    if trade_count < th.min_trade_count:
        reasons.append(f"trade_count={trade_count} < min={th.min_trade_count}")
        return ClassificationResult(
            verdict=BacktestVerdict.INSUFFICIENT_DATA,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 2) HIGH_DRAWDOWN — 손실 방어 우선 (수익률보다 lock).
    if max_dd > th.max_drawdown_pct:
        reasons.append(
            f"max_drawdown={max_dd:.4f} > max={th.max_drawdown_pct}"
        )
        return ClassificationResult(
            verdict=BacktestVerdict.HIGH_DRAWDOWN,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 3) LOW_QUALITY — profit_factor 미달.
    if profit_factor < th.min_profit_factor:
        reasons.append(
            f"profit_factor={profit_factor:.4f} < min={th.min_profit_factor}"
        )
        return ClassificationResult(
            verdict=BacktestVerdict.LOW_QUALITY,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 4) 모든 기준 통과.
    reasons.append("all_filters_passed")
    return ClassificationResult(
        verdict=BacktestVerdict.BACKTEST_PASS,
        reasons=reasons,
        used_thresholds=th.to_dict(),
    )
