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


# ============================================================================
# P-10: 일일 최대 신규 매수금액 제한 (Daily Buy Limit)
# ============================================================================
#
# AI Paper / Paper 자동매매가 하루에 너무 많은 자금을 한 번에 투입하지 않도록,
# 일일 최대 신규 BUY 금액을 초과하면 BUY 를 차단하는 *pure check* 함수.
#
# 사용자 요청서 §1 예시:
#   max=3,000,000 / today_used=2,500,000 / new=700,000 → 합계 3,200,000
#   → DAILY_BUY_LIMIT_EXCEEDED 차단
#
# 본 layer 는 #36 DailyLossLimitRule (실현손익 한도) 와는 *완전히 별개* —
# 본 함수는 *오늘 신규 BUY notional 합계* 기준 사전 가드.
#
# 호출 순서 (사용자 요청서 §4):
#   현재가 → P-08 sizing → P-06 min-lot/high-price → P-07 cash check →
#   *P-10 (본 함수)* → RiskManager → PermissionGate → VirtualOrder.
#
# CLAUDE.md 절대 원칙 (테스트로 lock):
# - broker / OrderExecutor / route_order import 0건 (본 모듈은 이미 만족)
# - settings.enable_*_trading mutation 0건
# - DailyBuyLimitResult.is_paper_only = True 영구
# - is_order_signal / is_live_authorization = False 영구


# ── system 기본값 (사용자 요청서 §2) — P-09 미사용 시 fallback. ──
DEFAULT_DAILY_BUY_LIMIT_KRW: int = 3_000_000


_BUY_TOKENS = frozenset({
    "buy", "open", "open_long", "long", "enter", "entry",
})


def _is_buy_side(side: str | None) -> bool:
    return (side or "").strip().lower() in _BUY_TOKENS


def _format_krw(amount: int | float) -> str:
    return f"{int(amount):,}"


# 반환 reason_code 의 enum-like 상수. (StrEnum 으로 만들 수도 있지만 caller
# 가 단순 문자열 비교만 하므로 상수 그룹 유지.)
DAILY_BUY_LIMIT_OK              = "DAILY_BUY_LIMIT_OK"
DAILY_BUY_LIMIT_EXCEEDED        = "DAILY_BUY_LIMIT_EXCEEDED"
DAILY_BUY_LIMIT_NOT_APPLICABLE  = "DAILY_BUY_LIMIT_NOT_APPLICABLE"
DAILY_BUY_LIMIT_INVALID_INPUT   = "DAILY_BUY_LIMIT_INVALID_INPUT"


@dataclass(frozen=True)
class DailyBuyLimitResult:
    """일일 매수 한도 검사 결과 — *advisory*, broker 호출 0건.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    allowed:                       bool
    reason_code:                   str
    reason_message:                str
    max_daily_buy_amount:          int
    today_buy_used_amount:         int
    new_buy_notional:              int
    projected_today_buy_amount:    int
    remaining_daily_buy_amount:    int
    symbol:                        str | None = None
    side:                          str | None = None
    price:                         float | None = None
    quantity:                      int = 0

    is_paper_only:                 bool = True
    is_order_signal:               bool = False
    is_live_authorization:         bool = False

    def __post_init__(self) -> None:
        if self.is_paper_only is not True:
            raise ValueError("DailyBuyLimitResult.is_paper_only must be True")
        if self.is_order_signal is not False:
            raise ValueError(
                "DailyBuyLimitResult.is_order_signal must be False"
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "DailyBuyLimitResult.is_live_authorization must be False"
            )
        if self.new_buy_notional < 0:
            raise ValueError(
                f"new_buy_notional must be >= 0, got {self.new_buy_notional}"
            )

    @property
    def blocked(self) -> bool:
        return not self.allowed

    @property
    def is_exceeded(self) -> bool:
        return self.reason_code == DAILY_BUY_LIMIT_EXCEEDED

    @property
    def is_not_applicable(self) -> bool:
        return self.reason_code == DAILY_BUY_LIMIT_NOT_APPLICABLE

    def to_dict(self) -> dict:
        return {
            "allowed":                    self.allowed,
            "reason_code":                self.reason_code,
            "reason_message":             self.reason_message,
            "max_daily_buy_amount":       int(self.max_daily_buy_amount),
            "today_buy_used_amount":      int(self.today_buy_used_amount),
            "new_buy_notional":           int(self.new_buy_notional),
            "projected_today_buy_amount": int(self.projected_today_buy_amount),
            "remaining_daily_buy_amount": int(self.remaining_daily_buy_amount),
            "symbol":                     self.symbol,
            "side":                       self.side,
            "price":                      self.price,
            "quantity":                   int(self.quantity),
            "blocked":                    self.blocked,
            "is_exceeded":                self.is_exceeded,
            "is_not_applicable":          self.is_not_applicable,
            "is_paper_only":              self.is_paper_only,
            "is_order_signal":            self.is_order_signal,
            "is_live_authorization":      self.is_live_authorization,
        }


def check_daily_buy_limit(
    *,
    side:                   str | None,
    price:                  float | int | None,
    quantity:               int | float,
    today_buy_used_amount:  int | float,
    max_daily_buy_amount:   int | float,
    symbol:                 str | None = None,
) -> DailyBuyLimitResult:
    """일일 매수 한도 사전 검사 — *advisory*. 사용자 요청서 §3 정확 매칭.

    실행 순서 (reason_code 우선순위 — 첫 매칭 반환):
      1. SELL / HOLD / NO_ACTION → DAILY_BUY_LIMIT_NOT_APPLICABLE
      2. price <= 0 / None       → DAILY_BUY_LIMIT_INVALID_INPUT (price)
      3. quantity <= 0           → DAILY_BUY_LIMIT_INVALID_INPUT (quantity)
      4. max_daily_buy <= 0      → DAILY_BUY_LIMIT_INVALID_INPUT (max)
      5. today_used < 0          → 0 으로 clamp + 진행
      6. projected > max         → DAILY_BUY_LIMIT_EXCEEDED  (allowed=False)
      7. 그 외                    → DAILY_BUY_LIMIT_OK         (allowed=True)

    Args:
        side: 매매 의도. BUY 만 검사. 그 외 → NOT_APPLICABLE.
        price: 1주 가격 (KRW).
        quantity: 요청 수량 (정수 ≥ 1 권장).
        today_buy_used_amount: 오늘 확정된 신규 BUY 누적 사용금액 (KRW).
        max_daily_buy_amount: 일일 최대 신규 매수금액 (KRW).
        symbol: 후보 종목 코드 (선택).

    Returns:
        DailyBuyLimitResult — to_dict() 로 API 응답에 그대로 carry 가능.
    """
    # 1. NOT APPLICABLE — BUY 가 아니면 한도 검사 무관.
    if not _is_buy_side(side):
        return DailyBuyLimitResult(
            allowed=True,
            reason_code=DAILY_BUY_LIMIT_NOT_APPLICABLE,
            reason_message=(
                "BUY 가 아니므로 일일 매수금액 제한을 적용하지 않습니다."
            ),
            max_daily_buy_amount=int(max(int(max_daily_buy_amount or 0), 0)),
            today_buy_used_amount=int(max(int(today_buy_used_amount or 0), 0)),
            new_buy_notional=0,
            projected_today_buy_amount=int(max(int(today_buy_used_amount or 0), 0)),
            remaining_daily_buy_amount=int(max(
                int(max_daily_buy_amount or 0) - int(today_buy_used_amount or 0), 0,
            )),
            symbol=symbol, side=side, price=None, quantity=0,
        )

    today_used = max(int(today_buy_used_amount or 0), 0)
    max_amount = int(max_daily_buy_amount or 0)

    # 4. max_daily_buy_amount 안전 가드 — 0 이하는 *모든 BUY 차단* 으로 해석.
    if max_amount <= 0:
        return DailyBuyLimitResult(
            allowed=False,
            reason_code=DAILY_BUY_LIMIT_INVALID_INPUT,
            reason_message=(
                f"일일 최대 매수금액이 0 이하 ({max_amount}) — BUY 를 평가할 수 없습니다."
            ),
            max_daily_buy_amount=max(max_amount, 0),
            today_buy_used_amount=today_used,
            new_buy_notional=0,
            projected_today_buy_amount=today_used,
            remaining_daily_buy_amount=0,
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 2. price 검증.
    if price is None:
        return DailyBuyLimitResult(
            allowed=False,
            reason_code=DAILY_BUY_LIMIT_INVALID_INPUT,
            reason_message="현재가가 없어 일일 매수금액을 평가할 수 없습니다.",
            max_daily_buy_amount=max_amount,
            today_buy_used_amount=today_used,
            new_buy_notional=0,
            projected_today_buy_amount=today_used,
            remaining_daily_buy_amount=max(max_amount - today_used, 0),
            symbol=symbol, side=side, price=None, quantity=0,
        )
    try:
        p = float(price)
    except (TypeError, ValueError):
        p = -1.0
    if not (p > 0):
        return DailyBuyLimitResult(
            allowed=False,
            reason_code=DAILY_BUY_LIMIT_INVALID_INPUT,
            reason_message=(
                f"현재가 {price!r} 가 0 이하라 일일 매수금액을 평가할 수 없습니다."
            ),
            max_daily_buy_amount=max_amount,
            today_buy_used_amount=today_used,
            new_buy_notional=0,
            projected_today_buy_amount=today_used,
            remaining_daily_buy_amount=max(max_amount - today_used, 0),
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 3. quantity 검증 — 정수 ≥ 1.
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        qty_int = -1
    else:
        try:
            qf = float(quantity)
        except (TypeError, ValueError):
            qf = -1.0
        if qf != qf or qf in (float("inf"), float("-inf")) or qf < 1:
            qty_int = -1
        elif float(qf).is_integer():
            qty_int = int(qf)
        else:
            qty_int = -1
    if qty_int < 1:
        return DailyBuyLimitResult(
            allowed=False,
            reason_code=DAILY_BUY_LIMIT_INVALID_INPUT,
            reason_message=(
                f"매수 수량 {quantity!r} 가 유효한 정수 ≥ 1 이 아니라 "
                "일일 매수금액을 평가할 수 없습니다."
            ),
            max_daily_buy_amount=max_amount,
            today_buy_used_amount=today_used,
            new_buy_notional=0,
            projected_today_buy_amount=today_used,
            remaining_daily_buy_amount=max(max_amount - today_used, 0),
            symbol=symbol, side=side, price=p, quantity=0,
        )

    # 6. 본 계산 — 사용자 요청서 §3 정확 매칭.
    new_buy_notional = int(p * qty_int)
    projected = today_used + new_buy_notional
    remaining_before = max(max_amount - today_used, 0)

    if projected > max_amount:
        return DailyBuyLimitResult(
            allowed=False,
            reason_code=DAILY_BUY_LIMIT_EXCEEDED,
            reason_message=(
                "일일 최대 매수금액을 초과하여 매수 차단 — 일일 최대 매수금액 "
                f"{_format_krw(max_amount)}원 중 이미 "
                f"{_format_krw(today_used)}원을 사용했고, 신규 매수금액 "
                f"{_format_krw(new_buy_notional)}원을 더하면 한도를 초과합니다."
            ),
            max_daily_buy_amount=max_amount,
            today_buy_used_amount=today_used,
            new_buy_notional=new_buy_notional,
            projected_today_buy_amount=projected,
            remaining_daily_buy_amount=remaining_before,
            symbol=symbol, side=side, price=p, quantity=qty_int,
        )

    # 7. OK.
    return DailyBuyLimitResult(
        allowed=True,
        reason_code=DAILY_BUY_LIMIT_OK,
        reason_message=(
            f"일일 매수 한도 이내 — 한도 {_format_krw(max_amount)}원 / "
            f"오늘 사용 {_format_krw(today_used)}원 / 신규 "
            f"{_format_krw(new_buy_notional)}원 / 잔여 "
            f"{_format_krw(max(max_amount - projected, 0))}원."
        ),
        max_daily_buy_amount=max_amount,
        today_buy_used_amount=today_used,
        new_buy_notional=new_buy_notional,
        projected_today_buy_amount=projected,
        remaining_daily_buy_amount=max(max_amount - projected, 0),
        symbol=symbol, side=side, price=p, quantity=qty_int,
    )


# ── 오늘 신규 BUY 사용금액 계산 helper ───────────────────────────────────────


# 합산 대상 status — 확정 / 체결 / accepted 만. rejected / cancelled / blocked
# / pending 은 제외.
_ACCEPTED_BUY_STATUSES = frozenset({
    "FILLED", "ACCEPTED", "EXECUTED", "COMPLETED", "CONFIRMED",
    "filled", "accepted", "executed", "completed", "confirmed",
})
_REJECTED_BUY_STATUSES = frozenset({
    "REJECTED", "CANCELLED", "CANCELED", "BLOCKED", "EXPIRED", "FAILED",
    "rejected", "cancelled", "canceled", "blocked", "expired", "failed",
})


def _kst_today_iso(now=None) -> str:
    """KST 기준 오늘 날짜 (YYYY-MM-DD). 테스트 주입용 `now` 옵션."""
    from datetime import datetime, timedelta, timezone
    if now is None:
        now = datetime.now(timezone.utc)
    kst = now.astimezone(timezone(timedelta(hours=9)))
    return kst.strftime("%Y-%m-%d")


def calculate_today_buy_used_amount(
    orders,
    *,
    today_kst: str | None = None,
    now=None,
) -> int:
    """오늘 (KST) 확정된 신규 BUY 의 누적 사용금액 (KRW).

    `orders` 는 dict-like sequence — 각 entry 가 다음 필드 *중 하나라도* 있으면
    사용:
        side / direction / action       — BUY 여부
        status / state                  — 확정 (FILLED/ACCEPTED/EXECUTED/...) /
                                          거부 (REJECTED/CANCELLED/BLOCKED/...)
        notional / amount /
        notional_krw / total_amount     — KRW 금액 (있으면 그대로)
        price * quantity                — fallback 계산
        created_at / order_date /
        date / kst_date / executed_at   — KST 날짜 비교 (YYYY-MM-DD)

    위 필드 중 *합산 가능* 한 BUY 만 더한다. rejected / cancelled / blocked /
    pending / unknown status 는 제외.

    Args:
        orders: 주문 dict / dataclass / Pydantic 같은 *iterable* 객체.
        today_kst: 비교 기준 날짜 (YYYY-MM-DD). None 이면 `now` 또는 시스템 KST.
        now: 테스트용 datetime 주입.

    Returns:
        int — 합산 KRW. orders 가 None / 빈 컬렉션이면 0.
    """
    if not orders:
        return 0
    today = today_kst or _kst_today_iso(now=now)

    def _date_of(o) -> str | None:
        for key in ("kst_date", "order_date", "date",
                    "created_at", "executed_at", "filled_at"):
            v = _get(o, key)
            if v:
                # ISO 형태 — 앞 10자 (YYYY-MM-DD) 만 비교. KST timezone 변환은
                # caller (storage) 가 이미 한 것으로 가정.
                if isinstance(v, str) and len(v) >= 10:
                    return v[:10]
        return None

    def _get(o, key, default=None):
        if isinstance(o, dict):
            return o.get(key, default)
        return getattr(o, key, default)

    def _side(o) -> str | None:
        return (
            _get(o, "side") or _get(o, "direction") or _get(o, "action")
            or _get(o, "trade_side")
        )

    def _status(o) -> str | None:
        return (
            _get(o, "status") or _get(o, "state") or _get(o, "order_status")
        )

    def _notional(o) -> int:
        # 명시 notional 필드 우선.
        for key in ("notional_krw", "notional", "amount",
                    "total_amount", "filled_amount"):
            v = _get(o, key)
            if v is not None:
                try:
                    return int(float(v))
                except (TypeError, ValueError):
                    pass
        # price × quantity fallback.
        try:
            p = float(_get(o, "price") or 0)
            q = float(_get(o, "quantity") or _get(o, "qty") or 0)
        except (TypeError, ValueError):
            return 0
        if p > 0 and q > 0:
            return int(p * q)
        return 0

    total = 0
    for o in orders:
        if o is None:
            continue
        if not _is_buy_side(_side(o)):
            continue
        status_raw = _status(o)
        if status_raw is None:
            # status 미상 — 보수적으로 *제외* (사용자 요청서 §5: 확정된 주문만).
            continue
        if str(status_raw) in _REJECTED_BUY_STATUSES:
            continue
        if str(status_raw) not in _ACCEPTED_BUY_STATUSES:
            # pending / 알 수 없는 status — 제외.
            continue
        # 날짜 비교 — 오늘만.
        ds = _date_of(o)
        if ds != today:
            continue
        total += max(_notional(o), 0)
    return total


__all_p10__ = [
    "DEFAULT_DAILY_BUY_LIMIT_KRW",
    "DAILY_BUY_LIMIT_OK",
    "DAILY_BUY_LIMIT_EXCEEDED",
    "DAILY_BUY_LIMIT_NOT_APPLICABLE",
    "DAILY_BUY_LIMIT_INVALID_INPUT",
    "DailyBuyLimitResult",
    "check_daily_buy_limit",
    "calculate_today_buy_used_amount",
]
