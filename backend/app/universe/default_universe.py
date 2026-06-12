"""기본 Universe(후보 종목군) 해결 — watchlist 가 비어도 PAPER 테스트 가능.

Auto Paper Loop / 진단(run-once) 흐름이 후보 종목을 고를 때, 사용자가 관심종목
(watchlist)을 등록하지 않았더라도 *시가총액 상위 50 종목* fallback universe 로
파이프라인 전체를 검증할 수 있게 한다.

설계 원칙 (CLAUDE.md 절대 원칙 1~5 상속):
- 본 모듈은 *순수 데이터 + 결정론적 함수* — broker / OrderExecutor /
  route_order / KIS / Anthropic / OpenAI / httpx / requests import 0건.
- DB / 외부 HTTP 호출 0건.
- 안전 flag mutate 0건.
- `UniverseResolution.is_order_signal=False` /
  `is_investment_advice=False` 영구 (dataclass `__post_init__` 가드).

**중요 — fallback universe 는 *투자 추천이 아니다*.** 시가총액 상위 종목을
*PAPER 검증 후보군* 으로 제공할 뿐이며, 매수 추천 / 주문 신호가 아니다. 실제
매수 여부는 RiskManager / PermissionGate / 전략 신호를 모두 거쳐 결정된다.

종목 코드는 KOSPI 대형주 위주의 *예시 후보군* 이며 실시간 시가총액 순위와
정확히 일치하지 않을 수 있다 (정적 목록). 운영자가 정식 관심종목을 등록하면
fallback 대신 사용자 종목이 우선한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Iterable


# ─────────────────────────────────────────────────────────────────────────────
# Fallback universe — KOSPI 대형주 50 (정적 예시 후보군, 투자 추천 아님)
# ─────────────────────────────────────────────────────────────────────────────
#
# (code, 표시명) — 표시명은 UI carry 용. code 가 단일 진실.
# 50 종목 정확히. 실시간 시총 순위와 다를 수 있음 (정적 목록).

# T1(2026-06-12): 풀 50 → 100 확장 (코스피 대형주 + 코스닥 거래량 상위, 유동성 우선).
#   회전 스캔(driver_bridge)이 100 풀을 ~7~10틱에 커버 — 틱당 KIS 부하는 현행 유지.
_FALLBACK_TOP100: tuple[tuple[str, str], ...] = (
    # ── 기존 KOSPI 대형주 50 ──
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"),
    ("005935", "삼성전자우"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("068270", "셀트리온"),
    ("005490", "POSCO홀딩스"),
    ("035420", "NAVER"),
    ("105560", "KB금융"),
    ("012330", "현대모비스"),
    ("028260", "삼성물산"),
    ("055550", "신한지주"),
    ("035720", "카카오"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("003670", "포스코퓨처엠"),
    ("086790", "하나금융지주"),
    ("015760", "한국전력"),
    ("032830", "삼성생명"),
    ("000810", "삼성화재"),
    ("033780", "KT&G"),
    ("066570", "LG전자"),
    ("003550", "LG"),
    ("017670", "SK텔레콤"),
    ("096770", "SK이노베이션"),
    ("009150", "삼성전기"),
    ("034730", "SK"),
    ("011200", "HMM"),
    ("030200", "KT"),
    ("010130", "고려아연"),
    ("259960", "크래프톤"),
    ("316140", "우리금융지주"),
    ("024110", "기업은행"),
    ("010950", "S-Oil"),
    ("018260", "삼성에스디에스"),
    ("051900", "LG생활건강"),
    ("090430", "아모레퍼시픽"),
    ("047050", "포스코인터내셔널"),
    ("011170", "롯데케미칼"),
    ("161390", "한국타이어앤테크놀로지"),
    ("097950", "CJ제일제당"),
    ("078930", "GS"),
    ("029780", "삼성카드"),
    ("086280", "현대글로비스"),
    ("000100", "유한양행"),
    ("128940", "한미약품"),
    ("271560", "오리온"),
    ("010140", "삼성중공업"),
    # ── 확장 KOSPI 35 (방산·조선·2차전지 소재·유통·금융·산업재) ──
    ("012450", "한화에어로스페이스"),
    ("042660", "한화오션"),
    ("064350", "현대로템"),
    ("047810", "한국항공우주"),
    ("009540", "HD한국조선해양"),
    ("267260", "HD현대일렉트릭"),
    ("042700", "한미반도체"),
    ("011070", "LG이노텍"),
    ("009830", "한화솔루션"),
    ("004020", "현대제철"),
    ("011790", "SKC"),
    ("010120", "LS ELECTRIC"),
    ("006260", "LS"),
    ("008770", "호텔신라"),
    ("139480", "이마트"),
    ("023530", "롯데쇼핑"),
    ("282330", "BGF리테일"),
    ("069960", "현대백화점"),
    ("035250", "강원랜드"),
    ("036570", "엔씨소프트"),
    ("251270", "넷마블"),
    ("003490", "대한항공"),
    ("138040", "메리츠금융지주"),
    ("071050", "한국금융지주"),
    ("016360", "삼성증권"),
    ("006800", "미래에셋증권"),
    ("039490", "키움증권"),
    ("011780", "금호석유"),
    ("000720", "현대건설"),
    ("001040", "CJ"),
    ("000150", "두산"),
    ("241560", "두산밥캣"),
    ("034220", "LG디스플레이"),
    ("018880", "한온시스템"),
    ("326030", "SK바이오팜"),
    # ── 확장 KOSDAQ 15 (2차전지·바이오·게임·반도체 소부장·엔터) ──
    ("247540", "에코프로비엠"),
    ("086520", "에코프로"),
    ("028300", "HLB"),
    ("196170", "알테오젠"),
    ("068760", "셀트리온제약"),
    ("263750", "펄어비스"),
    ("293490", "카카오게임즈"),
    ("112040", "위메이드"),
    ("357780", "솔브레인"),
    ("058470", "리노공업"),
    ("240810", "원익IPS"),
    ("041510", "에스엠"),
    ("035900", "JYP Ent."),
    ("145020", "휴젤"),
    ("214150", "클래시스"),
)


# 외부에 노출하는 fallback 종목 코드 튜플 — 정확히 100개.
FALLBACK_MARKET_CAP_TOP100: tuple[str, ...] = tuple(c for c, _ in _FALLBACK_TOP100)
# code → 표시명 (UI carry 용) — 100종 전체.
FALLBACK_MARKET_CAP_TOP100_NAMES: dict[str, str] = {c: n for c, n in _FALLBACK_TOP100}

# 하위호환 alias — 기존 import(news_agent/routes_positions 등) 보존. TOP50 = 앞 50 부분집합,
#   NAMES 는 100 전체(표시명 lookup 은 superset 이어도 무해·오히려 커버리지↑).
FALLBACK_MARKET_CAP_TOP50: tuple[str, ...] = FALLBACK_MARKET_CAP_TOP100[:50]
FALLBACK_MARKET_CAP_TOP50_NAMES: dict[str, str] = FALLBACK_MARKET_CAP_TOP100_NAMES

# 안전 검증 — 정확히 100개 + 중복 0건 (import 시점 self-check).
assert len(FALLBACK_MARKET_CAP_TOP100) == 100, "fallback universe must be exactly 100"
assert len(set(FALLBACK_MARKET_CAP_TOP100)) == 100, "fallback universe codes must be unique"


_FALLBACK_WARNING_KO = (
    "관심종목이 비어 있어 시가총액·거래량 상위 100개 기본 Universe 를 사용 중입니다. "
    "이는 PAPER 검증용 후보군이며 *투자 추천 / 주문 신호가 아닙니다*. 정식 "
    "관심종목 등록을 권장합니다."
)


class UniverseSource(StrEnum):
    """universe 출처 라벨 — frontend / API / 진단 리포트가 그대로 emit."""

    USER_DEFINED            = "USER_DEFINED"
    FALLBACK_MARKET_CAP_TOP100 = "FALLBACK_MARKET_CAP_TOP100"
    # 하위호환 — 기존 enum 값 보존(옛 테스트/UI 참조).
    FALLBACK_MARKET_CAP_TOP50 = "FALLBACK_MARKET_CAP_TOP50"
    EMPTY                   = "EMPTY"


@dataclass(frozen=True)
class UniverseResolution:
    """universe 해결 결과 — *advisory*, 주문 신호 / 투자 추천 아님.

    `is_order_signal=False` / `is_investment_advice=False` 영구
    (dataclass `__post_init__` ValueError 가드).
    """

    source:        UniverseSource
    symbols:       tuple[str, ...]
    count:         int
    fallback_used: bool
    warning_ko:    str

    is_order_signal:      bool = False
    is_investment_advice: bool = False

    def __post_init__(self) -> None:
        if self.is_order_signal is not False:
            raise ValueError("UniverseResolution.is_order_signal must be False")
        if self.is_investment_advice is not False:
            raise ValueError(
                "UniverseResolution.is_investment_advice must be False"
            )
        if self.count != len(self.symbols):
            raise ValueError(
                f"count ({self.count}) != len(symbols) ({len(self.symbols)})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":               self.source.value,
            "symbols":              list(self.symbols),
            "count":                int(self.count),
            "fallback_used":        bool(self.fallback_used),
            "warning_ko":           self.warning_ko,
            "is_order_signal":      self.is_order_signal,
            "is_investment_advice": self.is_investment_advice,
        }


def _clean_symbols(user_symbols: Iterable[str] | None) -> list[str]:
    """입력 종목 코드 정리 — 공백 제거 + 빈값 제외 + 순서 보존 중복 제거."""
    if not user_symbols:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in user_symbols:
        if s is None:
            continue
        code = str(s).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def get_default_universe(
    user_symbols: Iterable[str] | None = None,
) -> UniverseResolution:
    """현재 universe 해결.

    우선순위:
      1. user_symbols 가 1개 이상 → `USER_DEFINED` (fallback 미사용).
      2. user_symbols 가 비어 있음 → `FALLBACK_MARKET_CAP_TOP50` (50종목 + 경고).

    `EMPTY` 는 본 함수가 *반환하지 않는다* — fallback 이 항상 50종목을 보장하여
    "후보 0건" 으로 조용히 실패하는 것을 막는다. (EMPTY enum 은 caller 가 명시적
    으로 fallback 을 비활성화할 때만 사용.)

    Args:
        user_symbols: 사용자가 등록한 관심종목 코드 목록 (None / 빈 목록 허용).

    Returns:
        UniverseResolution — broker 호출 0건, 투자 추천 아님.
    """
    cleaned = _clean_symbols(user_symbols)
    if cleaned:
        return UniverseResolution(
            source=UniverseSource.USER_DEFINED,
            symbols=tuple(cleaned),
            count=len(cleaned),
            fallback_used=False,
            warning_ko="",
        )
    return UniverseResolution(
        source=UniverseSource.FALLBACK_MARKET_CAP_TOP100,
        symbols=FALLBACK_MARKET_CAP_TOP100,
        count=len(FALLBACK_MARKET_CAP_TOP100),
        fallback_used=True,
        warning_ko=_FALLBACK_WARNING_KO,
    )


__all__ = [
    "FALLBACK_MARKET_CAP_TOP100",
    "FALLBACK_MARKET_CAP_TOP100_NAMES",
    "FALLBACK_MARKET_CAP_TOP50",          # 하위호환 alias(앞 50)
    "FALLBACK_MARKET_CAP_TOP50_NAMES",
    "UniverseSource",
    "UniverseResolution",
    "get_default_universe",
]
