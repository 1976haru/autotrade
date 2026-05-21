"""P-07: Paper 현금 잔고 (CapitalState) — 매수 가능 여부의 *실제* 자금 기준.

P-01 / P-02 / P-04 / P-06 가 *한도* (시드머니 / 종목당 cap / 1주 가격 vs cap /
고가주 정책) 를 본다면, 본 모듈은 *실제로 지금 남아 있는 Paper 현금* 을
관리한다. 두 개념은 **별개** — 종목당 투자 한도는 충분해도 누적 매수로 인해
현금이 부족하면 신규 BUY 는 차단되어야 한다.

예시 (사용자 요청서):
- 종목당 투자 한도: 1,000,000원
- 남은 Paper 현금: 300,000원
- 현재가: 100,000원, 요청 수량: 5주
- 필요 금액: 500,000원
- → 한도는 충분하지만 *현금 부족* 으로 BUY 차단.

본 모듈은 (1) *advisory* 사전 검사 (`check_buy_cash_sufficient`) + (2)
*상태 갱신* (`commit_buy` / `commit_sell`) 의 두 책임을 갖는다. 상태 변경은
*BUY 체결 또는 VirtualOrder 생성 확정 시점에만* 호출되어야 한다 — 차단된
BUY 는 절대 차감하지 않는다 (사용자 요청서 §10).

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (안전 flag 결합 0건)
- app.brokers / app.execution / app.kis_paper.engine import 0건
- CapitalStateSnapshot.is_order_signal / is_live_authorization = False 영구
- is_paper_only = True 영구
- RiskManager / PermissionGate 우회 0건 — 본 모듈은 *advisory cash 검사*
  + *paper 현금 상태 store* 만, 어떤 주문도 직접 발행하지 않음.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from typing import Any


_log = logging.getLogger("autotrade.paper_capital_state")


# ============================================================================
# Verdict
# ============================================================================


class CashCheckVerdict(StrEnum):
    """현금 잔고 검사 결과 라벨.

    값은 frontend / API / ledger / diagnostics 에 그대로 emit. caller 는
    verdict 와 함께 reason_ko 를 사용자에게 표시.
    """

    ALLOWED                  = "ALLOWED"
    INSUFFICIENT_PAPER_CASH  = "INSUFFICIENT_PAPER_CASH"
    INVALID_PRICE            = "INVALID_PRICE"          # price <= 0
    MISSING_PRICE            = "MISSING_PRICE"          # price is None
    INVALID_QUANTITY         = "INVALID_QUANTITY"       # quantity <= 0 or fractional
    SKIP_NON_BUY             = "SKIP_NON_BUY"           # action != BUY


# ============================================================================
# Result dataclass
# ============================================================================


@dataclass(frozen=True)
class CashCheckResult:
    """현금 잔고 검사 결과 — *advisory*, broker / route_order 호출 0건.

    `is_paper_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드).
    """

    verdict:               CashCheckVerdict
    symbol:                str | None       = None
    action:                str | None       = None
    price:                 float | None     = None
    quantity:              int              = 0
    required_krw:          int              = 0
    available_cash_krw:    int              = 0
    shortfall_krw:         int              = 0
    reason_ko:             str              = ""
    risk_flag:             str | None       = None
    metadata:              dict[str, Any]   = field(default_factory=dict)

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    is_paper_only:         bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("CashCheckResult.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("CashCheckResult.is_live_authorization must be False")
        if self.is_paper_only is not True:
            raise ValueError("CashCheckResult.is_paper_only must be True")
        if self.required_krw < 0:
            raise ValueError(
                f"required_krw must be >= 0, got {self.required_krw}"
            )
        if self.shortfall_krw < 0:
            raise ValueError(
                f"shortfall_krw must be >= 0, got {self.shortfall_krw}"
            )

    @property
    def is_allowed(self) -> bool:
        return self.verdict == CashCheckVerdict.ALLOWED

    @property
    def is_insufficient(self) -> bool:
        return self.verdict == CashCheckVerdict.INSUFFICIENT_PAPER_CASH

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict":               self.verdict.value,
            "symbol":                self.symbol,
            "action":                self.action,
            "price":                 self.price,
            "quantity":              int(self.quantity),
            "required_krw":          int(self.required_krw),
            "available_cash_krw":    int(self.available_cash_krw),
            "shortfall_krw":         int(self.shortfall_krw),
            "reason_ko":             self.reason_ko,
            "risk_flag":             self.risk_flag,
            "metadata":              dict(self.metadata),
            "is_allowed":            self.is_allowed,
            "is_insufficient":       self.is_insufficient,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_only":         self.is_paper_only,
        }


# ============================================================================
# Mutable state snapshot
# ============================================================================


@dataclass(frozen=True)
class CapitalStateSnapshot:
    """Paper 현금 잔고 read-only snapshot.

    `available_cash_krw` 는 *지금 신규 BUY 에 사용 가능* 한 KRW. 누적 BUY
    체결로 차감, SELL 체결로 가산. broker / 실거래 계좌와 *결합 0건*.
    """

    initial_cash_krw:    int
    available_cash_krw:  int
    invested_krw:        int
    realized_pnl_krw:    int
    buy_count:           int
    sell_count:          int
    last_event_at:       str | None = None

    is_order_signal:     bool = False
    is_live_authorization: bool = False
    is_paper_only:       bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("CapitalStateSnapshot.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError(
                "CapitalStateSnapshot.is_live_authorization must be False"
            )
        if self.is_paper_only is not True:
            raise ValueError(
                "CapitalStateSnapshot.is_paper_only must be True"
            )
        if self.available_cash_krw < 0:
            raise ValueError(
                f"available_cash_krw must be >= 0, got {self.available_cash_krw}"
            )
        if self.invested_krw < 0:
            raise ValueError(
                f"invested_krw must be >= 0, got {self.invested_krw}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_cash_krw":      int(self.initial_cash_krw),
            "available_cash_krw":    int(self.available_cash_krw),
            "invested_krw":          int(self.invested_krw),
            "realized_pnl_krw":      int(self.realized_pnl_krw),
            "buy_count":             int(self.buy_count),
            "sell_count":            int(self.sell_count),
            "last_event_at":         self.last_event_at,
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_only":         self.is_paper_only,
        }


# ============================================================================
# Errors
# ============================================================================


class CapitalStateError(RuntimeError):
    """잘못된 상태 변경 시도 — 운영자가 인지하도록 raise."""


class InsufficientPaperCashError(CapitalStateError):
    """commit_buy 시점에 현금이 부족 — *advisory 사전 검사를 통과하지 않은*
    BUY 가 commit 되려고 한 경우만 발생 (정상 흐름에서는 미발생)."""


# ============================================================================
# Helpers
# ============================================================================


_BUY_TOKENS = frozenset({"buy", "open", "open_long", "long", "enter", "entry"})
_SELL_TOKENS = frozenset({
    "sell", "close", "close_long", "exit", "sell_to_close",
})


def _is_buy_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _BUY_TOKENS


def _is_sell_action(action: str | None) -> bool:
    return (action or "").strip().lower() in _SELL_TOKENS


def _format_krw(amount: int | float) -> str:
    return f"{int(amount):,}"


def _is_positive_integer_quantity(qty: int | float) -> bool:
    """qty 가 *정수 ≥ 1* 인지. bool 거부."""
    if isinstance(qty, bool):
        return False
    if isinstance(qty, int):
        return qty >= 1
    if isinstance(qty, float):
        if qty != qty or qty in (float("inf"), float("-inf")):  # NaN/inf
            return False
        return qty >= 1 and float(qty).is_integer()
    return False


def compute_required_krw(
    *,
    price:    float | int | None,
    quantity: int | float,
) -> int:
    """필요 금액 = floor(price × quantity). price/qty 이상값 → 0.

    floor 사용 — 정수 KRW 만 carry (한국 시장은 1원 단위).
    """
    if price is None:
        return 0
    p = float(price)
    if p <= 0:
        return 0
    if not isinstance(quantity, (int, float)) or isinstance(quantity, bool):
        return 0
    q = float(quantity)
    if q <= 0 or q != q or q in (float("inf"), float("-inf")):
        return 0
    return int(p * q)


# ============================================================================
# Pure check function (stateless — caller 가 cash 를 명시 주입)
# ============================================================================


def check_buy_cash_sufficient(
    *,
    action:             str | None,
    symbol:             str | None,
    price:              float | int | None,
    quantity:           int | float,
    available_cash_krw: int,
) -> CashCheckResult:
    """BUY 전 현금 충분성 사전 검사 — *advisory* verdict 반환.

    실행 순서 (verdict 우선순위 — 첫 매칭 verdict 반환):
      1. action 이 BUY 가 아님          → SKIP_NON_BUY
      2. price is None                   → MISSING_PRICE
      3. price <= 0                      → INVALID_PRICE
      4. quantity 가 정수 ≥ 1 이 아님    → INVALID_QUANTITY
      5. required > available_cash       → INSUFFICIENT_PAPER_CASH
      6. 그 외                            → ALLOWED

    Args:
        action: 매매 의도. BUY 만 검사. 그 외 → SKIP_NON_BUY.
        symbol: 후보 종목 코드. None 허용.
        price: 1주 가격 (KRW).
        quantity: 요청 수량 (정수 ≥ 1).
        available_cash_krw: 현재 *남은 Paper 현금*. 음수는 0 으로 clamp.

    Returns:
        CashCheckResult. broker / route_order 호출 0건.
    """
    cash = max(int(available_cash_krw), 0)

    # 1. SKIP_NON_BUY — SELL / HOLD / EXIT 무관.
    if not _is_buy_action(action):
        return CashCheckResult(
            verdict=CashCheckVerdict.SKIP_NON_BUY,
            symbol=symbol, action=action,
            price=float(price) if price is not None else None,
            quantity=int(quantity) if _is_positive_integer_quantity(quantity) else 0,
            required_krw=0,
            available_cash_krw=cash,
            reason_ko=(
                "BUY 가 아닌 action — 현금 잔고 검사 무관 (청산 / 관망은 "
                "현금 부족으로 차단되지 않습니다)."
            ),
        )

    # 2. price 누락.
    if price is None:
        return CashCheckResult(
            verdict=CashCheckVerdict.MISSING_PRICE,
            symbol=symbol, action=action,
            price=None, quantity=0,
            required_krw=0, available_cash_krw=cash,
            reason_ko="현재가가 없어 현금 잔고를 평가할 수 없습니다.",
            risk_flag="missing_price",
        )

    p = float(price)

    # 3. price <= 0.
    if p <= 0:
        return CashCheckResult(
            verdict=CashCheckVerdict.INVALID_PRICE,
            symbol=symbol, action=action,
            price=p, quantity=0,
            required_krw=0, available_cash_krw=cash,
            reason_ko=(
                f"현재가 {p} 가 0 이하라 현금 잔고를 평가할 수 없습니다."
            ),
            risk_flag="invalid_price",
        )

    # 4. quantity 가 정수 ≥ 1 이 아님.
    if not _is_positive_integer_quantity(quantity):
        return CashCheckResult(
            verdict=CashCheckVerdict.INVALID_QUANTITY,
            symbol=symbol, action=action,
            price=p, quantity=0,
            required_krw=0, available_cash_krw=cash,
            reason_ko=(
                f"매수 수량 {quantity!r} 가 유효한 정수(≥1)가 아닙니다."
            ),
            risk_flag="invalid_quantity",
        )

    qty_int = int(quantity)
    required = compute_required_krw(price=p, quantity=qty_int)

    # 5. required > cash → 현금 부족.
    if required > cash:
        shortfall = required - cash
        return CashCheckResult(
            verdict=CashCheckVerdict.INSUFFICIENT_PAPER_CASH,
            symbol=symbol, action=action,
            price=p, quantity=qty_int,
            required_krw=required,
            available_cash_krw=cash,
            shortfall_krw=shortfall,
            reason_ko=(
                "남은 Paper 현금이 부족하여 매수 차단 — 필요 금액 "
                f"{_format_krw(required)}원 > 남은 Paper 현금 "
                f"{_format_krw(cash)}원 (부족 {_format_krw(shortfall)}원)."
            ),
            risk_flag="insufficient_paper_cash",
        )

    # 6. 통과.
    return CashCheckResult(
        verdict=CashCheckVerdict.ALLOWED,
        symbol=symbol, action=action,
        price=p, quantity=qty_int,
        required_krw=required,
        available_cash_krw=cash,
        shortfall_krw=0,
        reason_ko=(
            f"Paper 현금 잔고 충분 — 필요 금액 {_format_krw(required)}원 "
            f"≤ 남은 Paper 현금 {_format_krw(cash)}원."
        ),
    )


# ============================================================================
# Mutable CapitalState (process singleton)
# ============================================================================


class CapitalState:
    """Paper 계좌의 *현금 잔고 mutable store* — thread-safe.

    상태 변경은 (1) `reset(initial_cash_krw)`, (2) `commit_buy(price, qty,
    symbol=None)`, (3) `commit_sell(price, qty, symbol=None,
    realized_pnl_krw=0)` 의 *3 메서드* 에서만 가능. 그 외 어떤 코드도
    `available_cash_krw` 를 직접 변경할 수 없다.

    `commit_buy` 는 *advisory 사전 검사를 caller 가 통과한 후에만* 호출되어야
    한다 — 본 메서드 내부에서도 한 번 더 검증해, 통과하지 않으면
    `InsufficientPaperCashError` 로 raise (마지막 backstop).

    `commit_sell` 은 *항상 현금을 증가* (가산) — 매도는 현금 부족으로 차단
    되지 않으므로 추가 검증 없음. realized_pnl_krw 는 음수도 허용 (손실
    실현).
    """

    def __init__(self, *, initial_cash_krw: int) -> None:
        if int(initial_cash_krw) < 0:
            raise ValueError(
                f"initial_cash_krw must be >= 0, got {initial_cash_krw}"
            )
        self._lock = threading.Lock()
        self._initial_cash_krw: int = int(initial_cash_krw)
        self._available_cash_krw: int = int(initial_cash_krw)
        self._invested_krw: int = 0
        self._realized_pnl_krw: int = 0
        self._buy_count: int = 0
        self._sell_count: int = 0
        self._last_event_at: str | None = None

    def reset(self, *, initial_cash_krw: int) -> CapitalStateSnapshot:
        """전체 상태 리셋 (테스트 / 운영자 명시 초기화)."""
        if int(initial_cash_krw) < 0:
            raise ValueError(
                f"initial_cash_krw must be >= 0, got {initial_cash_krw}"
            )
        with self._lock:
            self._initial_cash_krw = int(initial_cash_krw)
            self._available_cash_krw = int(initial_cash_krw)
            self._invested_krw = 0
            self._realized_pnl_krw = 0
            self._buy_count = 0
            self._sell_count = 0
            self._last_event_at = None
            return self._snapshot_unlocked()

    def snapshot(self) -> CapitalStateSnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    @property
    def available_cash_krw(self) -> int:
        with self._lock:
            return self._available_cash_krw

    def precheck_buy(
        self,
        *,
        action:   str | None,
        symbol:   str | None,
        price:    float | int | None,
        quantity: int | float,
    ) -> CashCheckResult:
        """현재 store 의 현금 기준으로 `check_buy_cash_sufficient` 호출.

        *상태 변경 0건* — 단순 read + advisory 결과 반환. 본 메서드 호출
        자체가 reservation 을 만들지 않는다.
        """
        with self._lock:
            cash = self._available_cash_krw
        return check_buy_cash_sufficient(
            action=action, symbol=symbol, price=price, quantity=quantity,
            available_cash_krw=cash,
        )

    def commit_buy(
        self,
        *,
        symbol:   str | None,
        price:    float | int,
        quantity: int,
        event_at: str | None = None,
    ) -> CapitalStateSnapshot:
        """BUY 체결 확정 — 현금 차감 + invested 증가.

        본 메서드는 *advisory precheck 를 통과한 BUY 만* 호출해야 한다 —
        그렇지 않은 경우 마지막 backstop 으로 `InsufficientPaperCashError`
        raise (state 변경 없음).
        """
        if price is None:
            raise ValueError("commit_buy: price is required")
        p = float(price)
        if p <= 0:
            raise ValueError(f"commit_buy: price must be > 0, got {p}")
        if not _is_positive_integer_quantity(quantity):
            raise ValueError(
                f"commit_buy: quantity must be int >= 1, got {quantity!r}"
            )
        q = int(quantity)
        required = int(p * q)
        with self._lock:
            if required > self._available_cash_krw:
                # advisory 사전 검사를 caller 가 우회하려고 시도한 경우.
                # state 변경 없이 raise — backstop.
                raise InsufficientPaperCashError(
                    f"insufficient_paper_cash: required {required:,} KRW > "
                    f"available {self._available_cash_krw:,} KRW"
                )
            self._available_cash_krw -= required
            self._invested_krw += required
            self._buy_count += 1
            if event_at is not None:
                self._last_event_at = event_at
            _log.info(
                "[paper-capital] commit_buy symbol=%s qty=%d price=%g "
                "required=%d cash_after=%d invested_after=%d",
                symbol, q, p, required,
                self._available_cash_krw, self._invested_krw,
            )
            return self._snapshot_unlocked()

    def commit_sell(
        self,
        *,
        symbol:           str | None,
        price:            float | int,
        quantity:         int,
        cost_basis_krw:   int = 0,
        event_at:         str | None = None,
    ) -> CapitalStateSnapshot:
        """SELL 체결 확정 — 현금 증가 + invested 감소 (cost_basis 만큼) +
        realized_pnl 누적.

        cost_basis_krw 는 *매도한 수량의 최초 매수가* — caller (PaperLedger /
        PositionEngine) 가 평균단가 등을 계산해 전달. 0 이면 (운영자 / 테스트
        편의용) invested 감소를 *동일 proceeds* 로 가정.

        매도는 현금 부족으로 차단되지 않는다 — 추가 검증 없음.
        """
        if price is None:
            raise ValueError("commit_sell: price is required")
        p = float(price)
        if p <= 0:
            raise ValueError(f"commit_sell: price must be > 0, got {p}")
        if not _is_positive_integer_quantity(quantity):
            raise ValueError(
                f"commit_sell: quantity must be int >= 1, got {quantity!r}"
            )
        q = int(quantity)
        proceeds = int(p * q)
        cost = int(cost_basis_krw) if cost_basis_krw > 0 else proceeds
        realized = proceeds - cost
        with self._lock:
            self._available_cash_krw += proceeds
            self._invested_krw = max(0, self._invested_krw - cost)
            self._realized_pnl_krw += realized
            self._sell_count += 1
            if event_at is not None:
                self._last_event_at = event_at
            _log.info(
                "[paper-capital] commit_sell symbol=%s qty=%d price=%g "
                "proceeds=%d cost_basis=%d realized=%d cash_after=%d "
                "invested_after=%d",
                symbol, q, p, proceeds, cost, realized,
                self._available_cash_krw, self._invested_krw,
            )
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> CapitalStateSnapshot:
        return CapitalStateSnapshot(
            initial_cash_krw=self._initial_cash_krw,
            available_cash_krw=self._available_cash_krw,
            invested_krw=self._invested_krw,
            realized_pnl_krw=self._realized_pnl_krw,
            buy_count=self._buy_count,
            sell_count=self._sell_count,
            last_event_at=self._last_event_at,
        )


# ============================================================================
# Process-wide singleton bound to PaperCapitalConfig.initial_cash
# ============================================================================


@lru_cache
def _get_state_singleton() -> CapitalState:
    """최초 호출 시 PaperCapitalConfig 의 initial_cash 로 초기화."""
    try:
        from app.auto_paper.capital_config import get_paper_capital_config
        cfg = get_paper_capital_config()
        initial = int(cfg.initial_cash)
    except Exception:  # noqa: BLE001 — config 미가용 시 안전 default.
        initial = 10_000_000
    return CapitalState(initial_cash_krw=initial)


def get_capital_state() -> CapitalState:
    """프로세스-wide singleton 반환. lru_cache 로 1회 인스턴스화."""
    return _get_state_singleton()


def reset_capital_state_for_tests(initial_cash_krw: int | None = None) -> None:
    """테스트 fixture 용 — singleton 의 상태 (또는 인스턴스) 초기화."""
    if initial_cash_krw is None:
        try:
            from app.auto_paper.capital_config import get_paper_capital_config
            initial_cash_krw = int(get_paper_capital_config().initial_cash)
        except Exception:  # noqa: BLE001
            initial_cash_krw = 10_000_000
    get_capital_state().reset(initial_cash_krw=int(initial_cash_krw))


__all__ = [
    "CashCheckVerdict",
    "CashCheckResult",
    "CapitalStateSnapshot",
    "CapitalState",
    "CapitalStateError",
    "InsufficientPaperCashError",
    "compute_required_krw",
    "check_buy_cash_sufficient",
    "get_capital_state",
    "reset_capital_state_for_tests",
]
