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


# ============================================================================
# P-11: 종목별 최대 비중 제한 (Symbol Weight Limit) — Paper advisory layer
# ============================================================================
#
# 기존 #35 PositionLimitRule (위) 는 broker.OrderRequest/Balance/Position 과
# *결합* 된 RiskManager rule. P-11 은 *Paper Auto Loop* 가 BUY 후보 시점에
# 호출하는 *순수 함수* — broker 결합 0건, dict / int / float 만 입력.
# 두 layer 는 충돌 없이 공존 (#35 는 그대로 RiskManager 가 호출, P-11 은
# paper_decision_bridge / agent_consumer 등 advisory caller 가 호출).
#
# 사용자 요청서 §1 예시:
#   total=10,000,000 / max_pct=20% → 한 종목 최대 2,000,000원
#   현재 보유 1,600,000 / 신규 700,000 → 예상 2,300,000
#   → SYMBOL_WEIGHT_LIMIT_EXCEEDED 차단
#
# 호출 순서 (사용자 요청서 §4):
#   현재가 → P-08 sizing → P-06 → P-07 cash → P-10 daily buy →
#   *P-11 symbol weight (본 함수)* → RiskManager → PermissionGate.
#
# CLAUDE.md 절대 원칙 (테스트로 lock):
# - broker / OrderExecutor / route_order import 0건 (본 모듈 위쪽은 이미
#   broker import 있지만, 본 P-11 layer 는 *값만* 받으므로 결합 0건)
# - settings.enable_*_trading mutation 0건
# - SymbolWeightLimitResult.is_paper_only = True 영구
# - is_order_signal / is_live_authorization = False 영구


# ── 시스템 기본값 (사용자 요청서 §2) — P-09 미사용 시 fallback. ──
DEFAULT_MAX_SYMBOL_WEIGHT_PCT: float = 0.20    # 20%


_BUY_TOKENS_P11 = frozenset({
    "buy", "open", "open_long", "long", "enter", "entry",
})


def _is_buy_side_p11(side: str | None) -> bool:
    return (side or "").strip().lower() in _BUY_TOKENS_P11


def _format_krw_p11(amount: int | float) -> str:
    return f"{int(amount):,}"


# reason_code 상수.
SYMBOL_WEIGHT_LIMIT_OK                     = "SYMBOL_WEIGHT_LIMIT_OK"
SYMBOL_WEIGHT_LIMIT_EXCEEDED               = "SYMBOL_WEIGHT_LIMIT_EXCEEDED"
SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE         = "SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE"
SYMBOL_WEIGHT_LIMIT_INVALID_PRICE          = "INVALID_PRICE"
SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY       = "INVALID_QUANTITY"
SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY   = "INVALID_TOTAL_EQUITY"
SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT         = "INVALID_SYMBOL_WEIGHT_LIMIT"


@dataclass(frozen=True)
class SymbolWeightLimitResult:
    """종목별 최대 비중 검사 결과 — *advisory*, broker 호출 0건.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    allowed:                            bool
    reason_code:                        str
    reason_message:                     str
    total_paper_equity:                 int
    max_symbol_weight_pct:              float
    max_symbol_exposure_amount:         int
    current_symbol_exposure_amount:     int
    new_buy_notional:                   int
    projected_symbol_exposure_amount:   int
    remaining_symbol_buy_capacity:      int
    symbol:                             str | None = None
    side:                               str | None = None
    price:                              float | None = None
    quantity:                           int = 0

    is_paper_only:                      bool = True
    is_order_signal:                    bool = False
    is_live_authorization:              bool = False

    def __post_init__(self) -> None:
        if self.is_paper_only is not True:
            raise ValueError(
                "SymbolWeightLimitResult.is_paper_only must be True"
            )
        if self.is_order_signal is not False:
            raise ValueError(
                "SymbolWeightLimitResult.is_order_signal must be False"
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "SymbolWeightLimitResult.is_live_authorization must be False"
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
        return self.reason_code == SYMBOL_WEIGHT_LIMIT_EXCEEDED

    @property
    def is_not_applicable(self) -> bool:
        return self.reason_code == SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE

    def to_dict(self) -> dict:
        return {
            "allowed":                          self.allowed,
            "reason_code":                      self.reason_code,
            "reason_message":                   self.reason_message,
            "total_paper_equity":               int(self.total_paper_equity),
            "max_symbol_weight_pct":            float(self.max_symbol_weight_pct),
            "max_symbol_exposure_amount":       int(self.max_symbol_exposure_amount),
            "current_symbol_exposure_amount":   int(self.current_symbol_exposure_amount),
            "new_buy_notional":                 int(self.new_buy_notional),
            "projected_symbol_exposure_amount": int(self.projected_symbol_exposure_amount),
            "remaining_symbol_buy_capacity":    int(self.remaining_symbol_buy_capacity),
            "symbol":                           self.symbol,
            "side":                             self.side,
            "price":                            self.price,
            "quantity":                         int(self.quantity),
            "blocked":                          self.blocked,
            "is_exceeded":                      self.is_exceeded,
            "is_not_applicable":                self.is_not_applicable,
            "is_paper_only":                    self.is_paper_only,
            "is_order_signal":                  self.is_order_signal,
            "is_live_authorization":            self.is_live_authorization,
        }


def check_symbol_weight_limit(
    *,
    side:                            str | None,
    symbol:                          str | None,
    price:                           float | int | None,
    quantity:                        int | float,
    total_paper_equity:              int | float,
    current_symbol_exposure_amount:  int | float,
    max_symbol_weight_pct:           float,
) -> SymbolWeightLimitResult:
    """종목별 최대 비중 사전 검사 — *advisory*. 사용자 요청서 §3 정확 매칭.

    실행 순서 (reason_code 우선순위 — 첫 매칭 반환):
      1. SELL / HOLD / NO_ACTION             → NOT_APPLICABLE
      2. max_symbol_weight_pct <= 0 또는 > 1 → INVALID_WEIGHT
      3. total_paper_equity <= 0             → INVALID_TOTAL_EQUITY
      4. price <= 0 / None                   → INVALID_PRICE
      5. quantity 가 정수 ≥ 1 이 아님         → INVALID_QUANTITY
      6. current_symbol_exposure_amount < 0  → 0 으로 clamp + 진행
      7. projected > max_exposure            → SYMBOL_WEIGHT_LIMIT_EXCEEDED
      8. 그 외                                → SYMBOL_WEIGHT_LIMIT_OK

    계산:
        max_symbol_exposure_amount   = floor(total_paper_equity × pct)
        new_buy_notional             = floor(price × quantity)
        projected_symbol_exposure    = current + new_buy_notional
        remaining_symbol_buy_capacity = max(max_exposure - current, 0)

    Returns:
        SymbolWeightLimitResult — to_dict() 로 API 응답에 그대로 carry 가능.
    """
    total_eq = int(max(int(total_paper_equity or 0), 0))
    current_exposure_clamped = max(int(current_symbol_exposure_amount or 0), 0)

    # 1. NOT APPLICABLE — BUY 가 아니면 한도 검사 무관.
    if not _is_buy_side_p11(side):
        return SymbolWeightLimitResult(
            allowed=True,
            reason_code=SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE,
            reason_message=(
                "BUY 가 아니므로 종목별 비중 제한을 적용하지 않습니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=float(max_symbol_weight_pct or 0),
            max_symbol_exposure_amount=0,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=0,
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 2. max_symbol_weight_pct 검증.
    try:
        pct = float(max_symbol_weight_pct)
    except (TypeError, ValueError):
        pct = -1.0
    if not (0.0 < pct <= 1.0):
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT,
            reason_message=(
                f"종목별 최대 비중 {max_symbol_weight_pct!r} 이 (0, 1] 범위가 "
                "아니라 평가할 수 없습니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=float(max(pct, 0.0)),
            max_symbol_exposure_amount=0,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=0,
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 3. total_paper_equity 검증.
    if total_eq <= 0:
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY,
            reason_message=(
                f"총 Paper 자산 {total_paper_equity!r} 이 0 이하라 평가할 수 없습니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=pct,
            max_symbol_exposure_amount=0,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=0,
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 사전 계산 (사용자 요청서 §3 정확).
    import math
    max_exposure = int(math.floor(total_eq * pct))

    # 4. price 검증.
    if price is None:
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_INVALID_PRICE,
            reason_message="현재가가 없어 종목별 비중을 평가할 수 없습니다.",
            total_paper_equity=total_eq,
            max_symbol_weight_pct=pct,
            max_symbol_exposure_amount=max_exposure,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=max(max_exposure - current_exposure_clamped, 0),
            symbol=symbol, side=side, price=None, quantity=0,
        )
    try:
        p = float(price)
    except (TypeError, ValueError):
        p = -1.0
    if not (p > 0):
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_INVALID_PRICE,
            reason_message=(
                f"현재가 {price!r} 가 0 이하라 종목별 비중을 평가할 수 없습니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=pct,
            max_symbol_exposure_amount=max_exposure,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=max(max_exposure - current_exposure_clamped, 0),
            symbol=symbol, side=side, price=None, quantity=0,
        )

    # 5. quantity 검증.
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
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY,
            reason_message=(
                f"매수 수량 {quantity!r} 가 유효한 정수 ≥ 1 이 아니라 "
                "종목별 비중을 평가할 수 없습니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=pct,
            max_symbol_exposure_amount=max_exposure,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=0,
            projected_symbol_exposure_amount=current_exposure_clamped,
            remaining_symbol_buy_capacity=max(max_exposure - current_exposure_clamped, 0),
            symbol=symbol, side=side, price=p, quantity=0,
        )

    # 7. 본 계산 — 사용자 요청서 §3 정확.
    new_buy_notional = int(p * qty_int)
    projected = current_exposure_clamped + new_buy_notional
    remaining_before = max(max_exposure - current_exposure_clamped, 0)
    remaining_after = max(max_exposure - projected, 0)

    if projected > max_exposure:
        return SymbolWeightLimitResult(
            allowed=False,
            reason_code=SYMBOL_WEIGHT_LIMIT_EXCEEDED,
            reason_message=(
                "종목별 최대 비중을 초과하여 매수 차단 — 총자산 "
                f"{_format_krw_p11(total_eq)}원 기준 종목별 최대 비중 "
                f"{pct * 100:.1f}%는 {_format_krw_p11(max_exposure)}원입니다. "
                f"현재 보유 {_format_krw_p11(current_exposure_clamped)}원에 "
                f"신규 매수 {_format_krw_p11(new_buy_notional)}원을 더하면 "
                f"{_format_krw_p11(projected)}원으로 한도를 초과합니다."
            ),
            total_paper_equity=total_eq,
            max_symbol_weight_pct=pct,
            max_symbol_exposure_amount=max_exposure,
            current_symbol_exposure_amount=current_exposure_clamped,
            new_buy_notional=new_buy_notional,
            projected_symbol_exposure_amount=projected,
            remaining_symbol_buy_capacity=remaining_before,
            symbol=symbol, side=side, price=p, quantity=qty_int,
        )

    # 8. OK.
    return SymbolWeightLimitResult(
        allowed=True,
        reason_code=SYMBOL_WEIGHT_LIMIT_OK,
        reason_message=(
            f"종목별 최대 비중 이내 — 한도 {pct * 100:.1f}% / "
            f"{_format_krw_p11(max_exposure)}원, 현재 보유 "
            f"{_format_krw_p11(current_exposure_clamped)}원, 신규 매수 "
            f"{_format_krw_p11(new_buy_notional)}원, 예상 "
            f"{_format_krw_p11(projected)}원, 잔여 "
            f"{_format_krw_p11(remaining_after)}원."
        ),
        total_paper_equity=total_eq,
        max_symbol_weight_pct=pct,
        max_symbol_exposure_amount=max_exposure,
        current_symbol_exposure_amount=current_exposure_clamped,
        new_buy_notional=new_buy_notional,
        projected_symbol_exposure_amount=projected,
        remaining_symbol_buy_capacity=remaining_after,
        symbol=symbol, side=side, price=p, quantity=qty_int,
    )


# ── 현재 종목 노출 계산 helper (사용자 요청서 §5) ────────────────────────────


# 합산 대상 status — 확정 / 체결 / accepted 만. rejected / cancelled / blocked
# 제외 (사용자 요청서 §5 정책 + 검증 20).
_ACCEPTED_POSITION_STATUSES = frozenset({
    "FILLED", "ACCEPTED", "EXECUTED", "COMPLETED", "CONFIRMED",
    "filled", "accepted", "executed", "completed", "confirmed",
})
_REJECTED_POSITION_STATUSES = frozenset({
    "REJECTED", "CANCELLED", "CANCELED", "BLOCKED", "EXPIRED", "FAILED",
    "rejected", "cancelled", "canceled", "blocked", "expired", "failed",
})


def calculate_current_symbol_exposure_amount(
    orders,
    *,
    symbol: str,
    last_price: float | int | None = None,
) -> int:
    """현재 해당 symbol 의 *Paper 보유 평가금액* (KRW) — 확정 BUY 누적 - 확정 SELL 누적.

    각 order 의 필드:
        side / direction / action
        status / state
        notional / amount / notional_krw / total_amount
        price * quantity (fallback)
        symbol

    rejected / cancelled / blocked / pending / status 미상은 *현재 노출 계산에서
    제외* (사용자 요청서 §5 + 검증 20).

    Args:
        orders: iterable of dict / dataclass / Pydantic-like objects.
        symbol: 평가 대상 종목 코드.
        last_price: 최신가 — *현재 시점 평가금액* 으로 재계산할 수 있도록
            optional. None 이면 *체결가 누적* 기준.

    Returns:
        int — 확정 BUY notional - 확정 SELL notional (음수면 0 clamp).
    """
    if not orders or not symbol:
        return 0

    def _get(o, key, default=None):
        if isinstance(o, dict):
            return o.get(key, default)
        return getattr(o, key, default)

    def _side(o):
        return (
            _get(o, "side") or _get(o, "direction") or _get(o, "action")
            or _get(o, "trade_side")
        )

    def _status(o):
        return _get(o, "status") or _get(o, "state") or _get(o, "order_status")

    def _notional(o) -> int:
        # last_price 가 주어지면 quantity × last_price 로 *재평가* (현재 시점).
        if last_price is not None:
            try:
                q = float(_get(o, "quantity") or _get(o, "qty") or 0)
                lp = float(last_price)
                if q > 0 and lp > 0:
                    return int(q * lp)
            except (TypeError, ValueError):
                pass
        # 명시 notional 우선.
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

    sym = str(symbol).strip()
    buy_total = 0
    sell_total = 0
    for o in orders:
        if o is None:
            continue
        # symbol 매칭 (대소문자 strip 비교).
        osym = _get(o, "symbol")
        if not osym or str(osym).strip() != sym:
            continue
        status_raw = _status(o)
        if status_raw is None:
            continue
        if str(status_raw) in _REJECTED_POSITION_STATUSES:
            continue
        if str(status_raw) not in _ACCEPTED_POSITION_STATUSES:
            continue
        side_str = (_side(o) or "").strip().lower()
        notional = max(_notional(o), 0)
        if side_str in _BUY_TOKENS_P11:
            buy_total += notional
        elif side_str in {
            "sell", "close", "close_long", "exit", "sell_to_close",
        }:
            sell_total += notional
        # 그 외 (HOLD / NO_ACTION 등) — 포지션 변동 없음.
    return max(buy_total - sell_total, 0)


__all_p11__ = [
    "DEFAULT_MAX_SYMBOL_WEIGHT_PCT",
    "SYMBOL_WEIGHT_LIMIT_OK",
    "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
    "SYMBOL_WEIGHT_LIMIT_NOT_APPLICABLE",
    "SYMBOL_WEIGHT_LIMIT_INVALID_PRICE",
    "SYMBOL_WEIGHT_LIMIT_INVALID_QUANTITY",
    "SYMBOL_WEIGHT_LIMIT_INVALID_TOTAL_EQUITY",
    "SYMBOL_WEIGHT_LIMIT_INVALID_WEIGHT",
    "SymbolWeightLimitResult",
    "check_symbol_weight_limit",
    "calculate_current_symbol_exposure_amount",
]
