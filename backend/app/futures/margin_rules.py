"""#48: Futures margin / leverage / liquidation risk rules.

`FuturesRiskManager`(#151)의 단일 함수 `evaluate_virtual_order`가 inline으로
처리하던 가드들을 *명시적 Rule*로 분리한다. 주식 `PositionLimitRule`(#35)이
한도 검사를 단일 진실로 모은 것과 같은 패턴 — 향후 가드 추가 시에도 한 곳에서
관리하고, audit / API surface가 동일 helper를 재사용할 수 있다.

## 세 가지 Rule

| Rule | 책임 | 산식 |
|---|---|---|
| `FuturesMarginRule` | 신규 주문의 initial margin이 `margin_available`로 충당 가능한가 + `margin_used + initial_margin ≤ max_margin_used` + maintenance margin 잔여 buffer | `notional/leverage` (initial), `notional × maintenance_margin_pct/100` |
| `LeverageLimitRule` | 주문 leverage가 정책 한도(`max_leverage`)와 contract 시장 한도(`leverage_max`) *둘 다* 통과 | `leverage ≤ min(policy.max_leverage, spec.leverage_max)` |
| `LiquidationRiskRule` | mark_price 대비 liquidation_price의 거리(%) 확인 | `distance_pct = abs(mark - liq) / mark × 100` — 3% / 7% threshold |

## 결정 매트릭스 (LiquidationRiskRule)

| `distance_to_liquidation_pct` | 결정 | reason 누적 |
|---|---|---|
| ≤ 3% (default `liquidation_critical_pct`) | `BLOCK` (REJECTED) | "liquidation distance ≤ critical threshold" |
| 3% ~ 7% (default `liquidation_warning_pct`) | `WARN` | "liquidation distance in warning band" — 호출자 결정 (REDUCE_SIZE 권고) |
| ≥ 7% | `PASS` | (reason 누적 없음) |

본 Rule들은 *advisory*로도 사용 가능 — `FuturesMarginRule.preview(...)`가
`MarginPreview` (read-only) dataclass를 반환해 UI / API에 안전하게 노출된다.
어떤 broker.place_order 호출도 본 모듈에서 발생하지 않는다 (정적 grep 가드).

## 강제청산 자동 주문 금지

본 모듈은 강제청산 *위험*을 계산만 하고, 실제 청산 주문을 broker에 보내지
않는다. `MockFuturesBroker.force_liquidate_if_needed`(#151) 역시 가상 환경
전용이며, 본 PR에서 새 자동 청산 경로를 추가하지 않는다.
[`docs/futures_margin_risk.md`](../../../docs/futures_margin_risk.md) §6 참조.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from app.futures.simulation import (
    compute_initial_margin,
    compute_liquidation_price,
)
from app.futures.types import (
    FuturesOrderRequest,
    FuturesPosition,
    FuturesPositionSide,
    FuturesSide,
)


# ====================================================================
# Decisions
# ====================================================================


class MarginRuleDecision(StrEnum):
    """단일 Rule의 판정.

    - `PASS`  — 통과
    - `WARN`  — 통과지만 운영자 주의 (예: liquidation distance 3-7%)
    - `BLOCK` — 거부 (FuturesRiskManager는 REJECTED로 변환)
    """
    PASS  = "PASS"
    WARN  = "WARN"
    BLOCK = "BLOCK"


@dataclass
class MarginRuleResult:
    decision: MarginRuleDecision
    reasons:  list[str]    = field(default_factory=list)
    warnings: list[str]    = field(default_factory=list)
    # 산출 결과 carry — UI / API가 reason 외 수치 값을 함께 보여줄 수 있다.
    metrics:  dict         = field(default_factory=dict)


@dataclass(frozen=True)
class MarginPreview:
    """`FuturesMarginRule.preview()` 응답.

    `notional`, `initial_margin`, `maintenance_margin`, `margin_used_after`,
    `margin_available_after`, `headroom_pct` (잔여 capacity %).

    UI는 본 dataclass를 그대로 노출 — 운영자가 한 화면에서 "이 주문이 들어갔을
    때 잔고가 어떻게 변하나"를 사전 시뮬 가능. broker 호출 0건 (read-only).
    """
    notional:                 int
    initial_margin:           int
    maintenance_margin:       int
    margin_used_after:        int
    margin_available_after:   int
    headroom_pct:             float
    max_margin_used:          int


@dataclass(frozen=True)
class LiquidationPreview:
    """`LiquidationRiskRule.preview()` 응답."""
    mark_price:                int
    liquidation_price:         int
    distance_pct:              float
    side:                      FuturesPositionSide
    severity:                  MarginRuleDecision  # PASS / WARN / BLOCK


# ====================================================================
# 1. LeverageLimitRule
# ====================================================================


@dataclass(frozen=True)
class LeverageLimitRule:
    """주문 leverage가 *정책 한도*와 *contract 시장 한도* 모두 통과하는지.

    필드:
    - `policy_max_leverage`: 운영자가 정책으로 정한 한도 (`FuturesRiskPolicy.max_leverage`)
    - `contract_leverage_max`: 거래소/broker 시장 한도 — None이면 검사 X (운영자
      가 contract spec을 주입하지 않은 환경)

    *작은 값이 효력* — 두 한도 중 더 보수적인 쪽을 적용.
    """

    policy_max_leverage:    float
    contract_leverage_max:  float | None = None

    @property
    def effective_max(self) -> float:
        if self.contract_leverage_max is None:
            return self.policy_max_leverage
        return min(self.policy_max_leverage, self.contract_leverage_max)

    def check(self, leverage: float) -> MarginRuleResult:
        result = MarginRuleResult(decision=MarginRuleDecision.PASS)
        result.metrics["leverage"]            = leverage
        result.metrics["effective_max"]       = self.effective_max
        result.metrics["policy_max"]          = self.policy_max_leverage
        result.metrics["contract_max"]        = self.contract_leverage_max

        # 양수 + finite 검사 — 0 또는 NaN/Inf는 산식 보호용.
        if not isinstance(leverage, (int, float)) or not isfinite(leverage):
            result.decision = MarginRuleDecision.BLOCK
            # 기존 reason substring "leverage" 보존.
            result.reasons.append("leverage must be a finite positive number")
            return result
        if leverage <= 0:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append("leverage must be positive")
            return result

        if leverage > self.effective_max:
            result.decision = MarginRuleDecision.BLOCK
            # "leverage" 및 max_leverage substring 보존 (기존 테스트 호환).
            if (self.contract_leverage_max is not None
                    and self.contract_leverage_max < self.policy_max_leverage):
                result.reasons.append(
                    f"leverage {leverage} exceeds contract leverage_max "
                    f"{self.contract_leverage_max}"
                )
            else:
                result.reasons.append(
                    f"leverage {leverage} exceeds max_leverage {self.policy_max_leverage}"
                )
        return result


# ====================================================================
# 2. FuturesMarginRule
# ====================================================================


@dataclass(frozen=True)
class FuturesMarginRule:
    """초기/유지 증거금 + max_margin_used 한도.

    필드:
    - `max_margin_used`: 정책 절대값 한도
    - `maintenance_margin_pct`: 유지증거금 비율(%) — `notional × pct/100`. 본
      Rule은 maintenance margin이 `margin_available`을 초과하면 WARN을 부여
      (강제청산 buffer 부족 advisory). default 10% (`FuturesSimulationParams`
      와 일치).

    `MockFuturesBroker` / 가상 환경의 `compute_initial_margin`(simulation)을
    그대로 사용 — 실 broker는 broker API의 초기 증거금 응답으로 대체될 수 있다.
    """

    max_margin_used:         int
    maintenance_margin_pct:  float = 10.0

    def preview(
        self,
        *,
        order:            FuturesOrderRequest,
        margin_used:      int,
        margin_available: int,
        mark_price:       int,
        leverage:         float,
    ) -> MarginPreview:
        """주문이 들어가면 잔고가 어떻게 변하나 (read-only)."""
        notional = max(0, mark_price) * max(0, order.quantity)
        if leverage > 0 and notional > 0:
            init_margin = compute_initial_margin(notional=notional, leverage=leverage)
        else:
            init_margin = notional
        maint_margin = int(notional * self.maintenance_margin_pct / 100.0)
        margin_used_after        = margin_used + init_margin
        margin_available_after   = margin_available - init_margin
        denom = self.max_margin_used if self.max_margin_used > 0 else 1
        headroom_pct = max(0.0, (1.0 - margin_used_after / denom) * 100.0)
        return MarginPreview(
            notional=notional,
            initial_margin=init_margin,
            maintenance_margin=maint_margin,
            margin_used_after=margin_used_after,
            margin_available_after=margin_available_after,
            headroom_pct=headroom_pct,
            max_margin_used=self.max_margin_used,
        )

    def check(
        self,
        *,
        order:            FuturesOrderRequest,
        margin_used:      int,
        margin_available: int,
        mark_price:       int,
        leverage:         float,
    ) -> MarginRuleResult:
        result = MarginRuleResult(decision=MarginRuleDecision.PASS)
        if mark_price <= 0:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append("mark_price must be positive")
            return result

        prev = self.preview(
            order=order, margin_used=margin_used,
            margin_available=margin_available,
            mark_price=mark_price, leverage=leverage,
        )
        result.metrics["initial_margin"]      = prev.initial_margin
        result.metrics["maintenance_margin"]  = prev.maintenance_margin
        result.metrics["margin_used_after"]   = prev.margin_used_after
        result.metrics["headroom_pct"]        = prev.headroom_pct

        # 1) 잔고 부족 — substring "margin_available" 보존.
        if margin_available < prev.initial_margin:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append(
                f"margin_available {margin_available} < required {prev.initial_margin}"
            )

        # 2) max_margin_used 한도 — substring "max_margin_used" 보존.
        if prev.margin_used_after > self.max_margin_used:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append(
                f"margin_used {prev.margin_used_after} exceeds "
                f"max_margin_used {self.max_margin_used}"
            )

        # 3) maintenance margin advisory — initial margin 충당 후 잔여
        #    margin_available_after 가 maintenance margin보다 적으면 WARN.
        #    BLOCK이 이미 발생한 상태에서도 metric은 carry — UI에 정보 노출.
        if (result.decision == MarginRuleDecision.PASS
                and prev.margin_available_after < prev.maintenance_margin):
            result.decision = MarginRuleDecision.WARN
            result.warnings.append(
                f"maintenance margin buffer thin: available_after "
                f"{prev.margin_available_after} < maintenance "
                f"{prev.maintenance_margin}"
            )

        return result


# ====================================================================
# 3. LiquidationRiskRule
# ====================================================================


@dataclass(frozen=True)
class LiquidationRiskRule:
    """mark price와 liquidation price의 거리(%)로 강제청산 위험을 평가.

    Thresholds (default — `docs/futures_margin_risk.md` §3에서 향후 조정 가능):
    - `critical_pct = 3.0` — distance ≤ 3% → BLOCK (REJECTED)
    - `warning_pct  = 7.0` — 3% < distance ≤ 7% → WARN (REDUCE_SIZE 권고)
    - distance > 7% → PASS

    `maintenance_margin_pct`는 `compute_liquidation_price` 산식에 그대로 위임 —
    `FuturesSimulationParams.maintenance_margin_pct`와 동일 default (10%) 유지.

    본 Rule은 **자동 청산 주문을 보내지 않는다** — 위험 *계산* 전용. 실제
    청산은 broker가 강제청산을 트리거할 때만 발생.
    """

    critical_pct:           float = 3.0
    warning_pct:            float = 7.0
    maintenance_margin_pct: float = 10.0

    def preview(
        self,
        *,
        side:        FuturesSide,
        entry_price: int,
        mark_price:  int,
        leverage:    float,
    ) -> LiquidationPreview:
        position_side = (
            FuturesPositionSide.LONG if side == FuturesSide.BUY
            else FuturesPositionSide.SHORT
        )
        liq = compute_liquidation_price(
            side=position_side,
            entry_price=entry_price,
            leverage=leverage,
            maintenance_margin_pct=self.maintenance_margin_pct,
        )
        # mark price 대비 절대거리 백분율 — LONG/SHORT 무관 부호 통일.
        distance_pct = (
            abs(mark_price - liq) / mark_price * 100.0
            if mark_price > 0 else 0.0
        )
        # severity 분류
        if distance_pct <= self.critical_pct:
            severity = MarginRuleDecision.BLOCK
        elif distance_pct <= self.warning_pct:
            severity = MarginRuleDecision.WARN
        else:
            severity = MarginRuleDecision.PASS
        return LiquidationPreview(
            mark_price=mark_price,
            liquidation_price=liq,
            distance_pct=distance_pct,
            side=position_side,
            severity=severity,
        )

    def check(
        self,
        *,
        order:        FuturesOrderRequest,
        positions:    list[FuturesPosition],
        mark_price:   int,
        leverage:     float,
    ) -> MarginRuleResult:
        """신규 주문 추가 후 동일 contract의 평균 진입가 기준으로 강제청산
        위험을 평가.

        existing position이 *반대 side*면 (close 의도) 본 Rule은 PASS — 청산
        의도의 주문에 liquidation 위험을 적용하면 항상 차단되기 때문.
        """
        result = MarginRuleResult(decision=MarginRuleDecision.PASS)
        if mark_price <= 0:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append("mark_price must be positive")
            return result
        if leverage <= 0:
            # 다른 Rule이 잡지만, 본 Rule도 안전 측에서 PASS.
            return result

        order_side_pos = (
            FuturesPositionSide.LONG if order.side == FuturesSide.BUY
            else FuturesPositionSide.SHORT
        )
        # 동일 contract의 같은 side existing 합산 — 가중평균 진입가 산출.
        same_side = [
            p for p in positions
            if p.contract == order.contract and p.side == order_side_pos
        ]
        existing_qty = sum(p.quantity for p in same_side)

        # 반대 side가 있으면 close 의도 — Rule은 PASS (caller가 close 결정).
        opposite_side = [
            p for p in positions
            if p.contract == order.contract and p.side != order_side_pos
        ]
        if opposite_side:
            result.metrics["skipped"] = "opposite-side close intent"
            return result

        # 가중평균 진입가 = ((existing_qty × avg_entry) + (new_qty × mark)) / total
        new_qty   = order.quantity
        total_qty = existing_qty + new_qty
        if total_qty <= 0:
            return result  # 0 quantity는 다른 Rule이 처리

        if same_side:
            existing_notional = sum(
                p.entry_price * p.quantity for p in same_side
            )
            blended_entry = int(
                (existing_notional + mark_price * new_qty) / total_qty
            )
        else:
            blended_entry = mark_price  # 신규 진입은 mark price 기준

        prev = self.preview(
            side=order.side, entry_price=blended_entry,
            mark_price=mark_price, leverage=leverage,
        )
        result.metrics["liquidation_price"]    = prev.liquidation_price
        result.metrics["distance_pct"]         = prev.distance_pct
        result.metrics["blended_entry_price"]  = blended_entry

        if prev.severity == MarginRuleDecision.BLOCK:
            result.decision = MarginRuleDecision.BLOCK
            result.reasons.append(
                f"liquidation distance {prev.distance_pct:.2f}% <= "
                f"critical threshold {self.critical_pct}% "
                f"(mark={mark_price}, liq={prev.liquidation_price})"
            )
        elif prev.severity == MarginRuleDecision.WARN:
            result.decision = MarginRuleDecision.WARN
            result.warnings.append(
                f"liquidation distance {prev.distance_pct:.2f}% in warning "
                f"band ({self.critical_pct}% < d <= {self.warning_pct}%)"
            )
        return result


# ====================================================================
# Module invariants
# ====================================================================
#
# - 본 모듈은 broker / OrderExecutor / route_order / app.brokers.* 어떤 모듈도
#   import하지 않는다 (정적 grep 가드).
# - 실제 강제청산 주문 / 자동 close 주문 0건 — Rule들은 모두 read-only 의사결정.
# - `MarginPreview` / `LiquidationPreview`는 frozen dataclass — UI/API가
#   안전하게 직렬화 가능.
