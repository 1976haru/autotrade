"""P-04: Paper BUY 후보 매수 가능성 (affordability) 사전 검사 — pure helper.

종목 1주 가격이 종목당 투자금 한도보다 비싸거나, 남은 Paper 현금이 1주
가격보다 적으면 AI Paper BUY 후보에서 *자동 제외* 한다.

예시 (사용자 요청서):
- 종목당 투자금 한도 1,000,000원 + SK하이닉스 1주 가격 1,800,000원
  → 1주도 살 수 없으므로 BUY 차단 (PRICE_OVER_CAP).

본 모듈은 *순수 함수* — broker / OrderExecutor / route_order / KIS / 외부 HTTP /
AI SDK / settings import 0건. caller (paper_decision_bridge / agent_consumer
등) 가 매 BUY 후보 *전* 본 함수를 호출해 verdict 를 받아 후보 보류 / 강등
여부를 결정.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (안전 flag 결합 0건)
- app.brokers / app.execution / app.kis_paper.engine import 0건
- AffordabilityResult.is_order_signal / is_live_authorization = False 영구
- is_paper_only = True 영구
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Verdict
# ============================================================================


class AffordabilityVerdict(StrEnum):
    """매수 가능성 평가 라벨.

    값은 frontend / API payload / ledger 에 그대로 emit. caller 는 verdict 와
    *함께 reason_ko* 를 사용자에게 표시.
    """
    AFFORDABLE             = "AFFORDABLE"
    PRICE_OVER_CAP         = "PRICE_OVER_CAP"          # price > per_symbol_cap
    INSUFFICIENT_CASH      = "INSUFFICIENT_CASH"       # available_cash < price
    BELOW_MIN_LOT          = "BELOW_MIN_LOT"           # floor(min/price) < 1
    MAX_POSITIONS_REACHED  = "MAX_POSITIONS_REACHED"   # 보유 ≥ 한도 (신규 종목)
    INVALID_PRICE          = "INVALID_PRICE"           # price <= 0
    MISSING_PRICE          = "MISSING_PRICE"           # price is None
    SKIP_NON_BUY           = "SKIP_NON_BUY"            # action != BUY


# ============================================================================
# Result dataclass
# ============================================================================


@dataclass(frozen=True)
class AffordabilityResult:
    """매수 가능성 평가 결과 — *advisory*, broker / route_order 호출 0건.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    verdict:                       AffordabilityVerdict
    symbol:                        str | None         = None
    action:                        str | None         = None
    price:                         float | None       = None
    effective_per_symbol_cap_krw:  int                = 0
    available_cash_krw:            int                = 0
    affordable_quantity:           int                = 0
    current_held_unique_symbols:   int                = 0
    max_concurrent_positions:      int                = 0
    is_existing_position:          bool               = False
    reason_ko:                     str                = ""
    risk_flag:                     str | None         = None
    metadata:                      dict[str, Any]     = field(default_factory=dict)

    is_order_signal:               bool = False
    is_live_authorization:         bool = False
    is_paper_only:                 bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("AffordabilityResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("AffordabilityResult.is_live_authorization must be False")
        if self.is_paper_only is not True:
            raise ValueError("AffordabilityResult.is_paper_only must be True")
        if self.affordable_quantity < 0:
            raise ValueError(
                f"affordable_quantity must be >= 0, got {self.affordable_quantity}"
            )

    @property
    def is_affordable(self) -> bool:
        """편의 — AFFORDABLE verdict 이면 True."""
        return self.verdict == AffordabilityVerdict.AFFORDABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                      self.verdict.value,
            "symbol":                       self.symbol,
            "action":                       self.action,
            "price":                        self.price,
            "effective_per_symbol_cap_krw": int(self.effective_per_symbol_cap_krw),
            "available_cash_krw":           int(self.available_cash_krw),
            "affordable_quantity":          int(self.affordable_quantity),
            "current_held_unique_symbols":  int(self.current_held_unique_symbols),
            "max_concurrent_positions":     int(self.max_concurrent_positions),
            "is_existing_position":         self.is_existing_position,
            "reason_ko":                    self.reason_ko,
            "risk_flag":                    self.risk_flag,
            "metadata":                     dict(self.metadata),
            "is_affordable":                self.is_affordable,
            "is_order_signal":              self.is_order_signal,
            "is_live_authorization":        self.is_live_authorization,
            "is_paper_only":                self.is_paper_only,
        }


# ============================================================================
# Helper functions
# ============================================================================


# action 정규화 — 대소문자 / 공백 무시.
_BUY_TOKENS = frozenset({"buy", "open", "open_long", "long", "enter", "entry"})


def _is_buy_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _BUY_TOKENS


def _format_krw(amount: int | float) -> str:
    """1,234,567 형태로 — 사람 친화 한국어 라벨."""
    return f"{int(amount):,}"


def check_paper_affordability(
    *,
    action:                       str | None,
    symbol:                       str | None,
    price:                        float | int | None,
    available_cash_krw:           int,
    effective_per_symbol_cap_krw: int,
    current_held_symbols:         list[str] | tuple[str, ...] | set[str] | None = None,
    max_concurrent_positions:     int = 0,
) -> AffordabilityResult:
    """Paper BUY 후보 사전 검사 — *advisory* verdict 반환.

    실행 순서 (verdict 우선순위 — 첫 매칭 verdict 반환):
      1. action 이 BUY 가 아님            → SKIP_NON_BUY (제한 무관)
      2. price is None                     → MISSING_PRICE
      3. price <= 0                        → INVALID_PRICE
      4. price > effective_per_symbol_cap_krw  → PRICE_OVER_CAP
      5. price > available_cash_krw        → INSUFFICIENT_CASH
      6. floor(min(cap, cash) / price) < 1 → BELOW_MIN_LOT
         (price 와 cap / cash 모두 정상이지만 1주조차 살 수 없는 corner case)
      7. 신규 종목 + 보유 ≥ 한도           → MAX_POSITIONS_REACHED
      8. 그 외                              → AFFORDABLE

    Args:
        action: 매매 의도 — BUY 만 검사. 그 외 → SKIP_NON_BUY.
        symbol: 후보 종목 코드. None 도 허용 (verdict 영향 없음).
        price: 1주 가격 (KRW). None / 0 / 음수 는 별도 verdict.
        available_cash_krw: 남은 Paper 현금. 음수면 0 으로 clamp.
        effective_per_symbol_cap_krw: PaperCapitalConfig.effective_per_symbol_cap_krw
            그대로 전달 권장.
        current_held_symbols: 현재 *고유* 보유 종목 코드 컬렉션. None 이면 0건.
        max_concurrent_positions: P-03 한도 (0 이면 한도 검사 skip).

    Returns:
        AffordabilityResult. broker / route_order 호출 0건.
    """
    cap   = int(effective_per_symbol_cap_krw)
    cash  = max(int(available_cash_krw), 0)

    held: set[str] = set()
    if current_held_symbols:
        held = {str(s) for s in current_held_symbols if s}
    unique_count = len(held)
    is_existing = bool(symbol and symbol in held)

    # 1. BUY 가 아니면 검사 skip.
    if not _is_buy_action(action):
        return AffordabilityResult(
            verdict=AffordabilityVerdict.SKIP_NON_BUY,
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko="BUY 가 아닌 action — affordability 검사 무관 (청산 / 관망은 자유)",
        )

    # 2. price 누락.
    if price is None:
        return AffordabilityResult(
            verdict=AffordabilityVerdict.MISSING_PRICE,
            symbol=symbol, action=action,
            price=None,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko="현재가가 없어 매수 가능성을 평가할 수 없어 Paper 매수 후보에서 제외했습니다.",
            risk_flag="missing_price",
        )

    p = float(price)

    # 3. price <= 0 (이상값).
    if p <= 0:
        return AffordabilityResult(
            verdict=AffordabilityVerdict.INVALID_PRICE,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko=(
                f"현재가 {p} 가 0 이하라 매수 가능성을 평가할 수 없어 Paper 매수 "
                "후보에서 제외했습니다."
            ),
            risk_flag="invalid_price",
        )

    # 4. price > cap → 1주 가격이 종목당 한도 초과.
    if cap > 0 and p > cap:
        return AffordabilityResult(
            verdict=AffordabilityVerdict.PRICE_OVER_CAP,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            affordable_quantity=0,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko=(
                f"1주 가격이 종목당 투자금 한도 {_format_krw(cap)}원을 초과해 "
                "Paper 매수 후보에서 제외했습니다."
            ),
            risk_flag="price_over_per_symbol_cap",
        )

    # 5. price > available_cash → 남은 현금으로 1주도 못 산다.
    if p > cash:
        return AffordabilityResult(
            verdict=AffordabilityVerdict.INSUFFICIENT_CASH,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            affordable_quantity=0,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko=(
                f"남은 Paper 현금 {_format_krw(cash)}원으로 1주({_format_krw(p)}원)를 "
                "살 수 없어 Paper 매수 후보에서 제외했습니다."
            ),
            risk_flag="insufficient_paper_cash",
        )

    # 6. floor(min(cap, cash) / price) — 매수 가능 수량.
    budget = min(cap, cash) if cap > 0 else cash
    affordable_qty = int(budget // p) if p > 0 else 0

    if affordable_qty < 1:
        return AffordabilityResult(
            verdict=AffordabilityVerdict.BELOW_MIN_LOT,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            affordable_quantity=0,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko=(
                f"가용 예산 {_format_krw(budget)}원으로 1주({_format_krw(p)}원)를 "
                "살 수 없어 Paper 매수 후보에서 제외했습니다."
            ),
            risk_flag="below_min_lot",
        )

    # 7. 보유 종목 수 한도. 추가 매수는 항상 ALLOW.
    if (
        max_concurrent_positions > 0
        and not is_existing
        and unique_count >= int(max_concurrent_positions)
    ):
        return AffordabilityResult(
            verdict=AffordabilityVerdict.MAX_POSITIONS_REACHED,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            available_cash_krw=cash,
            affordable_quantity=affordable_qty,
            current_held_unique_symbols=unique_count,
            max_concurrent_positions=int(max_concurrent_positions),
            is_existing_position=is_existing,
            reason_ko=(
                f"동시 보유 종목 수 한도 {max_concurrent_positions}종목 도달 — "
                f"신규 종목 매수 후보에서 제외했습니다 (현재 {unique_count}종목 보유)."
            ),
            risk_flag="max_positions_reached",
        )

    # 8. 통과.
    return AffordabilityResult(
        verdict=AffordabilityVerdict.AFFORDABLE,
        symbol=symbol, action=action,
        price=p,
        effective_per_symbol_cap_krw=cap,
        available_cash_krw=cash,
        affordable_quantity=affordable_qty,
        current_held_unique_symbols=unique_count,
        max_concurrent_positions=int(max_concurrent_positions),
        is_existing_position=is_existing,
        reason_ko=(
            f"Paper 매수 가능: 1주 {_format_krw(p)}원 × {affordable_qty}주 "
            f"가능 (한도 {_format_krw(cap)}원 / 현금 {_format_krw(cash)}원)."
        ),
    )


__all__ = [
    "AffordabilityVerdict",
    "AffordabilityResult",
    "check_paper_affordability",
]
