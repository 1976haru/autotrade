"""P-01 + P-02: Paper 모의매매 자본 + 종목당 투자금 설정.

AI 가 매수/매도 판단을 하려면 "가상 자본이 얼마인지" + "한 종목에 최대 얼마
까지 투자할지" 기준이 필요하다. 본 모듈은 *Paper 전용* 자본 정책을 관리하는
단일 진실:

P-01 — 초기 시드머니:
- 허용값 3종: 10,000,000 / 30,000,000 / 50,000,000 KRW
- 기본값:     10,000,000 KRW
- 통화:       KRW 한정

P-02 — 종목당 최대 투자금:
- 모드 2종 (PerSymbolAllocationMode):
  * FIXED_KRW       — 절대금액 (1,000,000 / 2,000,000 KRW)
  * PCT_OF_EQUITY   — 시드머니 대비 비율 (0.10 = 10%)
- 기본 모드:        FIXED_KRW
- 기본 절대값:       1,000,000 KRW
- 기본 비율값:       0.10 (10%)
- effective_per_symbol_cap_krw 계산:
  * FIXED_KRW       → per_symbol_max_krw
  * PCT_OF_EQUITY   → floor(initial_cash * per_symbol_max_pct)

P-16 — 영구 저장:
- in-memory (P-16 에서 DB / settings 영구화로 확장)

본 모듈은 *실전 계좌와 완전 분리* 된다 — 어떤 broker / OrderExecutor /
route_order / KIS client / 실거래 주문 한도와도 *결합 0건*. `is_paper_only`
영구 True 불변. 종목당 투자금은 *Paper sizing 계산 입력* 일 뿐 실전 주문
한도가 아님.

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
from enum import StrEnum
from typing import Any

_log = logging.getLogger("autotrade.paper_capital")


# ============================================================================
# 상수 — 허용 시드머니 옵션 (P-01)
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
# 상수 — 종목당 최대 투자금 옵션 (P-02)
# ============================================================================


class PerSymbolAllocationMode(StrEnum):
    """종목당 한도 계산 방식.

    값은 frontend / API payload 에 그대로 emit. caller 가 mode 와 함께
    *해당 mode 의 값* 만 제공하면 됨.
    """
    FIXED_KRW     = "FIXED_KRW"      # 절대 KRW 한도 (per_symbol_max_krw)
    PCT_OF_EQUITY = "PCT_OF_EQUITY"  # 시드머니 대비 비율 (per_symbol_max_pct)


# 종목당 한도 (절대 KRW) — 사용자 요청서 §3.
DEFAULT_PER_SYMBOL_MAX_KRW: int = 1_000_000  # 100만원
ALLOWED_PER_SYMBOL_MAX_KRW: tuple[int, ...] = (
    1_000_000,   # 100만원 (기본값)
    2_000_000,   # 200만원
)

# 종목당 한도 (시드머니 대비 비율) — 0.10 = 10%.
DEFAULT_PER_SYMBOL_MAX_PCT: float = 0.10  # 10%
ALLOWED_PER_SYMBOL_MAX_PCT: tuple[float, ...] = (
    0.10,        # 10% (기본값)
)

# 본 PR 시점 default mode — 절대금액 (사용자 요청 §3 기본값).
DEFAULT_PER_SYMBOL_MODE: PerSymbolAllocationMode = PerSymbolAllocationMode.FIXED_KRW


# ============================================================================
# 상수 — 최대 동시 보유 종목 수 (P-03)
# ============================================================================

DEFAULT_MAX_CONCURRENT_POSITIONS: int = 3

# 사용자가 *Paper 전용* 으로 선택 가능한 최대 동시 보유 종목 수.
# 과도한 분산 / 과도한 동시 진입 / 자금 과다 사용 방지용 안전장치.
ALLOWED_MAX_CONCURRENT_POSITIONS: tuple[int, ...] = (3, 5, 10)


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


class InvalidPerSymbolAllocationError(ValueError):
    """허용되지 않은 종목당 한도 (mode / krw / pct) 를 set 하려고 시도.

    `set_per_symbol_allocation(..., fallback_to_default=True)` 로 호출하면
    default 로 fallback. 본 예외는 *Paper 정책 위반* 만 의미 — 실거래 한도와
    무관.
    """


class InvalidMaxConcurrentPositionsError(ValueError):
    """허용되지 않은 최대 동시 보유 종목 수를 set 하려고 시도.

    `set_max_concurrent_positions(..., fallback_to_default=True)` 면 default
    로 fallback. 본 예외는 *Paper 정책 위반* 만 의미 — 실거래 한도와 무관.
    """


# ============================================================================
# 종목당 effective cap 계산 — pure helper, broker / DB 0건
# ============================================================================


def compute_effective_per_symbol_cap_krw(
    *,
    mode: PerSymbolAllocationMode,
    per_symbol_max_krw: int,
    per_symbol_max_pct: float,
    initial_cash: int,
) -> int:
    """현재 mode / 값 / 시드머니로 *effective* 종목당 한도 (KRW) 계산.

    FIXED_KRW         → per_symbol_max_krw 그대로
    PCT_OF_EQUITY     → floor(initial_cash * per_symbol_max_pct)

    본 함수는 *순수 함수* — DB / broker / settings 호출 0건.
    """
    if mode == PerSymbolAllocationMode.FIXED_KRW:
        return int(per_symbol_max_krw)
    if mode == PerSymbolAllocationMode.PCT_OF_EQUITY:
        return int(initial_cash * per_symbol_max_pct)
    raise ValueError(f"unknown per-symbol allocation mode: {mode!r}")


# ============================================================================
# Data class
# ============================================================================


@dataclass(frozen=True)
class PaperCapitalConfig:
    """Paper 시드머니 + 종목당 한도 설정 read-only 스냅샷 (P-01 + P-02).

    *실전 계좌와 무관* — `is_paper_only=True` / `is_live_authorization=False`
    영구. 본 dataclass 는 어떤 broker / live route 와도 결합되지 않는다.

    P-02 신규 필드:
      - per_symbol_mode:                  FIXED_KRW | PCT_OF_EQUITY
      - per_symbol_max_krw:               절대 KRW (FIXED_KRW 모드 기준값)
      - per_symbol_max_pct:               비율 (PCT_OF_EQUITY 모드 기준값)
      - effective_per_symbol_cap_krw:     mode 에 따라 산정된 *실제 적용* KRW 한도
      - allowed_per_symbol_max_krw_options: UI / API 표시용 허용 옵션
      - allowed_per_symbol_max_pct_options: UI / API 표시용 허용 옵션
    """

    initial_cash:                int
    allowed_initial_cash_options: tuple[int, ...]

    # P-02 — 종목당 한도.
    per_symbol_mode:                       PerSymbolAllocationMode = DEFAULT_PER_SYMBOL_MODE
    per_symbol_max_krw:                    int                     = DEFAULT_PER_SYMBOL_MAX_KRW
    per_symbol_max_pct:                    float                   = DEFAULT_PER_SYMBOL_MAX_PCT
    allowed_per_symbol_max_krw_options:    tuple[int, ...]         = ALLOWED_PER_SYMBOL_MAX_KRW
    allowed_per_symbol_max_pct_options:    tuple[float, ...]       = ALLOWED_PER_SYMBOL_MAX_PCT

    # P-03 — 최대 동시 보유 종목 수.
    max_concurrent_positions:              int                     = DEFAULT_MAX_CONCURRENT_POSITIONS
    allowed_max_concurrent_positions_options: tuple[int, ...]      = ALLOWED_MAX_CONCURRENT_POSITIONS

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

        # P-02 invariant — per_symbol 값 검증.
        if self.per_symbol_max_krw not in self.allowed_per_symbol_max_krw_options:
            raise ValueError(
                f"per_symbol_max_krw={self.per_symbol_max_krw:,} 가 "
                f"allowed {self.allowed_per_symbol_max_krw_options} 외 — 거부"
            )
        if self.per_symbol_max_pct not in self.allowed_per_symbol_max_pct_options:
            raise ValueError(
                f"per_symbol_max_pct={self.per_symbol_max_pct} 가 "
                f"allowed {self.allowed_per_symbol_max_pct_options} 외 — 거부"
            )
        if not isinstance(self.per_symbol_mode, PerSymbolAllocationMode):
            raise ValueError(
                f"per_symbol_mode must be PerSymbolAllocationMode, "
                f"got {type(self.per_symbol_mode).__name__}"
            )

        # P-03 invariant — max_concurrent_positions 검증.
        if self.max_concurrent_positions not in self.allowed_max_concurrent_positions_options:
            raise ValueError(
                f"max_concurrent_positions={self.max_concurrent_positions} 가 "
                f"allowed {self.allowed_max_concurrent_positions_options} 외 — 거부"
            )

    @property
    def effective_per_symbol_cap_krw(self) -> int:
        """현재 mode + 값 으로 산정한 *실제 적용* 종목당 한도 (KRW).

        FIXED_KRW       → per_symbol_max_krw 그대로.
        PCT_OF_EQUITY   → floor(initial_cash * per_symbol_max_pct).
        """
        return compute_effective_per_symbol_cap_krw(
            mode=self.per_symbol_mode,
            per_symbol_max_krw=self.per_symbol_max_krw,
            per_symbol_max_pct=self.per_symbol_max_pct,
            initial_cash=self.initial_cash,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_cash":                  int(self.initial_cash),
            "allowed_initial_cash_options":  list(self.allowed_initial_cash_options),
            # P-02
            "per_symbol_mode":                    self.per_symbol_mode.value,
            "per_symbol_max_krw":                 int(self.per_symbol_max_krw),
            "per_symbol_max_pct":                 float(self.per_symbol_max_pct),
            "effective_per_symbol_cap_krw":       int(self.effective_per_symbol_cap_krw),
            "allowed_per_symbol_max_krw_options": list(self.allowed_per_symbol_max_krw_options),
            "allowed_per_symbol_max_pct_options": list(self.allowed_per_symbol_max_pct_options),
            # P-03
            "max_concurrent_positions":              int(self.max_concurrent_positions),
            "allowed_max_concurrent_positions_options": list(self.allowed_max_concurrent_positions_options),

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
        # P-02 — 종목당 한도 store.
        self._per_symbol_mode: PerSymbolAllocationMode = DEFAULT_PER_SYMBOL_MODE
        self._per_symbol_max_krw: int = DEFAULT_PER_SYMBOL_MAX_KRW
        self._per_symbol_max_pct: float = DEFAULT_PER_SYMBOL_MAX_PCT
        # P-03 — 최대 동시 보유 종목 수 store.
        self._max_concurrent_positions: int = DEFAULT_MAX_CONCURRENT_POSITIONS
        self._updated_at: str = ""

    def snapshot(self) -> PaperCapitalConfig:
        with self._lock:
            return PaperCapitalConfig(
                initial_cash=self._initial_cash,
                allowed_initial_cash_options=ALLOWED_PAPER_INITIAL_CASH,
                per_symbol_mode=self._per_symbol_mode,
                per_symbol_max_krw=self._per_symbol_max_krw,
                per_symbol_max_pct=self._per_symbol_max_pct,
                allowed_per_symbol_max_krw_options=ALLOWED_PER_SYMBOL_MAX_KRW,
                allowed_per_symbol_max_pct_options=ALLOWED_PER_SYMBOL_MAX_PCT,
                max_concurrent_positions=self._max_concurrent_positions,
                allowed_max_concurrent_positions_options=ALLOWED_MAX_CONCURRENT_POSITIONS,
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

    def set_per_symbol_allocation(
        self,
        *,
        mode: PerSymbolAllocationMode | str | None = None,
        per_symbol_max_krw: int | None = None,
        per_symbol_max_pct: float | None = None,
        fallback_to_default: bool = False,
    ) -> tuple[PaperCapitalConfig, bool]:
        """P-02: 종목당 한도 설정 변경.

        세 필드 (mode / krw / pct) 는 *부분 업데이트* 가능 — None 으로 둔 필드
        는 현재 값 유지. 허용되지 않으면 fallback 옵션에 따라 default 로 대체
        하거나 InvalidPerSymbolAllocationError 로 raise.

        Returns:
            (new_config, fallback_used). fallback_used=True 면 1개 이상 필드가
            default 로 대체됐음.
        """
        fallback_used = False
        with self._lock:
            new_mode  = self._per_symbol_mode
            new_krw   = self._per_symbol_max_krw
            new_pct   = self._per_symbol_max_pct

        # 1. mode 처리.
        if mode is not None:
            try:
                target_mode = (
                    mode if isinstance(mode, PerSymbolAllocationMode)
                    else PerSymbolAllocationMode(str(mode))
                )
            except ValueError:
                if fallback_to_default:
                    _log.warning(
                        "[paper-capital] per-symbol mode=%r 가 알 수 없음 — "
                        "default %s 로 fallback",
                        mode, DEFAULT_PER_SYMBOL_MODE.value,
                    )
                    target_mode = DEFAULT_PER_SYMBOL_MODE
                    fallback_used = True
                else:
                    raise InvalidPerSymbolAllocationError(
                        f"unknown per-symbol allocation mode: {mode!r}"
                    )
            new_mode = target_mode

        # 2. KRW 값 처리 — None 이 아니면 검증.
        if per_symbol_max_krw is not None:
            target_krw = int(per_symbol_max_krw)
            if target_krw not in ALLOWED_PER_SYMBOL_MAX_KRW:
                if fallback_to_default:
                    _log.warning(
                        "[paper-capital] per_symbol_max_krw=%d 허용 외 — "
                        "default %d 로 fallback",
                        target_krw, DEFAULT_PER_SYMBOL_MAX_KRW,
                    )
                    target_krw = DEFAULT_PER_SYMBOL_MAX_KRW
                    fallback_used = True
                else:
                    raise InvalidPerSymbolAllocationError(
                        f"per_symbol_max_krw={target_krw:,} 가 허용 옵션 "
                        f"{ALLOWED_PER_SYMBOL_MAX_KRW} 외 — 거부."
                    )
            new_krw = target_krw

        # 3. PCT 값 처리 — None 이 아니면 검증.
        if per_symbol_max_pct is not None:
            target_pct = float(per_symbol_max_pct)
            if target_pct not in ALLOWED_PER_SYMBOL_MAX_PCT:
                if fallback_to_default:
                    _log.warning(
                        "[paper-capital] per_symbol_max_pct=%s 허용 외 — "
                        "default %s 로 fallback",
                        target_pct, DEFAULT_PER_SYMBOL_MAX_PCT,
                    )
                    target_pct = DEFAULT_PER_SYMBOL_MAX_PCT
                    fallback_used = True
                else:
                    raise InvalidPerSymbolAllocationError(
                        f"per_symbol_max_pct={target_pct} 가 허용 옵션 "
                        f"{ALLOWED_PER_SYMBOL_MAX_PCT} 외 — 거부."
                    )
            new_pct = target_pct

        with self._lock:
            self._per_symbol_mode    = new_mode
            self._per_symbol_max_krw = new_krw
            self._per_symbol_max_pct = new_pct
            self._updated_at = datetime.now(timezone.utc).isoformat()
        snap = self.snapshot()
        _log.info(
            "[paper-capital] per-symbol set → mode=%s krw=%d pct=%s "
            "effective=%d KRW (fallback=%s)",
            new_mode.value, new_krw, new_pct,
            snap.effective_per_symbol_cap_krw, fallback_used,
        )
        return snap, fallback_used

    def set_max_concurrent_positions(
        self,
        value: int,
        *,
        fallback_to_default: bool = False,
    ) -> tuple[PaperCapitalConfig, bool]:
        """P-03: 최대 동시 보유 종목 수 변경.

        Returns:
            (new_config, fallback_used). fallback_used=True 면 *입력값이 허용
            되지 않아 default 로 대체* 됐음.
        """
        target = int(value)
        fallback_used = False
        if target not in ALLOWED_MAX_CONCURRENT_POSITIONS:
            if fallback_to_default:
                _log.warning(
                    "[paper-capital] max_concurrent_positions=%d 허용 외 — "
                    "default %d 로 fallback",
                    target, DEFAULT_MAX_CONCURRENT_POSITIONS,
                )
                target = DEFAULT_MAX_CONCURRENT_POSITIONS
                fallback_used = True
            else:
                raise InvalidMaxConcurrentPositionsError(
                    f"max_concurrent_positions={target} 가 허용 옵션 "
                    f"{ALLOWED_MAX_CONCURRENT_POSITIONS} 외 — 거부."
                )
        with self._lock:
            self._max_concurrent_positions = target
            self._updated_at = datetime.now(timezone.utc).isoformat()
        snap = self.snapshot()
        _log.info(
            "[paper-capital] max_concurrent_positions set → %d (fallback=%s)",
            target, fallback_used,
        )
        return snap, fallback_used

    def reset_for_tests(self) -> None:
        """테스트 격리용 — process state 를 default 로 reset."""
        with self._lock:
            self._initial_cash = DEFAULT_PAPER_INITIAL_CASH
            self._per_symbol_mode    = DEFAULT_PER_SYMBOL_MODE
            self._per_symbol_max_krw = DEFAULT_PER_SYMBOL_MAX_KRW
            self._per_symbol_max_pct = DEFAULT_PER_SYMBOL_MAX_PCT
            self._max_concurrent_positions = DEFAULT_MAX_CONCURRENT_POSITIONS
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


# ============================================================================
# P-02 — public API
# ============================================================================


def set_per_symbol_allocation(
    *,
    mode: PerSymbolAllocationMode | str | None = None,
    per_symbol_max_krw: int | None = None,
    per_symbol_max_pct: float | None = None,
    fallback_to_default: bool = False,
) -> tuple[PaperCapitalConfig, bool]:
    """종목당 한도 설정 변경 — partial update 지원.

    세 필드 모두 *옵션* — None 으로 두면 현재 값 유지. 본 함수는 broker /
    OrderExecutor / route_order / KIS 어떤 호출도 *하지 않는다* (in-memory
    갱신만).

    Returns:
        (new_config, fallback_used). fallback_used=True 면 *입력값 1개 이상이
        허용되지 않아 default 로 대체* 됐음.
    """
    return _store.set_per_symbol_allocation(
        mode=mode,
        per_symbol_max_krw=per_symbol_max_krw,
        per_symbol_max_pct=per_symbol_max_pct,
        fallback_to_default=fallback_to_default,
    )


def is_allowed_per_symbol_max_krw(value: int) -> bool:
    """UI 사전 validation — 허용 KRW 옵션 안에 있는지."""
    try:
        return int(value) in ALLOWED_PER_SYMBOL_MAX_KRW
    except (TypeError, ValueError):
        return False


def is_allowed_per_symbol_max_pct(value: float) -> bool:
    """UI 사전 validation — 허용 PCT 옵션 안에 있는지."""
    try:
        return float(value) in ALLOWED_PER_SYMBOL_MAX_PCT
    except (TypeError, ValueError):
        return False


# ============================================================================
# P-03 — public API
# ============================================================================


def set_max_concurrent_positions(
    value: int,
    *,
    fallback_to_default: bool = False,
) -> tuple[PaperCapitalConfig, bool]:
    """최대 동시 보유 종목 수 변경.

    `fallback_to_default=True` 면 허용되지 않은 값도 default 로 대체.
    `False` (기본) 면 `InvalidMaxConcurrentPositionsError` 로 즉시 raise.

    본 함수는 broker / OrderExecutor / route_order / KIS 어떤 호출도 *하지
    않는다* — 단순히 in-memory 값을 갱신.
    """
    return _store.set_max_concurrent_positions(
        value, fallback_to_default=fallback_to_default,
    )


def is_allowed_max_concurrent_positions(value: int) -> bool:
    """UI 사전 validation — 허용 옵션 안에 있는지."""
    try:
        return int(value) in ALLOWED_MAX_CONCURRENT_POSITIONS
    except (TypeError, ValueError):
        return False


# ============================================================================
# P-10: 일일 최대 신규 매수금액 한도 — capital_config 측 helper
# ============================================================================
#
# 핵심 check 함수는 `app/risk/loss_limits.py::check_daily_buy_limit`. 본 모듈
# 은 *resolve* layer — 사용자 수동값 / P-09 risk profile 기반 / 시스템
# default 의 우선순위 단일 진실 (사용자 요청서 §2).

# system 기본값 — 사용자 요청서 §2 권장.
DEFAULT_DAILY_BUY_LIMIT_KRW: int = 3_000_000

# ============================================================================
# P-11: 종목별 최대 비중 — capital_config 측 helper
# ============================================================================
#
# 핵심 check 함수는 `app/risk/position_limits.py::check_symbol_weight_limit`.
# 본 모듈은 *resolve* layer — 사용자 수동값 / P-09 risk profile 기반 /
# 시스템 default 의 우선순위 단일 진실 (사용자 요청서 §2).

# 시스템 기본값 — 사용자 요청서 §2 권장 (20%).
DEFAULT_MAX_SYMBOL_WEIGHT_PCT: float = 0.20

# P-09 risk profile 기반 종목별 비중 — 사용자 요청서 §2 권장 매핑.
# (P-09 의 per_symbol_allocation_ratio 과 *동일 값* 이라 그것을 직접 carry.)
_RISK_PROFILE_SYMBOL_WEIGHT_PCT: dict[str, float] = {
    "CONSERVATIVE": 0.10,
    "BALANCED":     0.20,
    "AGGRESSIVE":   0.30,
}


def resolve_symbol_weight_limit_pct(
    *,
    manual_max_symbol_weight_pct: float | None = None,
    risk_profile:                  str | None  = None,
) -> tuple[float, str]:
    """종목별 최대 비중 resolve — 우선순위 (사용자 요청서 §2):

      1. 사용자 수동값 (`manual_max_symbol_weight_pct` 가 (0, 1] 일 때)
      2. P-09 risk profile 기반 자동값 (보수 10% / 안정 20% / 공격 30%)
      3. 시스템 기본값 (`DEFAULT_MAX_SYMBOL_WEIGHT_PCT` = 20%)

    Returns:
        tuple(pct, source) — source 는 "manual" / "risk_profile" /
        "system_default" 중 하나.
    """
    # 1. 수동값.
    if manual_max_symbol_weight_pct is not None:
        try:
            v = float(manual_max_symbol_weight_pct)
        except (TypeError, ValueError):
            v = -1.0
        if 0.0 < v <= 1.0:
            return v, "manual"

    # 2. P-09 risk profile.
    if risk_profile is not None:
        key = str(risk_profile).strip().upper()
        if key in _RISK_PROFILE_SYMBOL_WEIGHT_PCT:
            return _RISK_PROFILE_SYMBOL_WEIGHT_PCT[key], "risk_profile"

    # 3. 시스템 기본값.
    return DEFAULT_MAX_SYMBOL_WEIGHT_PCT, "system_default"


def resolve_daily_buy_limit(
    *,
    manual_daily_buy_limit_krw: int | None = None,
    risk_profile:               str | None = None,
    total_paper_capital_krw:    int | None = None,
) -> tuple[int, str]:
    """일일 매수 한도 resolve — 우선순위 (사용자 요청서 §2):

      1. 사용자 수동값 (`manual_daily_buy_limit_krw` 가 > 0 일 때)
      2. P-09 risk profile 기반 자동값
         (`capital_allocation_for(profile, total_paper_capital_krw).max_daily_buy_amount`)
      3. 시스템 기본값 (`DEFAULT_DAILY_BUY_LIMIT_KRW`)

    Returns:
        tuple(amount_krw, source) — source 는 "manual" / "risk_profile" /
        "system_default" / "fallback" 중 하나.
    """
    # 1. 수동값.
    if manual_daily_buy_limit_krw is not None:
        try:
            v = int(manual_daily_buy_limit_krw)
        except (TypeError, ValueError):
            v = 0
        if v > 0:
            return v, "manual"

    # 2. P-09 risk profile.
    if risk_profile is not None and total_paper_capital_krw is not None:
        try:
            from app.agents.risk_profile import capital_allocation_for
            r = capital_allocation_for(
                risk_profile,
                total_paper_capital_krw=int(total_paper_capital_krw),
            )
            if r.max_daily_buy_amount_krw > 0:
                return r.max_daily_buy_amount_krw, "risk_profile"
        except Exception:  # noqa: BLE001
            pass

    # 3. 시스템 기본값.
    return DEFAULT_DAILY_BUY_LIMIT_KRW, "system_default"


__all__ = [
    # P-01
    "DEFAULT_PAPER_INITIAL_CASH",
    "ALLOWED_PAPER_INITIAL_CASH",
    "PAPER_CAPITAL_CURRENCY",
    "InvalidPaperCapitalError",
    "PaperCapitalConfig",
    "get_paper_capital_config",
    "set_paper_capital_config",
    "is_allowed_initial_cash",
    "reset_paper_capital_for_tests",
    # P-02
    "PerSymbolAllocationMode",
    "DEFAULT_PER_SYMBOL_MAX_KRW",
    "ALLOWED_PER_SYMBOL_MAX_KRW",
    "DEFAULT_PER_SYMBOL_MAX_PCT",
    "ALLOWED_PER_SYMBOL_MAX_PCT",
    "DEFAULT_PER_SYMBOL_MODE",
    "InvalidPerSymbolAllocationError",
    "compute_effective_per_symbol_cap_krw",
    "set_per_symbol_allocation",
    "is_allowed_per_symbol_max_krw",
    "is_allowed_per_symbol_max_pct",
    # P-03
    "DEFAULT_MAX_CONCURRENT_POSITIONS",
    "ALLOWED_MAX_CONCURRENT_POSITIONS",
    "InvalidMaxConcurrentPositionsError",
    "set_max_concurrent_positions",
    "is_allowed_max_concurrent_positions",
    # P-10
    "DEFAULT_DAILY_BUY_LIMIT_KRW",
    "resolve_daily_buy_limit",
    # P-11
    "DEFAULT_MAX_SYMBOL_WEIGHT_PCT",
    "resolve_symbol_weight_limit_pct",
]
