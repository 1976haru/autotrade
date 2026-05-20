"""P-01: Paper 모의매매 초기 시드머니 설정.

AI 가 매수/매도 판단을 하려면 "가상 자본이 얼마인지" 기준이 필요하다.
본 모듈은 *Paper 전용* 초기 시드머니를 관리하는 단일 진실:

- 허용값 3종: 10,000,000 / 30,000,000 / 50,000,000 KRW
- 기본값:     10,000,000 KRW
- 통화:       KRW 한정 (P-01 시점)
- 영구 저장:   in-memory (P-16 에서 DB / settings 영구화로 확장)

본 모듈은 *실전 계좌와 완전 분리* 된다 — 어떤 broker / OrderExecutor /
route_order / KIS client / 실거래 주문 한도와도 *결합 0건*. `is_paper_only`
영구 True 불변.

CLAUDE.md 절대 원칙 (본 모듈은 다음을 *호출/import 하지 않는다*):
- `app.brokers.*` (broker adapter)
- `app.execution.executor` / `app.execution.order_router`
- `app.kis_paper.engine` / `KisClient` / `place_order`
- 외부 HTTP (httpx / requests) / AI SDK (anthropic / openai)
- `app.core.config.get_settings` (안전 flag 가 흘러와 본 모듈 결정에 영향 주지
  않도록 — Paper 시드머니는 *오직 caller 가 명시* 주입)

설계 원칙:
- caller 가 *허용되지 않은 금액* 을 set 하면 `InvalidPaperCapitalError` 로
  fail-fast — silent fallback 도 옵션으로 노출하지만 *기본은 raise*.
- 본 모듈은 *읽기 다중 / 쓰기 단일* — store 인스턴스 1개 (process singleton).
- API endpoint 에서 호출하는 함수 (`get_paper_capital_config` /
  `set_paper_capital_config`) 가 *thread-safe* (Lock 보호).
- `PaperCapitalConfig.is_paper_only=True` / `is_live_authorization=False` 영구
  불변 (dataclass `__post_init__` ValueError 가드).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_log = logging.getLogger("autotrade.paper_capital")


# ============================================================================
# 상수 — 허용 시드머니 옵션 (사용자 요청서 §2)
# ============================================================================

DEFAULT_PAPER_INITIAL_CASH: int = 10_000_000  # 1,000만원

# 사용자가 *Paper 전용* 으로 선택 가능한 시드머니. *immutable tuple* — 후속
# PR 에서 옵션 추가는 별도 마이그레이션 (예: P-16 영구화) 후에만 허용.
ALLOWED_PAPER_INITIAL_CASH: tuple[int, ...] = (
    10_000_000,   # 1,000만원
    30_000_000,   # 3,000만원
    50_000_000,   # 5,000만원
)

# 통화 — P-01 시점 KRW 한정. 다른 통화 (USD 등) 는 별도 PR.
PAPER_CAPITAL_CURRENCY: str = "KRW"


# ============================================================================
# 예외
# ============================================================================


class InvalidPaperCapitalError(ValueError):
    """허용되지 않은 시드머니 값을 set 하려고 시도.

    caller 는 `ALLOWED_PAPER_INITIAL_CASH` 안의 값만 set 할 수 있다.
    `set_paper_capital_config(..., fallback_to_default=True)` 로 호출하면
    본 예외를 raise 하는 대신 default 로 fallback (응답에 `fallback_used=True`
    carry).
    """


# ============================================================================
# Data class
# ============================================================================


@dataclass(frozen=True)
class PaperCapitalConfig:
    """Paper 시드머니 설정 read-only 스냅샷.

    *실전 계좌와 무관* — `is_paper_only=True` / `is_live_authorization=False`
    영구. 본 dataclass 는 어떤 broker / live route 와도 결합되지 않는다.
    """

    initial_cash:                int
    allowed_initial_cash_options: tuple[int, ...]
    currency:                    str       = PAPER_CAPITAL_CURRENCY
    is_paper_only:               bool      = True
    is_live_authorization:       bool      = False
    updated_at:                  str       = ""    # ISO 8601 UTC, 기본 빈 값

    def __post_init__(self) -> None:
        # 절대 invariant — 본 dataclass 가 실거래 권한 객체로 둔갑하지 않도록.
        if self.is_paper_only is not True:
            raise ValueError(
                "PaperCapitalConfig.is_paper_only must be True — "
                "this config is Paper/Virtual only, never live broker."
            )
        if self.is_live_authorization is not False:
            raise ValueError(
                "PaperCapitalConfig.is_live_authorization must be False — "
                "Paper seed money never authorizes live trading."
            )
        if self.currency != PAPER_CAPITAL_CURRENCY:
            raise ValueError(
                f"PaperCapitalConfig.currency must be {PAPER_CAPITAL_CURRENCY!r} "
                f"(P-01 한정), got {self.currency!r}"
            )
        # 허용값 검증 — *반드시* allowed 안에 있어야 한다.
        if self.initial_cash not in self.allowed_initial_cash_options:
            raise ValueError(
                f"PaperCapitalConfig.initial_cash={self.initial_cash:,} 가 "
                f"allowed_initial_cash_options {self.allowed_initial_cash_options} "
                "안에 없음 — caller 가 검증 우회 시도"
            )
        # allowed_initial_cash_options 자체가 비어 있거나 잘못된 타입이면 reject.
        if not self.allowed_initial_cash_options:
            raise ValueError("allowed_initial_cash_options must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_cash":                  int(self.initial_cash),
            "allowed_initial_cash_options":  list(self.allowed_initial_cash_options),
            "currency":                      self.currency,
            "is_paper_only":                 self.is_paper_only,
            "is_live_authorization":         self.is_live_authorization,
            "updated_at":                    self.updated_at,
        }


# ============================================================================
# In-memory store (process singleton, thread-safe)
# ============================================================================


class _PaperCapitalStore:
    """Process-singleton in-memory store. P-16 에서 DB / settings 영구화 예정.

    *상태 변경* 은 set_initial_cash 한 곳에서만 — 그 외 어떤 코드도 본 객체
    의 필드를 직접 mutate 하지 않는다 (테스트 lock).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._initial_cash: int = DEFAULT_PAPER_INITIAL_CASH
        self._updated_at: str = ""

    def snapshot(self) -> PaperCapitalConfig:
        with self._lock:
            return PaperCapitalConfig(
                initial_cash=self._initial_cash,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
                currency=PAPER_CAPITAL_CURRENCY,
                is_paper_only=True,
                is_live_authorization=False,
                updated_at=self._updated_at,
            )

    def set_initial_cash(
        self,
        value: int,
        *,
        fallback_to_default: bool = False,
    ) -> tuple[PaperCapitalConfig, bool]:
        """`value` 를 set. 허용되지 않으면 default 로 fallback (옵션).

        Returns:
            (new_config, fallback_used) — fallback_used=True 면 *입력값이
            허용되지 않아 default 로 대체* 됐음을 의미.

        Raises:
            InvalidPaperCapitalError: `fallback_to_default=False` 인데 허용되지
                않은 값이 들어옴.
        """
        target = int(value)
        fallback_used = False
        if target not in ALLOWED_PAPER_INITIAL_CASH:
            if fallback_to_default:
                _log.warning(
                    "[paper-capital] %d 가 허용 옵션 %s 외 — default %d 로 fallback",
                    target, ALLOWED_PAPER_INITIAL_CASH, DEFAULT_PAPER_INITIAL_CASH,
                )
                target = DEFAULT_PAPER_INITIAL_CASH
                fallback_used = True
            else:
                raise InvalidPaperCapitalError(
                    f"initial_cash={target:,} 가 허용 옵션 "
                    f"{ALLOWED_PAPER_INITIAL_CASH} 외 — 거부."
                )
        with self._lock:
            self._initial_cash = target
            self._updated_at = datetime.now(timezone.utc).isoformat()
        snap = self.snapshot()
        _log.info(
            "[paper-capital] initial_cash set → %d KRW (fallback=%s)",
            target, fallback_used,
        )
        return snap, fallback_used

    def reset_for_tests(self) -> None:
        """테스트 격리용 — process state 를 default 로 reset."""
        with self._lock:
            self._initial_cash = DEFAULT_PAPER_INITIAL_CASH
            self._updated_at = ""


_store: _PaperCapitalStore = _PaperCapitalStore()


# ============================================================================
# Public API
# ============================================================================


def get_paper_capital_config() -> PaperCapitalConfig:
    """현재 Paper 시드머니 설정 스냅샷."""
    return _store.snapshot()


def set_paper_capital_config(
    initial_cash: int,
    *,
    fallback_to_default: bool = False,
) -> tuple[PaperCapitalConfig, bool]:
    """Paper 시드머니 설정 변경.

    `fallback_to_default=True` 면 허용되지 않은 값이 들어와도 default 로 대체.
    `False` (기본) 면 `InvalidPaperCapitalError` 로 즉시 raise.

    본 함수는 broker / OrderExecutor / route_order / KIS 어떤 호출도 *하지
    않는다* — 단순히 in-memory 값을 갱신.
    """
    return _store.set_initial_cash(initial_cash, fallback_to_default=fallback_to_default)


def reset_paper_capital_for_tests() -> None:
    """테스트 격리용 — store 를 default 로 복귀."""
    _store.reset_for_tests()


def is_allowed_initial_cash(value: int) -> bool:
    """*허용 옵션* 안에 있는지 검사 — UI 사전 validation 용 helper."""
    try:
        return int(value) in ALLOWED_PAPER_INITIAL_CASH
    except (TypeError, ValueError):
        return False


__all__ = [
    "DEFAULT_PAPER_INITIAL_CASH",
    "ALLOWED_PAPER_INITIAL_CASH",
    "PAPER_CAPITAL_CURRENCY",
    "InvalidPaperCapitalError",
    "PaperCapitalConfig",
    "get_paper_capital_config",
    "set_paper_capital_config",
    "is_allowed_initial_cash",
    "reset_paper_capital_for_tests",
]
