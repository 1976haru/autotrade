"""3-03 — parameter optimization verdict 분류기.

3-02 의 `BacktestVerdict` (4단계 — INSUFFICIENT_DATA / LOW_QUALITY /
HIGH_DRAWDOWN / BACKTEST_PASS) 와 *별개*. 3-03 grid search 는 5단계 verdict
를 사용한다:

| 우선순위 | Verdict | 조건 |
|---|---|---|
| 1 | `INSUFFICIENT_DATA`   | trade_count < min_trade_count       |
| 2 | `NEGATIVE_EXPECTANCY` | expectancy <= 0 (비용 반영 전)        |
| 3 | `HIGH_DRAWDOWN`       | max_drawdown > max_drawdown_pct     |
| 4 | `LOW_QUALITY`         | profit_factor < min_profit_factor   |
| 5 | `PAPER_CANDIDATE`     | 위 4 가지 모두 통과                     |

본 verdict 는 *분류 라벨* — 실거래 자동 활성화 / 자동 paper trader 시작 /
mode 변경 의미 0건. PAPER_CANDIDATE 는 *paper 운용 후보* — 운영자 검토 후
Paper Auto Loop 수동 입력.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 본 verdict 분류기는 *순수 함수* — DB / network / broker 의존 0건.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class OptimizationVerdict(StrEnum):
    """5단계 verdict — JSON / 리포트에 그대로 emit. BUY/SELL/HOLD 0개."""

    INSUFFICIENT_DATA   = "INSUFFICIENT_DATA"
    NEGATIVE_EXPECTANCY = "NEGATIVE_EXPECTANCY"
    HIGH_DRAWDOWN       = "HIGH_DRAWDOWN"
    LOW_QUALITY         = "LOW_QUALITY"
    PAPER_CANDIDATE     = "PAPER_CANDIDATE"


# 필터 임계값 default — 3-02 와 동일 보수값.
DEFAULT_MIN_TRADE_COUNT     = 10
DEFAULT_MIN_PROFIT_FACTOR   = 1.10
DEFAULT_MAX_DRAWDOWN_PCT    = 0.15        # 15%
DEFAULT_MIN_EXPECTANCY_KRW = 0.0          # > 0 권장 (KRW per trade).


@dataclass(frozen=True)
class OptimizationThresholds:
    """grid search verdict 임계값."""

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
class OptimizationClassification:
    """단일 grid run 의 verdict + 사유."""

    verdict:         OptimizationVerdict
    reasons:         list[str]
    used_thresholds: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":              self.verdict.value,
            "reasons":              list(self.reasons),
            "used_thresholds":      dict(self.used_thresholds),
            # 본 verdict 는 *분류 라벨* — 주문 신호 / 자동 적용 / 실거래 허가 X.
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


def classify_optimization_run(
    metrics: dict[str, Any],
    *,
    thresholds: OptimizationThresholds | None = None,
) -> OptimizationClassification:
    """단일 grid run metric → 5단계 verdict.

    Args:
        metrics: 백테스트 결과 metric dict. 최소 ``trade_count`` /
            ``expectancy`` / ``profit_factor`` / ``max_drawdown`` 키 필요.
        thresholds: 운영자 override.

    Returns:
        OptimizationClassification — JSON 직렬화 가능.
    """
    th = thresholds or OptimizationThresholds()
    reasons: list[str] = []

    trade_count   = int(metrics.get("trade_count", 0) or 0)
    expectancy    = float(metrics.get("expectancy", 0.0) or 0.0)
    pf_raw        = metrics.get("profit_factor")
    profit_factor = float(pf_raw) if isinstance(pf_raw, (int, float)) else 0.0
    max_dd        = float(metrics.get("max_drawdown", 0.0) or 0.0)

    # 1) INSUFFICIENT_DATA — 거래 수 부족.
    if trade_count < th.min_trade_count:
        reasons.append(f"trade_count={trade_count} < min={th.min_trade_count}")
        return OptimizationClassification(
            verdict=OptimizationVerdict.INSUFFICIENT_DATA,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 2) NEGATIVE_EXPECTANCY — 기대값 0 이하.
    if expectancy <= th.min_expectancy_krw:
        reasons.append(
            f"expectancy={expectancy:.2f} <= min={th.min_expectancy_krw}"
        )
        return OptimizationClassification(
            verdict=OptimizationVerdict.NEGATIVE_EXPECTANCY,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 3) HIGH_DRAWDOWN — 손실 방어 우선 (수익률보다 lock).
    if max_dd > th.max_drawdown_pct:
        reasons.append(
            f"max_drawdown={max_dd:.4f} > max={th.max_drawdown_pct}"
        )
        return OptimizationClassification(
            verdict=OptimizationVerdict.HIGH_DRAWDOWN,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 4) LOW_QUALITY — profit_factor 미달.
    if profit_factor < th.min_profit_factor:
        reasons.append(
            f"profit_factor={profit_factor:.4f} < min={th.min_profit_factor}"
        )
        return OptimizationClassification(
            verdict=OptimizationVerdict.LOW_QUALITY,
            reasons=reasons,
            used_thresholds=th.to_dict(),
        )

    # 5) 모든 기준 통과 — paper 후보 자격.
    reasons.append("all_filters_passed")
    return OptimizationClassification(
        verdict=OptimizationVerdict.PAPER_CANDIDATE,
        reasons=reasons,
        used_thresholds=th.to_dict(),
    )
