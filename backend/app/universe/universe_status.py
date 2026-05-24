"""7-02: 기본 Universe 50개 상태 표시 (read-only, advisory).

사용자가 관심종목을 설정하지 않아도 자동매매 후보군이 비어 있지 않도록 기본
Universe 50개를 fallback 으로 쓰고, EXE 화면이 `universe_source` / `count` /
`symbols_preview` / `fallback_used` / `reason_code` 를 명확히 표시하도록 status 를
산출한다.

#54 가 추가하는 것 (기존 `default_universe.py` 위에 얇은 status 레이어):
- 6자리 한국 종목코드 *유효성* 검증 + invalid 제거 + 순서보존 dedup.
- `symbols_preview`(앞 N개) — 전체 50개를 UI 에 펼치지 않음.
- `reason_code` — 후보군 0개 / fallback 사유를 명시(조용히 멈추지 않게).

**기본 Universe 는 자동매매 후보군 확보용이며 투자 추천이 아니다.** 사용자가
관심종목을 설정하면 사용자 관심종목이 우선된다.

CLAUDE.md: broker / OrderExecutor / route_order / KIS endpoint / 외부 HTTP /
AI SDK import·호출 0건. Secret/계좌 carry 0건. 안전 flag 변경 0건.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from app.universe.default_universe import FALLBACK_MARKET_CAP_TOP50

DEFAULT_PREVIEW_LIMIT = 8
_KR_SYMBOL = re.compile(r"^\d{6}$")

# universe_source 라벨.
USER_WATCHLIST               = "USER_WATCHLIST"
DEFAULT_UNIVERSE_50          = "DEFAULT_UNIVERSE_50"
FALLBACK_DEFAULT_UNIVERSE_50 = "FALLBACK_DEFAULT_UNIVERSE_50"
EMPTY                        = "EMPTY"

# reason_code.
NO_USER_WATCHLIST   = "NO_USER_WATCHLIST"     # 관심종목 없음 → 기본 50 사용
USER_WATCHLIST_OK   = "USER_WATCHLIST_OK"     # 사용자 관심종목 사용
DEFAULT_UNIVERSE_OK = "DEFAULT_UNIVERSE_OK"   # 기본 50 정상
NO_VALID_SYMBOLS    = "NO_VALID_SYMBOLS"      # 사용자 종목이 전부 invalid
NO_UNIVERSE_SYMBOLS = "NO_UNIVERSE_SYMBOLS"   # 최종 후보군 0개

_MESSAGES_KO = {
    NO_USER_WATCHLIST:   "사용자 관심종목 없음 → 기본 Universe 50개 사용 중입니다.",
    USER_WATCHLIST_OK:   "사용자 관심종목을 사용 중입니다.",
    DEFAULT_UNIVERSE_OK: "기본 Universe 50개를 사용 중입니다.",
    NO_VALID_SYMBOLS:    "유효한 종목코드가 없어 기본 Universe 50개로 대체했습니다.",
    NO_UNIVERSE_SYMBOLS: "후보군이 없어 자동 판단을 건너뜁니다.",
}


def is_valid_kr_symbol(s: Any) -> bool:
    """6자리 숫자 한국 종목코드인지."""
    return bool(_KR_SYMBOL.match(str(s or "").strip()))


@dataclass(frozen=True)
class UniverseStatus:
    """universe 상태 — advisory, 주문 신호/투자 추천 아님."""

    universe_source: str
    universe_count: int
    symbols_preview: tuple[str, ...]
    fallback_used: bool
    reason_code: str
    message_ko: str
    invalid_symbols_removed: tuple[str, ...] = ()
    duplicate_removed_count: int = 0
    preview_limit: int = DEFAULT_PREVIEW_LIMIT

    is_order_signal: bool = False
    is_investment_advice: bool = False
    contains_secret: bool = False

    def __post_init__(self) -> None:
        for name in ("is_order_signal", "is_investment_advice", "contains_secret"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be False (universe status is advisory)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_source":         self.universe_source,
            "universe_count":          int(self.universe_count),
            "symbols_preview":         list(self.symbols_preview),
            "fallback_used":           bool(self.fallback_used),
            "reason_code":             self.reason_code,
            "message_ko":              self.message_ko,
            "invalid_symbols_removed": list(self.invalid_symbols_removed),
            "duplicate_removed_count": int(self.duplicate_removed_count),
            "preview_limit":           int(self.preview_limit),
            "is_order_signal":         False,
            "is_investment_advice":    False,
            "contains_secret":         False,
        }


def _clean_and_validate(user_symbols: Optional[Iterable[str]]):
    """(valid, invalid, duplicate_removed) — strip/upper, dedup, 6자리 검증."""
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    dup = 0
    for s in (user_symbols or []):
        if s is None:
            continue
        code = str(s).strip().upper()
        if not code:
            continue
        if code in seen:
            dup += 1
            continue
        seen.add(code)
        if is_valid_kr_symbol(code):
            valid.append(code)
        else:
            invalid.append(code)
    return valid, invalid, dup


def build_universe_status(
    user_symbols: Optional[Iterable[str]] = None,
    *,
    preview_limit: int = DEFAULT_PREVIEW_LIMIT,
    fallback_on_all_invalid: bool = True,
) -> UniverseStatus:
    """현재 universe 상태 산출. 후보군이 0개로 *조용히* 멈추지 않게 reason 명시."""
    limit = max(1, min(int(preview_limit or DEFAULT_PREVIEW_LIMIT), 20))
    provided = any(str(s or "").strip() for s in (user_symbols or []))
    valid, invalid, dup = _clean_and_validate(user_symbols)
    invalid_preview = tuple(invalid[:limit])

    def _status(source, symbols, fallback, reason) -> UniverseStatus:
        count = len(symbols)
        if count == 0:
            reason = NO_UNIVERSE_SYMBOLS
        return UniverseStatus(
            universe_source=source if count else EMPTY,
            universe_count=count,
            symbols_preview=tuple(symbols[:limit]),
            fallback_used=fallback,
            reason_code=reason,
            message_ko=_MESSAGES_KO.get(reason, ""),
            invalid_symbols_removed=invalid_preview,
            duplicate_removed_count=dup,
            preview_limit=limit,
        )

    # 1. 사용자 관심종목 미설정 → 기본 50 fallback.
    if not provided:
        return _status(DEFAULT_UNIVERSE_50, list(FALLBACK_MARKET_CAP_TOP50),
                       fallback=True, reason=NO_USER_WATCHLIST)

    # 2. 사용자 관심종목 + 유효 1개 이상 → USER_WATCHLIST (fallback 미사용).
    if valid:
        return _status(USER_WATCHLIST, valid, fallback=False, reason=USER_WATCHLIST_OK)

    # 3. 사용자 종목이 모두 invalid.
    if fallback_on_all_invalid:
        return _status(FALLBACK_DEFAULT_UNIVERSE_50, list(FALLBACK_MARKET_CAP_TOP50),
                       fallback=True, reason=NO_VALID_SYMBOLS)
    # fallback 비활성 — 후보군 EMPTY.
    return _status(EMPTY, [], fallback=False, reason=NO_VALID_SYMBOLS)


__all__ = [
    "DEFAULT_PREVIEW_LIMIT",
    "USER_WATCHLIST", "DEFAULT_UNIVERSE_50", "FALLBACK_DEFAULT_UNIVERSE_50", "EMPTY",
    "NO_USER_WATCHLIST", "USER_WATCHLIST_OK", "DEFAULT_UNIVERSE_OK",
    "NO_VALID_SYMBOLS", "NO_UNIVERSE_SYMBOLS",
    "is_valid_kr_symbol",
    "UniverseStatus",
    "build_universe_status",
]
