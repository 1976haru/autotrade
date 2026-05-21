"""Default Universe — 사용자 관심종목이 없을 때 사용할 fallback symbol 목록.

PAPER / SIMULATION 검증 흐름이 *관심종목 미등록* 으로 막히지 않도록, 시가
총액 상위 50개 종목 (2026 년 상반기 기준 *snapshot*) 을 fallback 으로 둔다.

**본 fallback 리스트는 *실전 투자 추천이 아니다***:
- 시가총액 순위는 시시각각 변하지만 본 리스트는 빌드 시점 *snapshot* —
  최신 순위를 보장하지 않는다.
- PAPER / SIMULATION 모의매매 흐름 검증 목적 *전용* — 실거래 종목 선정에
  사용해서는 안 된다.
- 운영자가 직접 관심종목을 등록하면 (`USER_DEFINED`) 본 fallback 은 *자동으로*
  비활성화된다.

장기적으로는 KIS / KRX / Naver / market_data_provider 기반의 *실시간 시가
총액 순위 조회* 로 교체하는 것이 목표이며, 본 모듈의 `UniverseSource` enum
에 `MARKET_CAP_PROVIDER` 라벨이 이미 정의되어 있다 — provider 호출 코드를
추가하면 같은 dataclass / API 표면으로 매끄럽게 확장 가능.

CLAUDE.md 절대 원칙 (테스트로 lock):
- broker / OrderExecutor / route_order import 0건
- KIS / Anthropic / OpenAI / httpx / requests import 0건
- app.core.config.get_settings import 0건 (settings mutation 0건)
- app.brokers / app.execution / app.kis_paper.engine import 0건
- ResolvedUniverse.is_order_signal / is_live_authorization = False 영구
- is_paper_safe_only = True 영구 — 본 universe 는 PAPER 검증 보조 자료
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Sequence


# ============================================================================
# Default size
# ============================================================================


DEFAULT_UNIVERSE_LIMIT: int = 50
DEFAULT_UNIVERSE_NAME:  str = "KOSPI_KOSDAQ_TOP50_MARKET_CAP"

# 시가총액 상위 *snapshot* (2026 상반기 기준) — 6자리 KRX 종목코드 + 한글명.
# 본 리스트는 정렬 순서 = 시총 상위 의도이지만 *정확한 순위 보장 X* — 빌드
# 시점 기준이며 매일 변동. 실전 종목 선정 금지.
FALLBACK_MARKET_CAP_TOP50_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("005380", "현대차"),
    ("005490", "POSCO홀딩스"),
    ("000270", "기아"),
    ("068270", "셀트리온"),
    ("035420", "NAVER"),
    ("105560", "KB금융"),
    ("055550", "신한지주"),
    ("012330", "현대모비스"),
    ("028260", "삼성물산"),
    ("035720", "카카오"),
    ("032830", "삼성생명"),
    ("086790", "하나금융지주"),
    ("066570", "LG전자"),
    ("003550", "LG"),
    ("015760", "한국전력"),
    ("034020", "두산에너빌리티"),
    ("096770", "SK이노베이션"),
    ("017670", "SK텔레콤"),
    ("009150", "삼성전기"),
    ("010130", "고려아연"),
    ("011200", "HMM"),
    ("018260", "삼성에스디에스"),
    ("316140", "우리금융지주"),
    ("003670", "포스코퓨처엠"),
    ("033780", "KT&G"),
    ("010950", "S-Oil"),
    ("090430", "아모레퍼시픽"),
    ("024110", "기업은행"),
    ("086280", "현대글로비스"),
    ("042660", "한화오션"),
    ("267260", "HD현대일렉트릭"),
    ("047050", "포스코인터내셔널"),
    ("003490", "대한항공"),
    ("011070", "LG이노텍"),
    ("009540", "HD한국조선해양"),
    ("251270", "넷마블"),
    ("034730", "SK"),
    ("006400", "삼성SDI"),
    ("051910", "LG화학"),
    ("012450", "한화에어로스페이스"),
    ("010140", "삼성중공업"),
    ("272210", "한화시스템"),
    ("138040", "메리츠금융지주"),
    ("352820", "하이브"),
    ("402340", "SK스퀘어"),
    ("259960", "크래프톤"),
)


# 운영자 / UI 노출용 한국어 경고 문구 — fallback 이 사용될 때마다 동일 carry.
FALLBACK_WARNING_KO = (
    "사용자 관심종목이 없어 시가총액 상위 50개 fallback universe 를 사용합니다. "
    "본 목록은 PAPER 테스트용이며 최신 시가총액 순위 보장이 아니고 투자 추천이 "
    "아닙니다."
)


# ============================================================================
# Source enum
# ============================================================================


class UniverseSource(StrEnum):
    """Universe 가 어디서 왔는지 라벨.

    값은 API payload / 로그 / 진단 리포트에 그대로 emit. caller 는 source 와
    함께 warning_ko 를 사용자에게 표시.
    """

    USER_DEFINED               = "USER_DEFINED"              # 운영자가 watchlist 등록
    MARKET_CAP_PROVIDER        = "MARKET_CAP_PROVIDER"       # 미래: 실시간 provider
    FALLBACK_MARKET_CAP_TOP50  = "FALLBACK_MARKET_CAP_TOP50" # 본 모듈의 snapshot
    EMPTY                      = "EMPTY"                     # NO_UNIVERSE 차단


# ============================================================================
# Result dataclass
# ============================================================================


@dataclass(frozen=True)
class ResolvedUniverse:
    """Universe 해결 결과 — *advisory*, broker / route_order 호출 0건.

    `is_paper_safe_only=True` / `is_order_signal=False` / `is_live_authorization=
    False` 영구 (dataclass __post_init__ 가드). 본 객체는 *후보 종목 carry*
    전용이며 어떤 주문도 발행하지 않는다.
    """

    source:                UniverseSource
    name:                  str
    symbols:               tuple[str, ...]
    requested_limit:       int                = DEFAULT_UNIVERSE_LIMIT
    fallback_used:         bool               = False
    warning_ko:            str                = ""
    metadata:              dict[str, Any]     = field(default_factory=dict)

    is_order_signal:       bool = False
    is_live_authorization: bool = False
    is_paper_safe_only:    bool = True

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("ResolvedUniverse.is_order_signal must be False")
        if self.is_live_authorization is not False:
            raise ValueError("ResolvedUniverse.is_live_authorization must be False")
        if self.is_paper_safe_only is not True:
            raise ValueError("ResolvedUniverse.is_paper_safe_only must be True")
        if self.requested_limit < 0:
            raise ValueError(
                f"requested_limit must be >= 0, got {self.requested_limit}"
            )
        if not isinstance(self.symbols, tuple):
            raise TypeError("ResolvedUniverse.symbols must be a tuple")
        # source = EMPTY 면 symbols 도 비어야 함 — 의미 일관성 가드.
        if self.source is UniverseSource.EMPTY and self.symbols:
            raise ValueError(
                "ResolvedUniverse.source=EMPTY but symbols is non-empty"
            )
        # USER_DEFINED 의 fallback_used 는 False 여야 함 — 라벨 혼동 차단.
        if self.source is UniverseSource.USER_DEFINED and self.fallback_used:
            raise ValueError(
                "USER_DEFINED universe cannot have fallback_used=True"
            )

    @property
    def count(self) -> int:
        return len(self.symbols)

    @property
    def is_empty(self) -> bool:
        return self.count == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_source":       self.source.value,
            "universe_name":         self.name,
            "universe_count":        self.count,
            "symbols":               list(self.symbols),
            "requested_limit":       int(self.requested_limit),
            "fallback_used":         self.fallback_used,
            "warning":               self.warning_ko,
            "is_empty":              self.is_empty,
            "metadata":              dict(self.metadata),
            "is_order_signal":       self.is_order_signal,
            "is_live_authorization": self.is_live_authorization,
            "is_paper_safe_only":    self.is_paper_safe_only,
        }


# ============================================================================
# Helper functions
# ============================================================================


def _normalize_symbol(raw: str | None) -> str | None:
    """trim + uppercase. None / 빈 문자열 → None (caller 가 skip)."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    return s or None


def _dedup_preserve_order(symbols: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        n = _normalize_symbol(s)
        if n is None or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return tuple(out)


def get_fallback_universe_symbols(limit: int = DEFAULT_UNIVERSE_LIMIT) -> tuple[str, ...]:
    """fallback snapshot 의 상위 `limit` 개 종목코드 반환 — *순수 함수*.

    limit < 0 → ValueError. limit == 0 → 빈 tuple. limit > 50 → 전체 50 반환.
    """
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")
    upper = min(int(limit), len(FALLBACK_MARKET_CAP_TOP50_SYMBOLS))
    return tuple(
        code for code, _name in FALLBACK_MARKET_CAP_TOP50_SYMBOLS[:upper]
    )


def get_default_universe(
    *,
    user_symbols: Sequence[str] | None = None,
    limit:        int = DEFAULT_UNIVERSE_LIMIT,
    name:         str = DEFAULT_UNIVERSE_NAME,
) -> ResolvedUniverse:
    """Universe 해결 — *사용자 관심종목 우선*, 없으면 fallback.

    우선순위:
      1. `user_symbols` 가 *비어있지 않으면* USER_DEFINED 로 carry (fallback 미사용).
      2. `user_symbols` 가 None / 빈 컬렉션이면 FALLBACK_MARKET_CAP_TOP50 적용 +
         사용자 친화 한국어 경고 carry.

    Args:
        user_symbols: 운영자 등록 관심종목 (watchlist) 코드 컬렉션.
        limit: 반환할 *최대* 종목 수. user_symbols 가 더 많으면 앞에서 잘림.
        name: universe 이름 라벨 — fallback 이면 강제로
              `DEFAULT_UNIVERSE_NAME` 사용.

    Returns:
        ResolvedUniverse — universe_source / universe_count / symbols /
        warning carry.

    Raises:
        ValueError: limit < 0.
    """
    if limit < 0:
        raise ValueError(f"limit must be >= 0, got {limit}")

    # 1. USER_DEFINED — 정규화 + dedup 후 비어있지 않으면 채택.
    if user_symbols:
        normalized = _dedup_preserve_order(user_symbols)
        if normalized:
            return ResolvedUniverse(
                source=UniverseSource.USER_DEFINED,
                name=name,
                symbols=normalized[:limit] if limit > 0 else (),
                requested_limit=int(limit),
                fallback_used=False,
                warning_ko="",
            )

    # 2. FALLBACK_MARKET_CAP_TOP50 — limit 만큼 잘라 반환.
    fallback_syms = get_fallback_universe_symbols(limit)
    if not fallback_syms:
        # limit == 0 — 명시 0건 요청. EMPTY 라벨로 carry.
        return ResolvedUniverse(
            source=UniverseSource.EMPTY,
            name=DEFAULT_UNIVERSE_NAME,
            symbols=(),
            requested_limit=int(limit),
            fallback_used=False,
            warning_ko=(
                "universe limit=0 요청 — 후보 종목이 없으므로 NO_UNIVERSE 차단."
            ),
        )

    return ResolvedUniverse(
        source=UniverseSource.FALLBACK_MARKET_CAP_TOP50,
        name=DEFAULT_UNIVERSE_NAME,
        symbols=fallback_syms,
        requested_limit=int(limit),
        fallback_used=True,
        warning_ko=FALLBACK_WARNING_KO,
        metadata={
            "snapshot_disclaimer": (
                "fallback / paper test only — 최신 시가총액 순위 보장이 아닙니다."
            ),
            "snapshot_count":  len(FALLBACK_MARKET_CAP_TOP50_SYMBOLS),
        },
    )


__all__ = [
    "DEFAULT_UNIVERSE_LIMIT",
    "DEFAULT_UNIVERSE_NAME",
    "FALLBACK_MARKET_CAP_TOP50_SYMBOLS",
    "FALLBACK_WARNING_KO",
    "UniverseSource",
    "ResolvedUniverse",
    "get_fallback_universe_symbols",
    "get_default_universe",
]
