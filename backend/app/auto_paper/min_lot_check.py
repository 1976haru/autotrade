"""P-05: Paper 최소 1주 매수 조건 가드 — pure helper.

국내주식 단타 단위는 *정수 1주* — 소수점 주식이 없다. AI Paper 모의매매가
*정수 수량 ≥ 1* 인 BUY 만 실행하도록 강제하는 사전 검사 모듈.

본 모듈은 P-04 (`affordability_check.py`) 와 함께 사용 — P-04 는 *가격 vs
한도 vs 현금* 의 매크로 조건을 보고, 본 모듈은 *결정된 매수 수량 자체*가
유효한 정수인지 + ≥ 1 인지 보는 마이크로 가드.

매트릭스:
    quantity = 0         → BLOCKED_ZERO_QUANTITY
    quantity = 음수      → BLOCKED_NEGATIVE_QUANTITY
    quantity = 0.5 등 소수 → BLOCKED_FRACTIONAL_QUANTITY
    quantity = 1 이상 정수 → ALLOWED
    action != BUY        → SKIP_NON_BUY (HOLD/SELL/EXIT 무관)

floor 정책:
- `compute_paper_affordable_lot(cap, cash, price)` 는 `floor(budget / price)` 사용.
- *반올림 금지* — int 가까운 쪽이 아니라 항상 *내림* (다음 lot 가 cap 을
  초과할 수 있으므로 보수적).
- price ≤ 0 / None 이면 0 반환 — caller 가 INVALID_PRICE / MISSING_PRICE
  로 처리.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건
- app.brokers / app.execution / app.kis_paper.engine import 0건
- MinLotCheckResult.is_order_signal / is_live_authorization = False 영구
- is_paper_only = True 영구
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Verdict
# ============================================================================


class MinLotVerdict(StrEnum):
    """최소 1주 매수 검사 결과 라벨.

    값은 frontend / API / ledger 에 그대로 emit. 모든 BLOCKED_* 는 *Paper
    BUY 차단* 을 의미 — SELL/EXIT/HOLD 는 SKIP_NON_BUY 로 분기.
    """
    ALLOWED                     = "ALLOWED"
    BLOCKED_ZERO_QUANTITY       = "BLOCKED_ZERO_QUANTITY"
    BLOCKED_NEGATIVE_QUANTITY   = "BLOCKED_NEGATIVE_QUANTITY"
    BLOCKED_FRACTIONAL_QUANTITY = "BLOCKED_FRACTIONAL_QUANTITY"
    SKIP_NON_BUY                = "SKIP_NON_BUY"


# ============================================================================
# Result dataclass
# ============================================================================


@dataclass(frozen=True)
class MinLotCheckResult:
    """최소 1주 매수 가드 결과 — advisory, broker / route_order 호출 0건."""

    verdict:                MinLotVerdict
    requested_quantity:     float | int        # caller-supplied (검증 전 원본)
    floored_quantity:       int                # floor 정책 적용 후 정수
    action:                 str | None         = None
    symbol:                 str | None         = None
    reason_ko:              str                = ""
    risk_flag:              str | None         = None
    metadata:               dict[str, Any]     = field(default_factory=dict)

    is_order_signal:        bool = False
    is_live_authorization:  bool = False
    is_paper_only:          bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("MinLotCheckResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("MinLotCheckResult.is_live_authorization must be False")
        if self.is_paper_only is not True:
            raise ValueError("MinLotCheckResult.is_paper_only must be True")
        if self.floored_quantity < 0:
            raise ValueError(
                f"floored_quantity must be >= 0, got {self.floored_quantity}"
            )

    @property
    def is_allowed(self) -> bool:
        return self.verdict == MinLotVerdict.ALLOWED

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":               self.verdict.value,
            "requested_quantity":    self.requested_quantity,
            "floored_quantity":      int(self.floored_quantity),
            "action":                self.action,
            "symbol":                self.symbol,
            "reason_ko":             self.reason_ko,
            "risk_flag":             self.risk_flag,
            "metadata":              dict(self.metadata),
            "is_allowed":            self.is_allowed,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_only":         self.is_paper_only,
        }


# ============================================================================
# Helper functions
# ============================================================================


_BUY_TOKENS = frozenset({"buy", "open", "open_long", "long", "enter", "entry"})


def _is_buy_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _BUY_TOKENS


def _is_integer_value(qty: float | int) -> bool:
    """qty 가 *정수 값* 인지 (1.0 / 2.0 도 정수로 인정, 1.5 는 거부).

    소수점이 0 인지 검사 — 운영자가 1.0 같은 float 으로 보내도 정수 의도면
    통과. 진짜 fractional (0.5 등) 은 거부.
    """
    if isinstance(qty, bool):
        # bool 은 int 의 서브타입이라 isinstance(True, int) → True. 의도치 않은
        # bool 입력은 거부.
        return False
    if isinstance(qty, int):
        return True
    if isinstance(qty, float):
        # NaN / inf 거부 + 소수점 0 검사.
        if math.isnan(qty) or math.isinf(qty):
            return False
        return float(qty).is_integer()
    return False


def compute_paper_affordable_lot(
    *,
    effective_per_symbol_cap_krw: int,
    available_cash_krw:           int,
    price:                        float | int | None,
) -> int:
    """*floor* 기준 매수 가능 lot 계산 — 정수 반환.

    `floor(min(cap, cash) / price)`. price <= 0 / None 이면 0.

    *반올림 금지* — 항상 내림. 예: budget=999_999 + price=1_000 → 999주 (1000
    아님). cap=0 (cap 무한 — 의미상) 인 경우 cash 만 제약.
    """
    if price is None:
        return 0
    p = float(price)
    if p <= 0:
        return 0
    cap = int(effective_per_symbol_cap_krw)
    cash = max(int(available_cash_krw), 0)
    budget = min(cap, cash) if cap > 0 else cash
    # int(budget // p) — budget 은 int, p 는 float → 결과 float, int() 로 내림.
    # 부동소수점 오차 안전 측면에서 math.floor 명시 사용.
    return int(math.floor(budget / p))


def validate_paper_min_lot(
    *,
    action:    str | None,
    quantity:  float | int | None,
    symbol:    str | None = None,
) -> MinLotCheckResult:
    """최소 1주 매수 가드.

    Args:
        action:   매매 의도. BUY 만 검사. 그 외는 SKIP_NON_BUY.
        quantity: caller 가 계산한 매수 수량. *정수 ≥ 1* 만 허용.
        symbol:   참조 종목 (verdict 영향 없음).

    Returns:
        MinLotCheckResult.

    Verdict 우선순위:
      1. action != BUY            → SKIP_NON_BUY
      2. quantity is None         → BLOCKED_ZERO_QUANTITY (None = 0 취급)
      3. quantity < 0             → BLOCKED_NEGATIVE_QUANTITY
      4. quantity is fractional   → BLOCKED_FRACTIONAL_QUANTITY
      5. quantity == 0            → BLOCKED_ZERO_QUANTITY
      6. quantity ≥ 1 정수         → ALLOWED
    """
    # 1. SKIP_NON_BUY — HOLD/SELL/EXIT 무관.
    if not _is_buy_action(action):
        return MinLotCheckResult(
            verdict=MinLotVerdict.SKIP_NON_BUY,
            requested_quantity=quantity if quantity is not None else 0,
            floored_quantity=0,
            action=action,
            symbol=symbol,
            reason_ko=(
                "BUY 가 아닌 action — 최소 1주 매수 가드 무관 (청산 / 관망은 "
                "수량 제한 없음)."
            ),
        )

    # 2. None → 0 취급.
    if quantity is None:
        return MinLotCheckResult(
            verdict=MinLotVerdict.BLOCKED_ZERO_QUANTITY,
            requested_quantity=0,
            floored_quantity=0,
            action=action,
            symbol=symbol,
            reason_ko=(
                "매수 수량이 지정되지 않아 Paper 매수 후보에서 제외했습니다 "
                "(1주 이상 정수 수량 필요)."
            ),
            risk_flag="zero_quantity",
        )

    # 3. 음수.
    if isinstance(quantity, (int, float)) and not isinstance(quantity, bool):
        if not math.isfinite(quantity) if isinstance(quantity, float) else False:
            return MinLotCheckResult(
                verdict=MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY,
                requested_quantity=quantity,
                floored_quantity=0,
                action=action,
                symbol=symbol,
                reason_ko=(
                    f"매수 수량 {quantity} 가 유효한 숫자가 아니라 Paper 매수 "
                    "후보에서 제외했습니다."
                ),
                risk_flag="invalid_quantity_nonfinite",
            )
        if quantity < 0:
            return MinLotCheckResult(
                verdict=MinLotVerdict.BLOCKED_NEGATIVE_QUANTITY,
                requested_quantity=quantity,
                floored_quantity=0,
                action=action,
                symbol=symbol,
                reason_ko=(
                    f"매수 수량 {quantity} 가 음수라 Paper 매수 후보에서 "
                    "제외했습니다."
                ),
                risk_flag="negative_quantity",
            )
    else:
        # int 도 float 도 아닌 입력 (예: str). caller 책임이지만 안전하게 거부.
        return MinLotCheckResult(
            verdict=MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY,
            requested_quantity=quantity,
            floored_quantity=0,
            action=action,
            symbol=symbol,
            reason_ko=(
                f"매수 수량 {quantity!r} 의 타입이 정수가 아니라 Paper 매수 "
                "후보에서 제외했습니다."
            ),
            risk_flag="invalid_quantity_type",
        )

    # 4. fractional (1.5 등) — 소수점 주식 불가.
    if not _is_integer_value(quantity):
        # 보수적 floor 적용한 정수 carry — 호출자가 참고 가능.
        floored = int(math.floor(float(quantity)))
        return MinLotCheckResult(
            verdict=MinLotVerdict.BLOCKED_FRACTIONAL_QUANTITY,
            requested_quantity=quantity,
            floored_quantity=max(floored, 0),
            action=action,
            symbol=symbol,
            reason_ko=(
                f"매수 수량 {quantity} 는 소수점이라 Paper 매수 후보에서 "
                "제외했습니다 (소수점 주식 불가 — 정수 1주 이상 필요)."
            ),
            risk_flag="fractional_quantity",
        )

    qty_int = int(quantity)

    # 5. 0.
    if qty_int == 0:
        return MinLotCheckResult(
            verdict=MinLotVerdict.BLOCKED_ZERO_QUANTITY,
            requested_quantity=quantity,
            floored_quantity=0,
            action=action,
            symbol=symbol,
            reason_ko=(
                "매수 수량이 0주라 Paper 매수 후보에서 제외했습니다 (최소 1주 "
                "이상 필요)."
            ),
            risk_flag="zero_quantity",
        )

    # 6. ≥ 1 정수.
    return MinLotCheckResult(
        verdict=MinLotVerdict.ALLOWED,
        requested_quantity=quantity,
        floored_quantity=qty_int,
        action=action,
        symbol=symbol,
        reason_ko=f"Paper 매수 수량 {qty_int}주 — 최소 1주 조건 통과.",
    )


__all__ = [
    "MinLotVerdict",
    "MinLotCheckResult",
    "compute_paper_affordable_lot",
    "validate_paper_min_lot",
]
