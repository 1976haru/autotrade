"""백테스트 결과 분류기 — 5단계 verdict.

사용자 spec 의 필터 기준:
- trade_count < 10            → INSUFFICIENT_DATA
- expectancy <= 0             → NEGATIVE_EXPECTANCY
- profit_factor < 1.10        → LOW_QUALITY
- max_drawdown > 15%          → HIGH_DRAWDOWN
- 수수료 / 슬리피지 반영 후 기대값 양수 + 기준 통과 → PAPER_CANDIDATE

verdict 우선순위 (가장 엄격한 거부 사유가 우선 — 그래야 운영자가 가장 문제
되는 원인을 즉시 확인):
    INSUFFICIENT_DATA > NEGATIVE_EXPECTANCY > HIGH_DRAWDOWN > LOW_QUALITY
                                                          > PAPER_CANDIDATE

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 본 filter 는 *분류기* 일 뿐 — 자동 적용 / 주문 트리거 0건.
- PAPER_CANDIDATE 라벨은 *paper 운용 검토 가능* — 자동 실거래 활성화 아님.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class BacktestVerdict(StrEnum):
    """5단계 verdict — 단어 그대로 paper_candidate_config 의 ``validation_status``
    필드에 emit. BUY/SELL/HOLD 값 0개 (정책 + 테스트 lock)."""

    INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"
    NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
    HIGH_DRAWDOWN       = "HIGH_DRAWDOWN"
    LOW_QUALITY         = "LOW_QUALITY"
    PAPER_CANDIDATE     = "PAPER_CANDIDATE"


# Filter thresholds — 운영자 override 가능 (CLI 인자로 받음).
DEFAULT_MIN_TRADE_COUNT     = 10
DEFAULT_MIN_PROFIT_FACTOR   = 1.10
DEFAULT_MAX_DRAWDOWN_PCT    = 0.15   # 15%
DEFAULT_MIN_EXPECTANCY_KRW = 0.0     # >0 권장 — KRW 단위 expectancy.


@dataclass(frozen=True)
class FilterThresholds:
    """필터 임계값 묶음 — caller 가 override 가능."""

    min_trade_count:        int   = DEFAULT_MIN_TRADE_COUNT
    min_profit_factor:      float = DEFAULT_MIN_PROFIT_FACTOR
    max_drawdown_pct:       float = DEFAULT_MAX_DRAWDOWN_PCT
    min_expectancy_krw:     float = DEFAULT_MIN_EXPECTANCY_KRW

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_trade_count":    self.min_trade_count,
            "min_profit_factor":  self.min_profit_factor,
            "max_drawdown_pct":   self.max_drawdown_pct,
            "min_expectancy_krw": self.min_expectancy_krw,
        }


@dataclass(frozen=True)
class ClassificationResult:
    """단일 backtest run 의 verdict + 라벨 carry. paper_candidate JSON 에 그대로 emit."""

    verdict:           BacktestVerdict
    reasons:           list[str]                  # 사람이 읽는 사유 라벨
    used_thresholds:   dict[str, Any]
    fee_adjusted_pos:  bool                       # 비용 반영 expectancy 양수 여부

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":          self.verdict.value,
            "reasons":          list(self.reasons),
            "used_thresholds":  dict(self.used_thresholds),
            "fee_adjusted_positive": self.fee_adjusted_pos,
        }


def classify_backtest_result(
    metrics: dict[str, Any],
    *,
    thresholds: FilterThresholds | None = None,
) -> ClassificationResult:
    """13개 metric 값을 보고 verdict 분류.

    Args:
        metrics: `compute_extended_metrics()` 결과 dict.
        thresholds: 운영자 override.

    Returns:
        ClassificationResult — paper_candidate config 의 validation_status carry.
    """
    th = thresholds or FilterThresholds()
    reasons: list[str] = []

    trade_count        = int(metrics.get("trade_count", 0))
    expectancy         = float(metrics.get("expectancy", 0.0) or 0.0)
    pf_raw             = metrics.get("profit_factor")
    profit_factor      = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    max_dd             = float(metrics.get("max_drawdown", 0.0) or 0.0)
    fee_adj            = float(metrics.get("fee_adjusted_return", 0.0) or 0.0)
    slip_adj           = float(metrics.get("slippage_adjusted_return", 0.0) or 0.0)

    fee_adjusted_pos = (fee_adj > 0.0) and (slip_adj > 0.0)

    # 1) INSUFFICIENT_DATA — 거래 수 부족.
    if trade_count < th.min_trade_count:
        reasons.append(
            f"trade_count={trade_count} < min={th.min_trade_count}"
        )
        return ClassificationResult(
            verdict=BacktestVerdict.INSUFFICIENT_DATA,
            reasons=reasons,
            used_thresholds=th.to_dict(),
            fee_adjusted_pos=fee_adjusted_pos,
        )

    # 2) NEGATIVE_EXPECTANCY — 비용 반영 / raw 모두 음수면 즉시 거부.
    if expectancy <= th.min_expectancy_krw:
        reasons.append(
            f"expectancy={expectancy:.2f} <= min={th.min_expectancy_krw}"
        )
        return ClassificationResult(
            verdict=BacktestVerdict.NEGATIVE_EXPECTANCY,
            reasons=reasons,
            used_thresholds=th.to_dict(),
            fee_adjusted_pos=fee_adjusted_pos,
        )

    # 3) HIGH_DRAWDOWN — 손실 방어가 우선 (수익률보다 lock).
    if max_dd > th.max_drawdown_pct:
        reasons.append(
            f"max_drawdown={max_dd:.4f} > max={th.max_drawdown_pct}"
        )
        return ClassificationResult(
            verdict=BacktestVerdict.HIGH_DRAWDOWN,
            reasons=reasons,
            used_thresholds=th.to_dict(),
            fee_adjusted_pos=fee_adjusted_pos,
        )

    # 4) LOW_QUALITY — profit_factor 또는 비용 반영 음수.
    low_pf = profit_factor < th.min_profit_factor
    if low_pf:
        reasons.append(
            f"profit_factor={profit_factor:.4f} < min={th.min_profit_factor}"
        )
    if not fee_adjusted_pos:
        reasons.append(
            f"fee_adjusted_return={fee_adj:.4f} or "
            f"slippage_adjusted_return={slip_adj:.4f} not positive"
        )
    if low_pf or not fee_adjusted_pos:
        return ClassificationResult(
            verdict=BacktestVerdict.LOW_QUALITY,
            reasons=reasons,
            used_thresholds=th.to_dict(),
            fee_adjusted_pos=fee_adjusted_pos,
        )

    # 5) 모든 조건 통과 — paper 후보.
    reasons.append("all_filters_passed")
    return ClassificationResult(
        verdict=BacktestVerdict.PAPER_CANDIDATE,
        reasons=reasons,
        used_thresholds=th.to_dict(),
        fee_adjusted_pos=fee_adjusted_pos,
    )
