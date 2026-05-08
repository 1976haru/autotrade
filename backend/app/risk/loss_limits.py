"""Loss Limit Rules (#36).

일일 손실한도 / 주간 손실한도 / 연속손실 중단을 명시적인 rule 객체로 분리.
RiskManager.evaluate_order이 본 rule들에 위임해 신규 BUY를 차단한다.

설계 원칙:
- **신규 BUY는 차단, SELL/EXIT은 통과**. 리스크 축소 주문을 막으면 손실이
  더 커지는 역효과 — CLAUDE.md '손실 방어 우선' 원칙.
- **soft → hard 단계**: WARN → REDUCE_SIZE → BLOCK_NEW_BUY. RiskCheckResult가
  size 조정을 직접 지원하지 않으므로 REDUCE_SIZE는 warning + reason으로만
  surface (실제 사이즈 축소는 호출자/PositionSizingAgent의 책임 — TODO).
- **realized PnL only**: unrealized(평가손익)는 stale price 위험이 있어
  본 rule이 직접 사용하지 않는다. 호출자가 broker statement reconciliation
  후 신뢰 가능하면 별도 인자로 주입 가능.
- **KST 기준**: 일일=KST date, 주간=월요일 00:00 KST 시작.

본 모듈은 broker / PermissionGate / OrderExecutor / route_order 어떤 함수도
호출하지 않는다. DB write 0건 — 순수 함수.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from app.brokers.base import OrderRequest, OrderSide


class LossLimitDecision(StrEnum):
    """rule이 반환할 결정. RiskManager가 보고 reasons/warnings에 반영."""
    ALLOW          = "ALLOW"          # 한도 미설정 또는 충분한 여유
    WARN           = "WARN"           # 임계의 X% 도달 — 운영자 인지 필요
    REDUCE_SIZE    = "REDUCE_SIZE"    # 임계의 Y% 도달 — 사이즈 축소 권고
    BLOCK_NEW_BUY  = "BLOCK_NEW_BUY"  # 임계 도달/초과 — 신규 BUY 차단


@dataclass(frozen=True)
class LossLimitResult:
    """단일 rule 평가 결과.

    `block_buy=True`이면 신규 BUY를 차단 (REJECTED). `passed`/`warnings`/`reasons`
    는 RiskManager의 RiskCheckResult에 그대로 merge.
    """
    decision:    LossLimitDecision
    passed:      list[str] = field(default_factory=list)
    warnings:    list[str] = field(default_factory=list)
    reasons:     list[str] = field(default_factory=list)
    indicators:  dict      = field(default_factory=dict)

    @property
    def block_buy(self) -> bool:
        return self.decision == LossLimitDecision.BLOCK_NEW_BUY

    def to_dict(self) -> dict:
        return {
            "decision":   self.decision.value,
            "passed":     list(self.passed),
            "warnings":   list(self.warnings),
            "reasons":    list(self.reasons),
            "indicators": dict(self.indicators),
            "block_buy":  self.block_buy,
        }


# ====================================================================
# DailyLossLimitRule
# ====================================================================


class DailyLossLimitRule:
    """일일 realized PnL이 임계에 도달하면 신규 BUY 차단.

    soft → hard 단계 (모두 옵션, 0이면 비활성):
    - `warn_pct`: 임계의 X% (e.g., 50%) 손실 도달 → WARN.
    - `reduce_pct`: 임계의 Y% (e.g., 70%) 손실 도달 → REDUCE_SIZE 권고.
    - 100% (=`limit`): 임계 전체 → BLOCK_NEW_BUY.

    `limit=0`이면 검사 비활성 (기존 evaluate_order에 이미 별도 max_daily_loss
    hard reject가 있으므로 본 rule은 *보완* 단계만 추가).
    """

    def __init__(
        self,
        limit: int,                  # 절대값 (양수). 0이면 비활성.
        *,
        warn_pct: float = 0.0,       # 0 = WARN 단계 비활성
        reduce_pct: float = 0.0,     # 0 = REDUCE_SIZE 단계 비활성
    ):
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if not (0 <= warn_pct <= 100):
            raise ValueError("warn_pct must be in [0, 100]")
        if not (0 <= reduce_pct <= 100):
            raise ValueError("reduce_pct must be in [0, 100]")
        if warn_pct > 0 and reduce_pct > 0 and warn_pct >= reduce_pct:
            raise ValueError("warn_pct must be < reduce_pct (soft → hard 순서)")
        self.limit       = int(limit)
        self.warn_pct    = float(warn_pct)
        self.reduce_pct  = float(reduce_pct)

    def evaluate(self, *, daily_pnl: int, order: OrderRequest) -> LossLimitResult:
        """daily_pnl(음수=손실)을 받아 결정 반환.

        SELL/EXIT은 차단하지 않는다 — 리스크 축소 보호.
        """
        # 한도 비활성 또는 손실이 아님
        if self.limit <= 0 or daily_pnl >= 0:
            return LossLimitResult(
                decision=LossLimitDecision.ALLOW,
                passed=["daily loss within limit"] if self.limit > 0 else [],
                indicators={"daily_pnl": daily_pnl, "daily_limit": self.limit},
            )

        loss = -daily_pnl  # 양수로 변환
        usage_pct = loss / self.limit * 100.0

        # SELL은 통과 (차단 안 함). 단, 결정 자체는 BUY 기준으로 산출 — 운영자
        # 인지를 위해 reason은 그대로 surface.
        is_buy = order.side == OrderSide.BUY

        indicators = {
            "daily_pnl":   daily_pnl,
            "daily_limit": self.limit,
            "usage_pct":   round(usage_pct, 2),
        }

        # 100% 이상 — BLOCK_NEW_BUY
        if loss >= self.limit:
            reason = (
                f"daily loss {loss} ≥ daily_loss_limit {self.limit} — "
                f"신규 BUY 차단 (#36 DailyLossLimitRule)"
            )
            if is_buy:
                return LossLimitResult(
                    decision=LossLimitDecision.BLOCK_NEW_BUY,
                    reasons=[reason],
                    indicators=indicators,
                )
            return LossLimitResult(
                decision=LossLimitDecision.BLOCK_NEW_BUY,
                warnings=[f"{reason} (SELL/EXIT은 통과 — 리스크 축소 허용)"],
                indicators=indicators,
            )

        # reduce_pct 도달 — REDUCE_SIZE 권고 (BUY/SELL 모두 surface)
        if self.reduce_pct > 0 and usage_pct >= self.reduce_pct:
            return LossLimitResult(
                decision=LossLimitDecision.REDUCE_SIZE,
                warnings=[
                    f"daily loss {loss} reached {usage_pct:.1f}% of limit "
                    f"{self.limit} — REDUCE_SIZE 권고 (사이즈 축소 권장)"
                ],
                indicators=indicators,
            )

        # warn_pct 도달 — WARN
        if self.warn_pct > 0 and usage_pct >= self.warn_pct:
            return LossLimitResult(
                decision=LossLimitDecision.WARN,
                warnings=[
                    f"daily loss {loss} reached {usage_pct:.1f}% of limit "
                    f"{self.limit} — 운영자 주의"
                ],
                indicators=indicators,
            )

        return LossLimitResult(
            decision=LossLimitDecision.ALLOW,
            passed=["daily loss within limit"],
            indicators=indicators,
        )


# ====================================================================
# WeeklyLossLimitRule
# ====================================================================


class WeeklyLossLimitRule:
    """주간 realized PnL이 임계에 도달하면 신규 BUY 차단.

    주간 기준 = 월요일 00:00 KST ~ 일요일 23:59 KST. `daily_pnl`이 일일 변동을
    잡는다면, 본 rule은 *장기 누적* 손실을 잡는다 — 매일 한도 미만이지만
    주간으로 보면 큰 손실인 케이스 (감정적 복구매매 패턴).

    `limit=0`이면 검사 비활성. soft 단계 (warn/reduce)는 일일과 동일 정책.
    """

    def __init__(
        self,
        limit: int,
        *,
        warn_pct: float = 0.0,
        reduce_pct: float = 0.0,
    ):
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if not (0 <= warn_pct <= 100):
            raise ValueError("warn_pct must be in [0, 100]")
        if not (0 <= reduce_pct <= 100):
            raise ValueError("reduce_pct must be in [0, 100]")
        if warn_pct > 0 and reduce_pct > 0 and warn_pct >= reduce_pct:
            raise ValueError("warn_pct must be < reduce_pct")
        self.limit      = int(limit)
        self.warn_pct   = float(warn_pct)
        self.reduce_pct = float(reduce_pct)

    def evaluate(self, *, weekly_pnl: int, order: OrderRequest) -> LossLimitResult:
        if self.limit <= 0 or weekly_pnl >= 0:
            return LossLimitResult(
                decision=LossLimitDecision.ALLOW,
                passed=["weekly loss within limit"] if self.limit > 0 else [],
                indicators={"weekly_pnl": weekly_pnl, "weekly_limit": self.limit},
            )

        loss = -weekly_pnl
        usage_pct = loss / self.limit * 100.0
        is_buy = order.side == OrderSide.BUY

        indicators = {
            "weekly_pnl":   weekly_pnl,
            "weekly_limit": self.limit,
            "usage_pct":    round(usage_pct, 2),
        }

        if loss >= self.limit:
            reason = (
                f"weekly loss {loss} ≥ weekly_loss_limit {self.limit} — "
                f"신규 BUY 차단 (자동운용 pause 권고, #36 WeeklyLossLimitRule)"
            )
            if is_buy:
                return LossLimitResult(
                    decision=LossLimitDecision.BLOCK_NEW_BUY,
                    reasons=[reason],
                    indicators=indicators,
                )
            return LossLimitResult(
                decision=LossLimitDecision.BLOCK_NEW_BUY,
                warnings=[f"{reason} (SELL/EXIT은 통과)"],
                indicators=indicators,
            )

        if self.reduce_pct > 0 and usage_pct >= self.reduce_pct:
            return LossLimitResult(
                decision=LossLimitDecision.REDUCE_SIZE,
                warnings=[
                    f"weekly loss {loss} reached {usage_pct:.1f}% of limit "
                    f"{self.limit} — REDUCE_SIZE 권고"
                ],
                indicators=indicators,
            )

        if self.warn_pct > 0 and usage_pct >= self.warn_pct:
            return LossLimitResult(
                decision=LossLimitDecision.WARN,
                warnings=[
                    f"weekly loss {loss} reached {usage_pct:.1f}% of limit "
                    f"{self.limit} — 운영자 주의"
                ],
                indicators=indicators,
            )

        return LossLimitResult(
            decision=LossLimitDecision.ALLOW,
            passed=["weekly loss within limit"],
            indicators=indicators,
        )


# ====================================================================
# ConsecutiveLossRule
# ====================================================================


class ConsecutiveLossRule:
    """연속해서 손실로 청산된 거래가 임계에 도달하면 신규 BUY 차단.

    봇이 연속 손실 중인 패턴은 시장 조건이 전략과 안 맞거나 stop-loss를
    운영자가 점검할 시점 — `cooldown` 설정. SELL/EXIT은 차단하지 않는다.

    `limit=0`이면 검사 비활성. soft 단계는 의도적으로 도입하지 않음 — 연속
    손실은 정성적 신호라 임계에 도달하면 명확히 멈추는 게 안전.
    """

    def __init__(self, limit: int):
        if limit < 0:
            raise ValueError("limit must be >= 0")
        self.limit = int(limit)

    def evaluate(self, *, consecutive_loss_count: int, order: OrderRequest) -> LossLimitResult:
        if self.limit <= 0:
            return LossLimitResult(
                decision=LossLimitDecision.ALLOW,
                indicators={"consecutive_loss_count": consecutive_loss_count, "limit": self.limit},
            )

        indicators = {
            "consecutive_loss_count": consecutive_loss_count,
            "consecutive_loss_limit": self.limit,
        }

        if consecutive_loss_count >= self.limit:
            reason = (
                f"consecutive losing trades {consecutive_loss_count} ≥ limit "
                f"{self.limit} — cooldown / 신규 BUY 차단 (#36 ConsecutiveLossRule)"
            )
            is_buy = order.side == OrderSide.BUY
            if is_buy:
                return LossLimitResult(
                    decision=LossLimitDecision.BLOCK_NEW_BUY,
                    reasons=[reason],
                    indicators=indicators,
                )
            return LossLimitResult(
                decision=LossLimitDecision.BLOCK_NEW_BUY,
                warnings=[f"{reason} (SELL/EXIT은 통과)"],
                indicators=indicators,
            )

        return LossLimitResult(
            decision=LossLimitDecision.ALLOW,
            passed=["consecutive losses within limit"],
            indicators=indicators,
        )


# ====================================================================
# Combined evaluation — RiskManager 호출 편의 함수
# ====================================================================


@dataclass
class LossLimitMerged:
    """3 rule 결과를 하나로 합친 묶음. RiskManager가 reasons/warnings/passed를
    자기 RiskCheckResult로 그대로 옮긴다.
    """
    block_buy:   bool
    passed:      list[str] = field(default_factory=list)
    warnings:    list[str] = field(default_factory=list)
    reasons:     list[str] = field(default_factory=list)
    daily:       LossLimitResult | None = None
    weekly:      LossLimitResult | None = None
    consecutive: LossLimitResult | None = None


def evaluate_loss_limits(
    *,
    order: OrderRequest,
    daily_rule:        DailyLossLimitRule | None,
    weekly_rule:       WeeklyLossLimitRule | None,
    consecutive_rule:  ConsecutiveLossRule | None,
    daily_pnl:         int = 0,
    weekly_pnl:        int = 0,
    consecutive_loss_count: int = 0,
) -> LossLimitMerged:
    """3 rule을 한 번에 평가해 합친 결과 반환.

    각 rule이 None이면 그 단계는 skip. 결과는 RiskManager가 그대로 merge —
    block_buy=True이면 신규 BUY는 REJECTED.
    """
    merged = LossLimitMerged(block_buy=False)
    if daily_rule is not None:
        d = daily_rule.evaluate(daily_pnl=daily_pnl, order=order)
        merged.daily = d
        merged.passed.extend(d.passed)
        merged.warnings.extend(d.warnings)
        merged.reasons.extend(d.reasons)
        if d.block_buy and order.side == OrderSide.BUY:
            merged.block_buy = True
    if weekly_rule is not None:
        w = weekly_rule.evaluate(weekly_pnl=weekly_pnl, order=order)
        merged.weekly = w
        merged.passed.extend(w.passed)
        merged.warnings.extend(w.warnings)
        merged.reasons.extend(w.reasons)
        if w.block_buy and order.side == OrderSide.BUY:
            merged.block_buy = True
    if consecutive_rule is not None:
        c = consecutive_rule.evaluate(
            consecutive_loss_count=consecutive_loss_count, order=order,
        )
        merged.consecutive = c
        merged.passed.extend(c.passed)
        merged.warnings.extend(c.warnings)
        merged.reasons.extend(c.reasons)
        if c.block_buy and order.side == OrderSide.BUY:
            merged.block_buy = True
    return merged


# ----------------------------------------------------------------------
# REDUCE_SIZE TODO
# ----------------------------------------------------------------------
#
# 현재 RiskCheckResult는 사이즈 축소를 직접 표현하지 않는다. REDUCE_SIZE는
# warnings로만 surface — PositionSizingAgent / 운영자가 명시 축소를 결정.
# 향후 RiskCheckResult에 normalized_order(축소된 사이즈)를 채우거나 별도
# RiskDecision.REDUCED 상태로 분기하는 옵트인 PR 가능. 본 PR에서는 정책만
# 명시 (#36 docs/loss_limit_policy.md 참고).
