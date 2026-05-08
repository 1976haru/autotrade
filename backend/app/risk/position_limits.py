"""Position Limit Rule (#35).

1회 거래금액 / 자본 대비 1회 주문 비율 / 종목당 노출 / 자본 대비 종목당 노출 /
총 노출 / 자본 대비 총 노출 / 최대 보유 종목 수를 한 곳에 모아둔 정책 객체.

본 모듈이 RiskManager의 `evaluate_order`가 사용해 온 inline 검사 로직을
대체한다 — RiskManager는 본 객체에 위임하여 단일 진실(single source of truth).
기존 reason / passed 문자열은 그대로 보존 (backwards compat).

선물(FuturesRiskPolicy)은 별도 — 계약 수, 명목금액, 레버리지, margin 기준이
다르다. `FuturesPositionLimitRule`은 향후 별도 모듈로 분리 (TODO 주석 참고).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.brokers.base import Balance, OrderRequest, OrderSide, Position


@dataclass(frozen=True)
class PositionLimitInput:
    """check 메서드의 입력 컨텍스트.

    필드:
    - order: 평가 대상 주문
    - balance: 현재 자본 (cash/equity/buying_power)
    - positions: 현재 보유 포지션 (broker 스냅샷)
    - latest_price: 주문 평가 시점 가격
    """
    order:        OrderRequest
    balance:      Balance
    positions:    list[Position]
    latest_price: int


@dataclass(frozen=True)
class PositionLimitPolicy:
    """PositionLimitRule이 사용하는 한도 묶음.

    `RiskPolicy`의 일부 필드와 1:1 매핑 — RiskManager는 본 객체를 어댑터로
    감싸서 rule에 넘긴다. 0 또는 빈 값은 검사 비활성.
    """
    max_order_notional:      int   = 0
    max_position_size_pct:   float = 0.0
    max_positions:           int   = 0
    max_symbol_exposure:     int   = 0
    max_symbol_exposure_pct: float = 0.0
    max_total_exposure:      int   = 0
    max_total_exposure_pct:  float = 0.0


@dataclass(frozen=True)
class PositionLimitPreview:
    """주문 평가 시점 노출 / 한도 / 잔여 capacity 스냅샷.

    UI / Agent / API preview endpoint에 그대로 노출 — 운영자가 "이 주문이
    들어가면 노출이 어떻게 변하나" 즉시 인지.
    """
    order_notional:            int
    current_symbol_exposure:   int
    projected_symbol_exposure: int
    current_total_exposure:    int
    projected_total_exposure:  int
    current_position_count:    int
    projected_position_count:  int
    will_open_new_position:    bool
    # 한도 미설정(=0)이면 None — 무한대 의미.
    remaining_symbol_capacity:     int | None
    remaining_total_capacity:      int | None
    remaining_position_slots:      int | None

    def to_dict(self) -> dict:
        return {
            "order_notional":              self.order_notional,
            "current_symbol_exposure":     self.current_symbol_exposure,
            "projected_symbol_exposure":   self.projected_symbol_exposure,
            "current_total_exposure":      self.current_total_exposure,
            "projected_total_exposure":    self.projected_total_exposure,
            "current_position_count":      self.current_position_count,
            "projected_position_count":    self.projected_position_count,
            "will_open_new_position":      self.will_open_new_position,
            "remaining_symbol_capacity":   self.remaining_symbol_capacity,
            "remaining_total_capacity":    self.remaining_total_capacity,
            "remaining_position_slots":    self.remaining_position_slots,
        }


@dataclass
class PositionLimitResult:
    """check() 결과 — passed/reasons + 노출 미리보기.

    `allowed`는 reasons가 비어있으면 True. RiskManager는 본 결과의 passed/
    reasons를 자신의 RiskCheckResult에 그대로 merge한다.
    """
    passed:  list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    preview: PositionLimitPreview | None = None

    @property
    def allowed(self) -> bool:
        return not self.reasons

    def to_dict(self) -> dict:
        return {
            "allowed":  self.allowed,
            "passed":   list(self.passed),
            "reasons":  list(self.reasons),
            "preview":  self.preview.to_dict() if self.preview else None,
        }


# ====================================================================
# Rule
# ====================================================================


class PositionLimitRule:
    """주문 한도 + 종목 / 총 노출 + 보유 종목 수 검사.

    각 check_* 메서드는 (passed, reasons) 튜플을 반환 — RiskManager가 자기
    결과 객체에 그대로 누적 가능. `check()`는 모든 검사를 한 번에 돌려
    `PositionLimitResult`를 반환 (preview 포함).

    설계 결정:
    - **Single source of truth**: RiskManager.evaluate_order이 본 메서드들을
      호출해 inline 로직 중복을 제거. 기존 reason/passed 문자열은 그대로
      유지 (backwards compat).
    - **0 = 검사 비활성**: 모든 한도 필드가 0이면 자유롭게 통과 (기존 default).
    - **side-aware**: BUY는 노출 증가 검사. SELL/청산은 보유 가능성 검사가
      아니라 노출 *축소* 의도이므로 max_positions / total_exposure /
      symbol_exposure_pct는 우회 (기존 동작).
    - 단, `max_order_notional`/`max_position_size_pct`/`max_symbol_exposure`
      는 양방향 검사 (기존 동작 — symbol_exposure는 SELL에도 passed 추가).

    Futures(FuturesRiskPolicy)는 별도 — 계약 수, margin, 레버리지 기준이
    다르므로 본 rule을 사용하지 않는다. TODO: `FuturesPositionLimitRule`은
    `app/risk/futures_position_limits.py` 또는 `app/futures/position_limits.py`
    에 별도 구현 (#35 시점에는 미구현, 선물 LIVE 비활성).
    """

    def __init__(self, policy: PositionLimitPolicy):
        self.policy = policy

    # ---------- preview ----------

    def build_preview(self, inp: PositionLimitInput) -> PositionLimitPreview:
        order = inp.order
        order_notional = inp.latest_price * order.quantity

        symbol_pos = next(
            (p for p in inp.positions if p.symbol == order.symbol), None,
        )
        current_symbol_exposure = (
            symbol_pos.quantity * symbol_pos.market_price if symbol_pos else 0
        )
        current_total_exposure = sum(p.quantity * p.market_price for p in inp.positions)
        current_symbols = {p.symbol for p in inp.positions if p.quantity > 0}
        current_count   = len(current_symbols)

        if order.side == OrderSide.BUY:
            projected_symbol = current_symbol_exposure + order_notional
            projected_total  = current_total_exposure + order_notional
            will_open_new = order.symbol not in current_symbols
            projected_count = current_count + (1 if will_open_new else 0)
        else:
            # SELL: 노출 축소. 가격 변동 없이 보유 가치만큼 차감 (음수면 0 floor).
            sell_notional = order_notional
            projected_symbol = max(0, current_symbol_exposure - sell_notional)
            projected_total  = max(0, current_total_exposure - sell_notional)
            will_open_new = False
            # SELL이 포지션을 0으로 만들면 종목 수 -1, 아니면 동일.
            if symbol_pos and symbol_pos.quantity <= order.quantity:
                projected_count = max(0, current_count - 1)
            else:
                projected_count = current_count

        # remaining capacity — 한도 미설정(=0)이면 None.
        sym_remaining: int | None = None
        if self.policy.max_symbol_exposure > 0:
            sym_remaining = max(0, self.policy.max_symbol_exposure - projected_symbol)
        tot_remaining: int | None = None
        if self.policy.max_total_exposure > 0:
            tot_remaining = max(0, self.policy.max_total_exposure - projected_total)
        slots_remaining: int | None = None
        if self.policy.max_positions > 0:
            slots_remaining = max(0, self.policy.max_positions - projected_count)

        return PositionLimitPreview(
            order_notional=order_notional,
            current_symbol_exposure=current_symbol_exposure,
            projected_symbol_exposure=projected_symbol,
            current_total_exposure=current_total_exposure,
            projected_total_exposure=projected_total,
            current_position_count=current_count,
            projected_position_count=projected_count,
            will_open_new_position=will_open_new,
            remaining_symbol_capacity=sym_remaining,
            remaining_total_capacity=tot_remaining,
            remaining_position_slots=slots_remaining,
        )

    # ---------- 개별 검사 (RiskManager가 inline 호출) ----------

    def check_order_notional(self, inp: PositionLimitInput) -> tuple[list[str], list[str]]:
        """1회 주문 명목금액 한도. 양방향 검사.

        반환: (passed, reasons). 비활성(=0)일 땐 ⚠ *passed에는 추가됨* —
        기존 evaluate_order 동작 보존 (max_order_notional은 default 1_000_000
        으로 항상 활성).
        """
        order_notional = inp.latest_price * inp.order.quantity
        if (self.policy.max_order_notional > 0
                and order_notional > self.policy.max_order_notional):
            return [], ["order notional exceeds max_order_notional"]
        return ["order notional within limit"], []

    def check_equity_relative_order_size(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """자본 대비 1회 주문 비율 한도. pct=0이면 검사 우회 (passed/reasons 모두 빈 list)."""
        pct = self.policy.max_position_size_pct
        if pct <= 0:
            return [], []
        order_notional = inp.latest_price * inp.order.quantity
        cap = inp.balance.equity * pct / 100.0
        if order_notional > cap:
            return [], [
                f"order notional {order_notional} exceeds {pct}% of equity ({cap:.0f})"
            ]
        return ["order notional within equity-relative cap"], []

    def check_max_positions(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """최대 보유 종목 수. BUY + 신규 종목일 때만 위반 가능, 그 외는 passed."""
        order = inp.order
        current_symbols = {p.symbol for p in inp.positions if p.quantity > 0}
        if (order.side == OrderSide.BUY
                and order.symbol not in current_symbols
                and len(current_symbols) >= self.policy.max_positions):
            return [], ["max positions reached"]
        return ["position count within limit"], []

    def check_symbol_exposure(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """종목별 노출 절대값 한도. BUY + 한도 초과시 위반."""
        order = inp.order
        symbol_pos = next(
            (p for p in inp.positions if p.symbol == order.symbol), None,
        )
        current_exposure = (
            symbol_pos.quantity * symbol_pos.market_price if symbol_pos else 0
        )
        order_notional = inp.latest_price * order.quantity
        if (order.side == OrderSide.BUY
                and current_exposure + order_notional > self.policy.max_symbol_exposure):
            return [], ["symbol exposure limit exceeded"]
        return ["symbol exposure within limit"], []

    def check_symbol_exposure_pct(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """종목별 노출 자본 대비 % 한도. BUY + pct>0일 때만 검사."""
        order = inp.order
        sym_pct = self.policy.max_symbol_exposure_pct
        if order.side != OrderSide.BUY or sym_pct <= 0:
            return [], []
        symbol_pos = next(
            (p for p in inp.positions if p.symbol == order.symbol), None,
        )
        current_exposure = (
            symbol_pos.quantity * symbol_pos.market_price if symbol_pos else 0
        )
        order_notional = inp.latest_price * order.quantity
        cap = inp.balance.equity * sym_pct / 100.0
        new_sym_exposure = current_exposure + order_notional
        if new_sym_exposure > cap:
            return [], [
                f"symbol exposure {new_sym_exposure} exceeds {sym_pct}% of "
                f"equity ({cap:.0f}) for {order.symbol}"
            ]
        return ["symbol exposure within equity-relative limit"], []

    def check_total_exposure(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """총 노출 절대값 한도. BUY + max_total_exposure>0일 때만 검사."""
        order = inp.order
        if order.side != OrderSide.BUY or self.policy.max_total_exposure <= 0:
            return [], []
        order_notional = inp.latest_price * order.quantity
        current_total = sum(p.quantity * p.market_price for p in inp.positions)
        new_total = current_total + order_notional
        if new_total > self.policy.max_total_exposure:
            return [], [
                f"total exposure {new_total} exceeds max_total_exposure "
                f"{self.policy.max_total_exposure}"
            ]
        return ["total exposure within absolute limit"], []

    def check_total_exposure_pct(
        self, inp: PositionLimitInput,
    ) -> tuple[list[str], list[str]]:
        """총 노출 자본 대비 % 한도. BUY + pct>0일 때만 검사."""
        order = inp.order
        tot_pct = self.policy.max_total_exposure_pct
        if order.side != OrderSide.BUY or tot_pct <= 0:
            return [], []
        order_notional = inp.latest_price * order.quantity
        current_total = sum(p.quantity * p.market_price for p in inp.positions)
        new_total = current_total + order_notional
        cap = inp.balance.equity * tot_pct / 100.0
        if new_total > cap:
            return [], [
                f"total exposure {new_total} exceeds {tot_pct}% of "
                f"equity ({cap:.0f})"
            ]
        return ["total exposure within equity-relative limit"], []

    # ---------- 통합 ----------

    def check(self, inp: PositionLimitInput) -> PositionLimitResult:
        """모든 한도를 한 번에 검사. 결과는 RiskManager의 evaluate_order에서
        쓰는 순서와 동일하게 누적된다 — 현재 reasons/passed 순서 호환.
        """
        passed: list[str] = []
        reasons: list[str] = []
        for fn in (
            self.check_order_notional,
            self.check_equity_relative_order_size,
            self.check_max_positions,
            self.check_symbol_exposure,
            self.check_symbol_exposure_pct,
            self.check_total_exposure,
            self.check_total_exposure_pct,
        ):
            p, r = fn(inp)
            passed.extend(p)
            reasons.extend(r)
        return PositionLimitResult(
            passed=passed,
            reasons=reasons,
            preview=self.build_preview(inp),
        )


def policy_from_risk_policy(risk_policy) -> PositionLimitPolicy:
    """`RiskPolicy` 인스턴스를 본 모듈의 PositionLimitPolicy로 어댑터.

    RiskPolicy import 순환을 피하기 위해 *duck typing*. 호출자(RiskManager)가
    risk_policy를 그대로 넘긴다.
    """
    return PositionLimitPolicy(
        max_order_notional      = getattr(risk_policy, "max_order_notional", 0),
        max_position_size_pct   = getattr(risk_policy, "max_position_size_pct", 0.0),
        max_positions           = getattr(risk_policy, "max_positions", 0),
        max_symbol_exposure     = getattr(risk_policy, "max_symbol_exposure", 0),
        max_symbol_exposure_pct = getattr(risk_policy, "max_symbol_exposure_pct", 0.0),
        max_total_exposure      = getattr(risk_policy, "max_total_exposure", 0),
        max_total_exposure_pct  = getattr(risk_policy, "max_total_exposure_pct", 0.0),
    )


# ----------------------------------------------------------------------
# Futures placeholder
# ----------------------------------------------------------------------
#
# 선물 한도는 본 rule에 포함하지 않는다.
# - 현물(주식): 명목금액 = price × quantity. 본 rule이 담당.
# - 선물:  명목금액 = price × multiplier × quantity, 거기에 margin /
#   leverage / 계약 수 한도가 별도 적용.
# `app/futures/risk.py`의 `FuturesRiskPolicy`가 max_contracts /
# max_margin_used / max_leverage를 강제. `FuturesPositionLimitRule`로
# 분리는 향후 옵트인 PR (FUTURES_LIVE 비활성 상태에서는 본 rule 도입이
# 우선순위 낮음).
# 자세한 정책: docs/position_limit_policy.md §5.
