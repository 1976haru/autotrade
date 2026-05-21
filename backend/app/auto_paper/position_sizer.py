"""#4-08: Paper position sizing — Risk cap 기반 가상 수량 계산.

AI Paper 판단이 BUY / SELL / EXIT 를 만들 때 *가상 수량* 을 고정 1주 가 아니라
*위험 한도 기반* 으로 계산. 결정론적 휴리스틱:

```
position_risk_krw   = account_equity * max_risk_per_trade_pct (default 1%)
base_quantity_raw   = position_risk_krw / (price * stop_loss_pct)
size_multiplier     = confidence_factor × risk_flag_factor × regime_factor
final_quantity      = base_quantity_raw × size_multiplier
final_quantity     = clamp(final_quantity, 0, max_quantity_from_cap)
```

## 절대 invariant (CLAUDE.md 절대 원칙 1~5 상속)

1. **본 모듈은 *결정론적 수량 계산기*** — broker / OrderExecutor / route_order
   호출 0건.
2. `SizingResult.is_order_signal=False` / `auto_apply_allowed=False` /
   `is_live_authorization=False` 불변.
3. 외부 HTTP / AI SDK / LLM import 0건.
4. DB write 0건.
5. **EMERGENCY_STOP / UNKNOWN regime → 수량 0** (영구 lock).
6. 음수 / NaN / Inf 입력 → 안전한 0 으로 fallback (silent failure 0건 — 사유 carry).

## Cap 우선순위

```
1. EMERGENCY_STOP / UNKNOWN → quantity=0 (영구)
2. confidence ≤ min_confidence_threshold → quantity=0 (HOLD 신호)
3. risk_flags 수 ≥ max_risk_flags → quantity=0 (HOLD 신호)
4. price ≤ 0 또는 account_equity ≤ 0 → quantity=0
5. risk-based 계산 → clamp(0, max_quantity_from_pct_cap)
6. 최종 클램프 → clamp(0, max_quantity_from_krw_cap)
```

각 단계 카운트 + 차단 사유는 `SizingResult.reasons` 에 carry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


SIZER_SCHEMA_VERSION = "1.0"


# 결정론적 multiplier — 후속 PR 에서 운영자 조정 가능.
_DEFAULT_REGIME_MULTIPLIER: dict[str, float] = {
    "TREND_UP":         1.0,
    "TREND_DOWN":       0.5,    # 하락 추세에서 신규 진입 축소
    "SIDEWAYS":         0.8,
    "HIGH_VOLATILITY":  0.5,    # 변동성 급증 시 size 축소
    "LOW_LIQUIDITY":    0.3,    # 거래대금 부족 시 추가 축소
    "CHOPPY":           0.5,
    "UNKNOWN":          0.0,    # 장세 분류 불가 시 수량 0
}


class SizingVerdict(StrEnum):
    """결정론적 sizing 결과 분류 — *주문 방향 0개*."""
    SIZED         = "SIZED"          # 정상 수량 산정
    REDUCED       = "REDUCED"         # confidence/risk_flags/regime 으로 축소
    BLOCKED_EMERGENCY = "BLOCKED_EMERGENCY"   # EMERGENCY_STOP
    BLOCKED_UNKNOWN   = "BLOCKED_UNKNOWN"     # UNKNOWN regime
    BLOCKED_LOW_CONFIDENCE = "BLOCKED_LOW_CONFIDENCE"
    BLOCKED_RISK_FLAGS = "BLOCKED_RISK_FLAGS"
    INSUFFICIENT_DATA  = "INSUFFICIENT_DATA"   # price/equity 0/음수


_VERDICT_LABEL_KO: dict[SizingVerdict, str] = {
    SizingVerdict.SIZED:                     "정상 수량 산정",
    SizingVerdict.REDUCED:                   "축소 적용 (confidence / 위험 / 장세)",
    SizingVerdict.BLOCKED_EMERGENCY:         "EMERGENCY_STOP — 수량 0",
    SizingVerdict.BLOCKED_UNKNOWN:           "UNKNOWN 장세 — 수량 0",
    SizingVerdict.BLOCKED_LOW_CONFIDENCE:    "confidence 임계 미달 — 수량 0",
    SizingVerdict.BLOCKED_RISK_FLAGS:        "위험 신호 다수 — 수량 0",
    SizingVerdict.INSUFFICIENT_DATA:         "데이터 부족 (price / equity) — 수량 0",
}


# ─────────────────────────────────────────────────────────────────────────────
# Policy + Input + Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PositionSizingPolicy:
    """운영자가 CLI / API 로 조정 가능한 임계값 집합 — *advisory only*."""

    # 위험 기반 — 1회 거래당 손실 한도 (계좌 자본 대비 %).
    max_risk_per_trade_pct:   float = 0.01        # 1.0%
    # default stop-loss — 가격 대비 %.
    default_stop_loss_pct:    float = 0.03        # 3.0%
    # 1 종목 최대 비중 (계좌 자본 대비 %).
    max_position_pct:         float = 0.20        # 20%
    # 1 종목 최대 KRW.
    max_position_krw:         int   = 5_000_000   # 500만원 (default conservative)
    # confidence 임계 — 미달이면 quantity=0.
    min_confidence_threshold: float = 0.40
    # risk_flags 다수 → quantity=0 임계.
    max_risk_flags:           int   = 3
    # 최소 가상 단위 수량 — 1 이면 정수 가상 주식 수.
    min_unit_quantity:        int   = 1

    def __post_init__(self) -> None:
        if not (0.0 < self.max_risk_per_trade_pct <= 1.0):
            raise ValueError(
                f"max_risk_per_trade_pct must be in (0, 1], got {self.max_risk_per_trade_pct}"
            )
        if not (0.0 < self.default_stop_loss_pct <= 1.0):
            raise ValueError(
                f"default_stop_loss_pct must be in (0, 1], got {self.default_stop_loss_pct}"
            )
        if not (0.0 < self.max_position_pct <= 1.0):
            raise ValueError(
                f"max_position_pct must be in (0, 1], got {self.max_position_pct}"
            )
        if self.max_position_krw <= 0:
            raise ValueError("max_position_krw must be > 0")
        if not (0.0 <= self.min_confidence_threshold <= 1.0):
            raise ValueError(
                f"min_confidence_threshold must be in [0, 1], got {self.min_confidence_threshold}"
            )
        if self.max_risk_flags < 0:
            raise ValueError("max_risk_flags must be >= 0")
        if self.min_unit_quantity < 1:
            raise ValueError("min_unit_quantity must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_risk_per_trade_pct":    float(self.max_risk_per_trade_pct),
            "default_stop_loss_pct":     float(self.default_stop_loss_pct),
            "max_position_pct":          float(self.max_position_pct),
            "max_position_krw":          int(self.max_position_krw),
            "min_confidence_threshold":  float(self.min_confidence_threshold),
            "max_risk_flags":            int(self.max_risk_flags),
            "min_unit_quantity":         int(self.min_unit_quantity),
        }


@dataclass(frozen=True)
class SizingInput:
    """단일 sizing 호출 입력 — caller 가 채워서 전달.

    *secret 필드 0건* — API key / 계좌번호 carry 0개 (테스트 lock).
    """

    strategy:        str
    symbol:          str
    price:           float
    account_equity:  float
    confidence:      float = 0.5    # 0~1
    risk_flag_count: int   = 0
    market_regime:   str   = "UNKNOWN"
    loop_state:      str   = "PAUSED"
    stop_loss_pct:   float | None = None    # None → policy.default_stop_loss_pct

    def __post_init__(self) -> None:
        if not self.strategy or not self.symbol:
            raise ValueError("strategy / symbol must be non-empty.")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if self.risk_flag_count < 0:
            raise ValueError("risk_flag_count must be >= 0")
        if self.stop_loss_pct is not None and not (0.0 < self.stop_loss_pct <= 1.0):
            raise ValueError(
                f"stop_loss_pct must be in (0,1] or None, got {self.stop_loss_pct}"
            )


@dataclass(frozen=True)
class SizingResult:
    """sizing 결과 — 가상 수량 + 사유 + advisory 라벨."""

    strategy:        str
    symbol:          str
    verdict:         SizingVerdict
    quantity:        int                 # *가상* 주식 수 (음수 X — caller 가 BUY/SELL 방향 적용)
    notional_krw:    float               # price * quantity (KRW)
    risk_krw:        float               # quantity * price * stop_loss_pct (1회 거래 손실 한도 추정)
    multiplier:      float               # confidence × risk_flag × regime 결합 multiplier
    reasons:         list[str]           = field(default_factory=list)
    policy_snapshot: dict[str, Any]      = field(default_factory=dict)
    input_snapshot:  dict[str, Any]      = field(default_factory=dict)

    # 절대 invariant — 본 결과는 *advisory*.
    is_order_signal:       bool = False
    auto_apply_allowed:    bool = False
    is_live_authorization: bool = False

    def __post_init__(self) -> None:
        for name, val in (
            ("is_order_signal",       self.is_order_signal),
            ("auto_apply_allowed",    self.auto_apply_allowed),
            ("is_live_authorization", self.is_live_authorization),
        ):
            if val is not False:
                raise ValueError(f"SizingResult.{name} must be False.")
        if not isinstance(self.verdict, SizingVerdict):
            raise ValueError("verdict must be SizingVerdict.")
        if self.quantity < 0:
            raise ValueError(
                f"SizingResult.quantity must be >= 0 (direction applied by caller), "
                f"got {self.quantity}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy":         self.strategy,
            "symbol":           self.symbol,
            "verdict":          self.verdict.value,
            "verdict_label_ko": _VERDICT_LABEL_KO[self.verdict],
            "quantity":         int(self.quantity),
            "notional_krw":     float(self.notional_krw),
            "risk_krw":         float(self.risk_krw),
            "multiplier":       float(self.multiplier),
            "reasons":          list(self.reasons),
            "policy_snapshot":  dict(self.policy_snapshot),
            "input_snapshot":   dict(self.input_snapshot),
            # invariants.
            "is_order_signal":       False,
            "auto_apply_allowed":    False,
            "is_live_authorization": False,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Multiplier helpers
# ─────────────────────────────────────────────────────────────────────────────


def _confidence_multiplier(confidence: float) -> float:
    """confidence 0~1 → 수량 multiplier 0.5~1.0 (선형 보간).

    confidence ≥ 0.9 → 1.0 (full size)
    confidence ≤ 0.5 → 0.5 (반 size, *경계 — 최소 confidence 미달 시 0*)
    """
    if confidence >= 0.9:
        return 1.0
    if confidence >= 0.7:
        return 0.85
    if confidence >= 0.5:
        return 0.6
    return 0.5   # min_confidence_threshold 통과 시 최저 multiplier


def _risk_flag_multiplier(flag_count: int) -> float:
    """위험 신호 개수 → multiplier (0개=1.0, 1개=0.7, 2개=0.4, 3+개=0)."""
    if flag_count <= 0:
        return 1.0
    if flag_count == 1:
        return 0.7
    if flag_count == 2:
        return 0.4
    return 0.0


def _regime_multiplier(regime: str) -> float:
    return _DEFAULT_REGIME_MULTIPLIER.get(regime, 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Main entry — compute_position_size
# ─────────────────────────────────────────────────────────────────────────────


def compute_position_size(
    inp:    SizingInput,
    policy: PositionSizingPolicy | None = None,
) -> SizingResult:
    """입력 → 가상 수량 산정 결과.

    *broker 호출 0건* — 순수 계산.
    """
    pol = policy or PositionSizingPolicy()
    reasons: list[str] = []
    input_snap = {
        "strategy":        inp.strategy,
        "symbol":          inp.symbol,
        "price":           float(inp.price),
        "account_equity":  float(inp.account_equity),
        "confidence":      float(inp.confidence),
        "risk_flag_count": int(inp.risk_flag_count),
        "market_regime":   inp.market_regime,
        "loop_state":      inp.loop_state,
        "stop_loss_pct":   inp.stop_loss_pct,
    }
    pol_snap = pol.to_dict()

    def _zero(verdict: SizingVerdict, reason: str) -> SizingResult:
        reasons.append(reason)
        return SizingResult(
            strategy=inp.strategy, symbol=inp.symbol,
            verdict=verdict,
            quantity=0, notional_krw=0.0, risk_krw=0.0, multiplier=0.0,
            reasons=reasons,
            policy_snapshot=pol_snap, input_snapshot=input_snap,
        )

    # 1. EMERGENCY_STOP → quantity 0 (영구).
    if inp.loop_state == "EMERGENCY_STOP":
        return _zero(
            SizingVerdict.BLOCKED_EMERGENCY,
            "EMERGENCY_STOP — 모든 sizing 차단",
        )

    # 2. UNKNOWN regime → quantity 0.
    if inp.market_regime == "UNKNOWN":
        return _zero(
            SizingVerdict.BLOCKED_UNKNOWN,
            "UNKNOWN 장세 — 수량 0",
        )

    # 3. confidence 임계 미달 → quantity 0.
    if inp.confidence < pol.min_confidence_threshold:
        return _zero(
            SizingVerdict.BLOCKED_LOW_CONFIDENCE,
            f"confidence={inp.confidence:.2f} < threshold={pol.min_confidence_threshold:.2f}",
        )

    # 4. risk_flags 다수 → quantity 0.
    if inp.risk_flag_count >= pol.max_risk_flags:
        return _zero(
            SizingVerdict.BLOCKED_RISK_FLAGS,
            f"risk_flag_count={inp.risk_flag_count} >= max={pol.max_risk_flags}",
        )

    # 5. price / equity 검증.
    if inp.price <= 0 or not math.isfinite(inp.price):
        return _zero(SizingVerdict.INSUFFICIENT_DATA, f"price invalid: {inp.price}")
    if inp.account_equity <= 0 or not math.isfinite(inp.account_equity):
        return _zero(
            SizingVerdict.INSUFFICIENT_DATA,
            f"account_equity invalid: {inp.account_equity}",
        )

    # 6. risk-based 계산.
    stop_loss = inp.stop_loss_pct if inp.stop_loss_pct is not None \
        else pol.default_stop_loss_pct
    # position_risk_krw = 계좌 자본 × max_risk_per_trade_pct.
    position_risk_krw = inp.account_equity * pol.max_risk_per_trade_pct
    # base 수량 = position_risk_krw / (price × stop_loss_pct).
    # stop_loss 가 크면 base 수량은 감소.
    base_quantity_raw = position_risk_krw / (inp.price * stop_loss)
    reasons.append(
        f"base: risk_krw={position_risk_krw:.0f} / "
        f"(price={inp.price:.2f} × stop_loss={stop_loss:.4f}) = {base_quantity_raw:.2f}"
    )

    # 7. Multiplier 결합.
    conf_mult = _confidence_multiplier(inp.confidence)
    flag_mult = _risk_flag_multiplier(inp.risk_flag_count)
    regime_mult = _regime_multiplier(inp.market_regime)
    multiplier = conf_mult * flag_mult * regime_mult
    reasons.append(
        f"multiplier: confidence={conf_mult:.2f} × risk_flag={flag_mult:.2f} × "
        f"regime[{inp.market_regime}]={regime_mult:.2f} = {multiplier:.3f}"
    )

    sized_raw = base_quantity_raw * multiplier

    # 8. Cap 적용 — max_position_pct / max_position_krw.
    max_qty_from_pct = (inp.account_equity * pol.max_position_pct) / inp.price
    max_qty_from_krw = pol.max_position_krw / inp.price
    cap = min(max_qty_from_pct, max_qty_from_krw)
    final_qty_raw = min(sized_raw, cap)
    if sized_raw > cap:
        reasons.append(
            f"cap_applied: sized={sized_raw:.2f} → cap={cap:.2f} "
            f"(min(pct_cap={max_qty_from_pct:.2f}, krw_cap={max_qty_from_krw:.2f}))"
        )

    # 9. 최종 quantity (정수, 최소 단위).
    final_qty = int(math.floor(final_qty_raw))
    if final_qty < pol.min_unit_quantity:
        # 정수 floor 후 최소 단위 미달 → 0 (수량 부족).
        reasons.append(
            f"below_min_unit: floor({final_qty_raw:.2f}) = {final_qty} < "
            f"min_unit={pol.min_unit_quantity} → 수량 0"
        )
        return SizingResult(
            strategy=inp.strategy, symbol=inp.symbol,
            verdict=SizingVerdict.INSUFFICIENT_DATA,
            quantity=0, notional_krw=0.0, risk_krw=0.0, multiplier=multiplier,
            reasons=reasons,
            policy_snapshot=pol_snap, input_snapshot=input_snap,
        )

    notional = inp.price * final_qty
    risk = notional * stop_loss

    # 10. verdict 결정.
    if multiplier < 1.0 or final_qty_raw < base_quantity_raw:
        verdict = SizingVerdict.REDUCED
        reasons.append(
            f"REDUCED — final_qty={final_qty} (base_raw={base_quantity_raw:.2f} × "
            f"multiplier={multiplier:.3f} → {sized_raw:.2f}, cap={cap:.2f})"
        )
    else:
        verdict = SizingVerdict.SIZED
        reasons.append(f"SIZED — final_qty={final_qty}")

    return SizingResult(
        strategy=inp.strategy, symbol=inp.symbol,
        verdict=verdict,
        quantity=final_qty,
        notional_krw=notional,
        risk_krw=risk,
        multiplier=multiplier,
        reasons=reasons,
        policy_snapshot=pol_snap, input_snapshot=input_snap,
    )


__all__ = [
    "SIZER_SCHEMA_VERSION",
    "PositionSizingPolicy",
    "SizingInput",
    "SizingResult",
    "SizingVerdict",
    "compute_position_size",
    # P-08 (아래 정의) — 단순 max_amount / price 기반 sizer.
    "QuantityByPriceVerdict",
    "QuantityByPriceResult",
    "compute_paper_quantity_by_price",
]


# ============================================================================
# P-08: Paper position sizing by price — *simple* max_amount / price floor.
# ============================================================================
#
# AI Paper 매수 후보가 결정되면 *종목당 투자금 한도* (P-02 / P-04 / P-06 의
# `effective_per_symbol_cap_krw`) 와 *현재가* 만으로 수량을 계산:
#
#     quantity = floor(max_amount / price)
#
# 위 계산은 #4-08 risk-based `compute_position_size` 와 *별개*:
# - #4-08 은 risk_per_trade × stop_loss × confidence × regime × flags 의
#   복잡한 휴리스틱 sizing — 운영자가 설정한 위험 한도 기반.
# - P-08 은 *가장 단순한 cap-based* sizing — 종목당 한도와 현재가만 보고
#   "몇 주 살 수 있는가" 만 답한다.
#
# 두 sizer 의 책임:
#  - P-08 `compute_paper_quantity_by_price` → "현재가 기준 살 수 있는 최대
#    수량" (정수, ≥ 1 만 의미 있음).
#  - 그 다음 P-07 `check_buy_cash_sufficient` → 남은 Paper 현금 으로 그 수량
#    이 실제 가능한지 검사.
#  - 그 다음 RiskManager / PermissionGate → live-side 검사.
#  - 본 PR 은 *P-08 만* 추가하며 위 흐름의 다음 단계 wire-up 은 별도 PR.
#
# 호출 순서 (사용자 요청서 §11):
#   현재가 확인
#   → 종목당 투자금 기준 quantity 계산   (P-08, 본 모듈)
#   → quantity >= 1 인지 확인           (P-08 + P-05)
#   → Paper 현금 잔고 확인              (P-07)
#   → RiskManager
#   → PermissionGate
#   → VirtualOrder / PaperOrder 후보 생성
#
# CLAUDE.md 절대 원칙 (테스트로 lock):
#  - broker / OrderExecutor / route_order import 0건
#  - KIS / Anthropic / OpenAI / httpx / requests import 0건
#  - settings.enable_*_trading mutation 0건
#  - QuantityByPriceResult.is_order_signal / is_live_authorization = False
#    영구 (dataclass __post_init__ ValueError 가드)
#  - is_paper_only = True 영구


class QuantityByPriceVerdict(StrEnum):
    """수량 계산 결과 라벨 — frontend / API payload / diagnostics 가 그대로 emit."""

    OK                       = "OK"                       # quantity ≥ 1
    MIN_LOT_NOT_AFFORDABLE   = "MIN_LOT_NOT_AFFORDABLE"   # floor(cap/price) < 1
    INVALID_PRICE            = "INVALID_PRICE"            # price <= 0
    INVALID_MAX_AMOUNT       = "INVALID_MAX_AMOUNT"       # max_amount <= 0
    SKIP_NON_BUY             = "SKIP_NON_BUY"             # action != BUY


_BUY_TOKENS = frozenset({
    "buy", "open", "open_long", "long", "enter", "entry",
})


def _is_buy_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _BUY_TOKENS


def _format_krw(amount: int | float) -> str:
    return f"{int(amount):,}"


@dataclass(frozen=True)
class QuantityByPriceResult:
    """P-08: max_amount / price 기반 수량 계산 결과 — *advisory*.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ ValueError 가드).
    """

    verdict:         QuantityByPriceVerdict
    quantity:        int
    notional_krw:    int
    remainder_krw:   int
    symbol:          str | None       = None
    action:          str | None       = None
    price:           float | None     = None
    max_amount_krw:  int              = 0
    reason_code:     str              = ""
    reason_ko:       str              = ""
    metadata:        dict[str, Any]   = field(default_factory=dict)

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    is_paper_only:         bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError(
                "QuantityByPriceResult.is_order_signal must be False"
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "QuantityByPriceResult.is_live_authorization must be False"
            )
        if self.is_paper_only is not True:
            raise ValueError(
                "QuantityByPriceResult.is_paper_only must be True"
            )
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool):
            raise ValueError(
                f"QuantityByPriceResult.quantity must be int, "
                f"got {type(self.quantity).__name__}"
            )
        if self.quantity < 0:
            raise ValueError(
                f"quantity must be >= 0, got {self.quantity}"
            )
        if self.notional_krw < 0:
            raise ValueError(
                f"notional_krw must be >= 0, got {self.notional_krw}"
            )

    @property
    def is_ok(self) -> bool:
        return self.verdict == QuantityByPriceVerdict.OK

    @property
    def is_min_lot_not_affordable(self) -> bool:
        return self.verdict == QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":               self.verdict.value,
            "quantity":              int(self.quantity),
            "notional_krw":          int(self.notional_krw),
            "remainder_krw":         int(self.remainder_krw),
            "symbol":                self.symbol,
            "action":                self.action,
            "price":                 self.price,
            "max_amount_krw":        int(self.max_amount_krw),
            "reason_code":           self.reason_code,
            "reason_ko":             self.reason_ko,
            "metadata":              dict(self.metadata),
            "is_ok":                 self.is_ok,
            "is_min_lot_not_affordable": self.is_min_lot_not_affordable,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_only":         self.is_paper_only,
        }


def compute_paper_quantity_by_price(
    *,
    action:          str | None,
    symbol:          str | None,
    price:           float | int | None,
    max_amount_krw:  int,
) -> QuantityByPriceResult:
    """P-08: `quantity = floor(max_amount / price)` 계산 — *advisory*.

    실행 순서 (verdict 우선순위 — 첫 매칭 verdict 반환):
      1. action 이 BUY 가 아님         → SKIP_NON_BUY
      2. max_amount <= 0               → INVALID_MAX_AMOUNT
      3. price is None / price <= 0    → INVALID_PRICE
      4. floor(max_amount/price) < 1   → MIN_LOT_NOT_AFFORDABLE
      5. 그 외                          → OK (quantity ≥ 1)

    Args:
        action: 매매 의도. BUY 만 sizing. 그 외 → SKIP_NON_BUY.
        symbol: 후보 종목 코드. None 허용.
        price: 1주 가격 (KRW). None / 0 / 음수 → INVALID_PRICE.
        max_amount_krw: 종목당 투자금 한도 (KRW, 정수).

    Returns:
        QuantityByPriceResult. broker / route_order 호출 0건.

    호출 컨텍스트:
        본 함수는 *sizing 단계* — 호출자는 결과를 받고 P-07 cash check /
        RiskManager / PermissionGate 흐름을 *별도로* 진행해야 한다.
    """
    # 1. SKIP_NON_BUY.
    if not _is_buy_action(action):
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.SKIP_NON_BUY,
            quantity=0, notional_krw=0, remainder_krw=int(max(max_amount_krw, 0)),
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            max_amount_krw=int(max(max_amount_krw, 0)),
            reason_code="SKIP_NON_BUY",
            reason_ko=(
                "BUY 가 아닌 action — 수량 계산 무관 (청산 / 관망은 sizing 강제 적용 안 함)."
            ),
        )

    cap = int(max_amount_krw)

    # 2. INVALID_MAX_AMOUNT.
    if cap <= 0:
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.INVALID_MAX_AMOUNT,
            quantity=0, notional_krw=0, remainder_krw=0,
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            max_amount_krw=cap,
            reason_code="INVALID_MAX_AMOUNT",
            reason_ko=(
                "종목당 투자금이 0 이하라 수량을 계산할 수 없습니다."
            ),
        )

    # 3. INVALID_PRICE.
    if price is None:
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.INVALID_PRICE,
            quantity=0, notional_krw=0, remainder_krw=cap,
            symbol=symbol, action=action,
            price=None, max_amount_krw=cap,
            reason_code="INVALID_PRICE",
            reason_ko=(
                "현재가가 없어 수량을 계산할 수 없습니다."
            ),
        )

    try:
        p = float(price)
    except (TypeError, ValueError):
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.INVALID_PRICE,
            quantity=0, notional_krw=0, remainder_krw=cap,
            symbol=symbol, action=action,
            price=None, max_amount_krw=cap,
            reason_code="INVALID_PRICE",
            reason_ko=(
                f"현재가 {price!r} 가 숫자가 아니라 수량을 계산할 수 없습니다."
            ),
        )

    if not math.isfinite(p) or p <= 0:
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.INVALID_PRICE,
            quantity=0, notional_krw=0, remainder_krw=cap,
            symbol=symbol, action=action,
            price=p if math.isfinite(p) else None,
            max_amount_krw=cap,
            reason_code="INVALID_PRICE",
            reason_ko=(
                f"현재가 {p} 가 0 이하라 수량을 계산할 수 없습니다."
            ),
        )

    # 4. floor(max_amount / price) 계산.
    qty = int(math.floor(cap / p))
    notional = int(p * qty) if qty > 0 else 0
    remainder = cap - notional

    if qty < 1:
        return QuantityByPriceResult(
            verdict=QuantityByPriceVerdict.MIN_LOT_NOT_AFFORDABLE,
            quantity=0, notional_krw=0, remainder_krw=cap,
            symbol=symbol, action=action,
            price=p, max_amount_krw=cap,
            reason_code="MIN_LOT_NOT_AFFORDABLE",
            reason_ko=(
                f"종목당 투자금 {_format_krw(cap)}원으로 1주({_format_krw(p)}원)"
                "를 살 수 없습니다 (1주도 살 수 없음)."
            ),
        )

    # 5. OK.
    return QuantityByPriceResult(
        verdict=QuantityByPriceVerdict.OK,
        quantity=qty, notional_krw=notional, remainder_krw=remainder,
        symbol=symbol, action=action,
        price=p, max_amount_krw=cap,
        reason_code="OK",
        reason_ko=(
            f"종목당 투자금 기준 {qty}주 매수 가능 "
            f"(1주 {_format_krw(p)}원 × {qty}주 = "
            f"{_format_krw(notional)}원, 잔여 {_format_krw(remainder)}원)."
        ),
    )
