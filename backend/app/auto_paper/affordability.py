"""P-06: 고가주 처리 정책 — 종목당 투자금으로 1주도 살 수 없는 종목을
AI Paper 진입 전 단계에서 *정책별로* 안전 처리하는 pure helper.

P-04 (`affordability_check.py`) 의 `PRICE_OVER_CAP` / `BELOW_MIN_LOT` verdict 는
*항상 제외* 의 결과를 반환한다. 본 모듈은 그 위에 *정책 선택지* 를 얹어
운영자가 다음 3가지 중 하나를 선택할 수 있게 한다:

  - EXCLUDE              : 후보에서 제외 (default — 가장 보수적).
  - HOLD                 : 완전히 삭제하지 않고 보류 상태로 carry.
  - INCREASE_BUDGET_HINT : 사용자에게 *종목당 투자금 증액 안내* 만 carry.

본 모듈은 *순수 함수* — broker / OrderExecutor / route_order / KIS / 외부 HTTP /
AI SDK / settings import 0건. caller (UI / API / agent_consumer) 가 매 BUY
후보 *전* 본 함수를 호출해 verdict 를 받아 후보 보류 / 강등 / 안내 여부를
결정.

예시 (사용자 요청서):
- 종목당 투자금: 100,000원
- 현재가: 120,000원
- 계산 수량: 0주
- → 고가주 예외 처리 대상 — policy 에 따라 EXCLUDE / HOLD / HINT.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (안전 flag 결합 0건)
- app.brokers / app.execution / app.kis_paper.engine import 0건
- HighPriceCheckResult.is_order_signal / is_live_authorization = False 영구
- is_paper_only = True 영구
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ============================================================================
# Policy + Verdict
# ============================================================================


class HighPricePolicy(StrEnum):
    """고가주 처리 정책 — 3 선택지.

    default: `EXCLUDE` (가장 보수적). 운영자가 명시 변경 가능.
    """
    EXCLUDE              = "EXCLUDE"
    HOLD                 = "HOLD"
    INCREASE_BUDGET_HINT = "INCREASE_BUDGET_HINT"


# default 정책 — `evaluate_high_price` 의 policy 미지정 시 사용.
DEFAULT_HIGH_PRICE_POLICY: HighPricePolicy = HighPricePolicy.EXCLUDE


class HighPriceVerdict(StrEnum):
    """고가주 평가 결과 라벨.

    값은 frontend / API payload / ledger 에 그대로 emit. caller 는 verdict 와
    *함께 reason_ko* 를 사용자에게 표시.
    """
    AFFORDABLE       = "AFFORDABLE"        # 1주 이상 매수 가능 — 정상 흐름.
    EXCLUDED         = "EXCLUDED"          # policy=EXCLUDE — 후보에서 제외.
    HELD             = "HELD"              # policy=HOLD — 후보 보류.
    BUDGET_HINT      = "BUDGET_HINT"       # policy=INCREASE_BUDGET_HINT — 증액 안내.
    INVALID_PRICE    = "INVALID_PRICE"     # price <= 0
    MISSING_PRICE    = "MISSING_PRICE"     # price is None
    SKIP_NON_BUY     = "SKIP_NON_BUY"      # action != BUY


# ============================================================================
# Result dataclass
# ============================================================================


@dataclass(frozen=True)
class HighPriceCheckResult:
    """고가주 평가 결과 — *advisory*, broker / route_order 호출 0건.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    verdict:                       HighPriceVerdict
    policy:                        HighPricePolicy
    symbol:                        str | None         = None
    action:                        str | None         = None
    price:                         float | None       = None
    effective_per_symbol_cap_krw:  int                = 0
    affordable_quantity:           int                = 0
    is_high_price:                 bool               = False
    reason_ko:                     str                = ""
    risk_flag:                     str | None         = None
    suggested_min_cap_krw:         int | None         = None
    metadata:                      dict[str, Any]     = field(default_factory=dict)

    is_order_signal:               bool = False
    is_live_authorization:         bool = False
    is_paper_only:                 bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("HighPriceCheckResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("HighPriceCheckResult.is_live_authorization must be False")
        if self.is_paper_only is not True:
            raise ValueError("HighPriceCheckResult.is_paper_only must be True")
        if self.affordable_quantity < 0:
            raise ValueError(
                f"affordable_quantity must be >= 0, got {self.affordable_quantity}"
            )

    @property
    def is_affordable(self) -> bool:
        return self.verdict == HighPriceVerdict.AFFORDABLE

    @property
    def is_excluded(self) -> bool:
        return self.verdict == HighPriceVerdict.EXCLUDED

    @property
    def is_held(self) -> bool:
        return self.verdict == HighPriceVerdict.HELD

    @property
    def is_budget_hint(self) -> bool:
        return self.verdict == HighPriceVerdict.BUDGET_HINT

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":                      self.verdict.value,
            "policy":                       self.policy.value,
            "symbol":                       self.symbol,
            "action":                       self.action,
            "price":                        self.price,
            "effective_per_symbol_cap_krw": int(self.effective_per_symbol_cap_krw),
            "affordable_quantity":          int(self.affordable_quantity),
            "is_high_price":                self.is_high_price,
            "reason_ko":                    self.reason_ko,
            "risk_flag":                    self.risk_flag,
            "suggested_min_cap_krw":        (
                int(self.suggested_min_cap_krw)
                if self.suggested_min_cap_krw is not None else None
            ),
            "metadata":                     dict(self.metadata),
            "is_affordable":                self.is_affordable,
            "is_excluded":                  self.is_excluded,
            "is_held":                      self.is_held,
            "is_budget_hint":               self.is_budget_hint,
            "is_order_signal":              self.is_order_signal,
            "is_live_authorization":        self.is_live_authorization,
            "is_paper_only":                self.is_paper_only,
        }


# ============================================================================
# Helper functions
# ============================================================================


_BUY_TOKENS = frozenset({"buy", "open", "open_long", "long", "enter", "entry"})


def _is_buy_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _BUY_TOKENS


def _format_krw(amount: int | float) -> str:
    """1,234,567 형태로 — 사람 친화 한국어 라벨."""
    return f"{int(amount):,}"


def _resolve_policy(policy: HighPricePolicy | str | None) -> HighPricePolicy:
    """policy 미지정 / None → default EXCLUDE. 문자열은 enum 으로 정규화.

    잘못된 값은 ValueError — caller 가 명시 처리.
    """
    if policy is None:
        return DEFAULT_HIGH_PRICE_POLICY
    if isinstance(policy, HighPricePolicy):
        return policy
    if isinstance(policy, str):
        normalized = policy.strip().upper()
        try:
            return HighPricePolicy(normalized)
        except ValueError as exc:
            raise ValueError(
                f"unknown high-price policy: {policy!r} "
                f"(allowed: {[p.value for p in HighPricePolicy]})"
            ) from exc
    raise TypeError(
        f"policy must be HighPricePolicy / str / None, got {type(policy).__name__}"
    )


def compute_affordable_quantity(
    *,
    effective_per_symbol_cap_krw: int,
    price:                        float | int | None,
) -> int:
    """`floor(cap / price)` — 정수 매수 가능 수량.

    price <= 0 / None → 0. cap <= 0 → 0 (한도 없음 해석은 caller 책임 —
    본 헬퍼는 *cap 기반 정수 수량* 만 계산).
    """
    if price is None:
        return 0
    p = float(price)
    if p <= 0:
        return 0
    cap = int(effective_per_symbol_cap_krw)
    if cap <= 0:
        return 0
    return int(math.floor(cap / p))


def evaluate_high_price(
    *,
    action:                       str | None,
    symbol:                       str | None,
    price:                        float | int | None,
    effective_per_symbol_cap_krw: int,
    policy:                       HighPricePolicy | str | None = None,
) -> HighPriceCheckResult:
    """고가주 처리 정책 평가 — *advisory* verdict 반환.

    *고가주 판단 기준*: 종목당 투자금 `effective_per_symbol_cap_krw` 로 현재가
    `price` 기준 1주도 살 수 없는 경우 → `floor(cap / price) < 1`.

    Verdict 우선순위:
      1. action != BUY              → SKIP_NON_BUY
      2. price is None              → MISSING_PRICE
      3. price <= 0                 → INVALID_PRICE
      4. 1주 이상 가능 (정상)        → AFFORDABLE
      5. 고가주 + policy=EXCLUDE              → EXCLUDED   (default)
      6. 고가주 + policy=HOLD                  → HELD
      7. 고가주 + policy=INCREASE_BUDGET_HINT → BUDGET_HINT

    Args:
        action: 매매 의도. BUY 만 평가. 그 외 → SKIP_NON_BUY.
        symbol: 후보 종목 코드. None 허용 (verdict 영향 없음).
        price: 1주 가격 (KRW). None / 0 / 음수는 별도 verdict.
        effective_per_symbol_cap_krw: 종목당 투자금 한도 (KRW).
        policy: 고가주 처리 정책. None → default EXCLUDE.

    Returns:
        HighPriceCheckResult — verdict / reason_ko / suggested_min_cap_krw carry.

    Raises:
        ValueError: policy 가 알 수 없는 문자열일 때.
    """
    resolved_policy = _resolve_policy(policy)
    cap = int(effective_per_symbol_cap_krw)

    # 1. SKIP_NON_BUY — HOLD/SELL/EXIT 무관.
    if not _is_buy_action(action):
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.SKIP_NON_BUY,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            effective_per_symbol_cap_krw=cap,
            reason_ko=(
                "BUY 가 아닌 action — 고가주 정책 평가 무관 (청산 / 관망은 "
                "자유)."
            ),
        )

    # 2. price 누락.
    if price is None:
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.MISSING_PRICE,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=None,
            effective_per_symbol_cap_krw=cap,
            reason_ko="현재가가 없어 고가주 정책을 평가할 수 없습니다.",
            risk_flag="missing_price",
        )

    p = float(price)

    # 3. price <= 0 (이상값).
    if p <= 0:
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.INVALID_PRICE,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            reason_ko=(
                f"현재가 {p} 가 0 이하라 고가주 정책을 평가할 수 없습니다."
            ),
            risk_flag="invalid_price",
        )

    qty = compute_affordable_quantity(
        effective_per_symbol_cap_krw=cap, price=p,
    )

    # 4. 1주 이상 가능 — 정상 흐름.
    if qty >= 1:
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.AFFORDABLE,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            affordable_quantity=qty,
            is_high_price=False,
            reason_ko=(
                f"종목당 한도 {_format_krw(cap)}원으로 1주({_format_krw(p)}원) "
                f"이상 매수 가능 ({qty}주)."
            ),
        )

    # 5~7. 고가주 — 정책별 분기.
    # 사용자가 *증액해야 할 최소 한도* 안내 — 현재가 올림.
    suggested_min_cap = int(math.ceil(p))

    if resolved_policy is HighPricePolicy.EXCLUDE:
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.EXCLUDED,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            affordable_quantity=0,
            is_high_price=True,
            reason_ko="1주 가격이 투자한도 초과로 제외",
            risk_flag="high_price_excluded",
            suggested_min_cap_krw=suggested_min_cap,
        )

    if resolved_policy is HighPricePolicy.HOLD:
        return HighPriceCheckResult(
            verdict=HighPriceVerdict.HELD,
            policy=resolved_policy,
            symbol=symbol, action=action,
            price=p,
            effective_per_symbol_cap_krw=cap,
            affordable_quantity=0,
            is_high_price=True,
            reason_ko="1주 가격이 투자한도 초과로 보류",
            risk_flag="high_price_held",
            suggested_min_cap_krw=suggested_min_cap,
        )

    # INCREASE_BUDGET_HINT
    return HighPriceCheckResult(
        verdict=HighPriceVerdict.BUDGET_HINT,
        policy=resolved_policy,
        symbol=symbol, action=action,
        price=p,
        effective_per_symbol_cap_krw=cap,
        affordable_quantity=0,
        is_high_price=True,
        reason_ko="1주 가격이 투자한도 초과: 종목당 투자금 증액 필요",
        risk_flag="high_price_budget_hint",
        suggested_min_cap_krw=suggested_min_cap,
    )


__all__ = [
    "HighPricePolicy",
    "DEFAULT_HIGH_PRICE_POLICY",
    "HighPriceVerdict",
    "HighPriceCheckResult",
    "compute_affordable_quantity",
    "evaluate_high_price",
]
