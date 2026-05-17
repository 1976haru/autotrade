"""Walk-forward 검증 연결부 (3-04) — 본 PR 시점에는 *준비 stub*.

walk-forward 의 본 알고리즘은 기존 `app/backtest/walk_forward_runner.py` 에
이미 존재. 본 모듈은 *real-data 파이프라인* 에서 호출 가능한 *구조 / 입력
규격 / OVERFIT_RISK 판정 helper* 만 제공.

후속 PR (3-04) 에서:
- 실제 train / validation 분리 호출 — `walk_forward_runner.run_walk_forward()`
  연결.
- 결과를 `compute_extended_metrics` 13 지표 매트릭스에 채워서 caller 에 carry.

본 PR 에서는 *데이터 구조 + OVERFIT_RISK 판단 helper 만* 정의 — 자동 실행
0건 (운영자 명시 옵트인 후 별도 PR).

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 본 모듈은 *분석 helper* — 자동 주문 / 자동 적용 0건.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class WalkForwardVerdict(StrEnum):
    """train / validation 비교 결과 verdict."""
    HEALTHY        = "HEALTHY"           # train + validation 모두 양호.
    OVERFIT_RISK   = "OVERFIT_RISK"      # train 만 좋고 validation 부진.
    UNDERFIT       = "UNDERFIT"          # train / validation 모두 부진.
    INSUFFICIENT   = "INSUFFICIENT"      # 데이터 부족으로 판정 불가.


@dataclass(frozen=True)
class WalkForwardSplit:
    """train / validation 분리 결과."""

    train_metrics:      dict[str, Any]
    validation_metrics: dict[str, Any]
    fold_count:         int
    train_bars:         int
    validation_bars:    int


@dataclass(frozen=True)
class WalkForwardAssessment:
    """walk-forward verdict + 사유."""

    verdict: WalkForwardVerdict
    reasons: list[str]
    train_expectancy:      float = 0.0
    validation_expectancy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":               self.verdict.value,
            "reasons":               list(self.reasons),
            "train_expectancy":      float(self.train_expectancy),
            "validation_expectancy": float(self.validation_expectancy),
        }


# OVERFIT_RISK 판정 임계값 — 보수적 default.
# train 이 양수인데 validation 이 음수면 OVERFIT_RISK.
DEFAULT_OVERFIT_RATIO_THRESHOLD = 0.5
DEFAULT_MIN_FOLD_COUNT          = 3


def assess_walk_forward_overfit(
    split: WalkForwardSplit,
    *,
    overfit_ratio: float = DEFAULT_OVERFIT_RATIO_THRESHOLD,
    min_fold_count: int = DEFAULT_MIN_FOLD_COUNT,
) -> WalkForwardAssessment:
    """train / validation expectancy 비교로 OVERFIT_RISK 판정.

    Args:
        split: train + validation 결과.
        overfit_ratio: validation_expectancy / train_expectancy 가 이 비율 미만이면
            OVERFIT_RISK (train_expectancy > 0 일 때만 적용).
        min_fold_count: 최소 fold 수 — 부족하면 INSUFFICIENT.

    Returns:
        WalkForwardAssessment.
    """
    reasons: list[str] = []

    if split.fold_count < min_fold_count:
        reasons.append(
            f"fold_count={split.fold_count} < min={min_fold_count}"
        )
        return WalkForwardAssessment(
            verdict=WalkForwardVerdict.INSUFFICIENT,
            reasons=reasons,
        )

    train_exp = float(split.train_metrics.get("expectancy", 0.0) or 0.0)
    val_exp   = float(split.validation_metrics.get("expectancy", 0.0) or 0.0)

    if train_exp <= 0 and val_exp <= 0:
        reasons.append(f"train_expectancy={train_exp:.2f}, val={val_exp:.2f} both <= 0")
        return WalkForwardAssessment(
            verdict=WalkForwardVerdict.UNDERFIT,
            reasons=reasons,
            train_expectancy=train_exp,
            validation_expectancy=val_exp,
        )

    if train_exp > 0:
        ratio = (val_exp / train_exp) if train_exp != 0 else 0.0
        if val_exp <= 0 or ratio < overfit_ratio:
            reasons.append(
                f"validation_expectancy={val_exp:.2f} much lower than "
                f"train={train_exp:.2f} (ratio={ratio:.3f} < {overfit_ratio})"
            )
            return WalkForwardAssessment(
                verdict=WalkForwardVerdict.OVERFIT_RISK,
                reasons=reasons,
                train_expectancy=train_exp,
                validation_expectancy=val_exp,
            )

    reasons.append("train + validation expectancy both positive and balanced")
    return WalkForwardAssessment(
        verdict=WalkForwardVerdict.HEALTHY,
        reasons=reasons,
        train_expectancy=train_exp,
        validation_expectancy=val_exp,
    )
