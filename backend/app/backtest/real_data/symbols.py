"""대표 종목 카탈로그 — 3단계 1차 검증용 10종.

코드에서는 항상 ``symbol`` (6자리 종목코드) 기준으로 처리한다. ``display_ko``
는 *문서 / 리포트용 라벨* 일 뿐 매매 로직에 사용 금지.

2차 확장 정책 (`extension_policy`):
- 거래대금 상위 50~100 종목으로 확장 가능 — 별도 PR / 운영자 옵트인.
- 본 모듈은 1차 후보만 정적으로 정의 — 자동 외부 fetch 0건.

CLAUDE.md 절대 원칙:
- broker / OrderExecutor / route_order import 0건.
- 외부 API 호출 0건 (정적 카탈로그만).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepresentativeSymbol:
    """대표 종목 메타 — 코드에서 사용하는 symbol + 사람이 읽는 라벨.

    매매 로직 / 백테스트는 ``symbol`` 만 사용. ``display_ko`` 는 리포트 출력용.
    """

    symbol:      str           # 6자리 종목코드 (필수, 매매 로직 키)
    display_ko:  str           # 한글 표시명 (운영자 리포트용)
    market:      str           # "KOSPI" | "KOSDAQ"
    sector_hint: str           # 운영자 검토용 섹터 힌트 (분석 가중치 아님)

    def __post_init__(self) -> None:
        if not (len(self.symbol) == 6 and self.symbol.isdigit()):
            raise ValueError(
                f"symbol must be 6-digit Korean stock code: {self.symbol!r}"
            )
        if self.market not in ("KOSPI", "KOSDAQ"):
            raise ValueError(f"market must be KOSPI or KOSDAQ: {self.market!r}")


# 1차 검증 카탈로그 — 거래대금 / 대표성 위주로 선정한 10종.
# 본 목록은 *고정 카탈로그* — 운영자가 변경할 때는 별도 PR + 백테스트 재실행.
REPRESENTATIVE_SYMBOLS: tuple[RepresentativeSymbol, ...] = (
    RepresentativeSymbol("005930", "삼성전자",         "KOSPI", "semiconductor"),
    RepresentativeSymbol("000660", "SK하이닉스",       "KOSPI", "semiconductor"),
    RepresentativeSymbol("035420", "NAVER",            "KOSPI", "internet"),
    RepresentativeSymbol("035720", "카카오",           "KOSPI", "internet"),
    RepresentativeSymbol("005380", "현대차",           "KOSPI", "auto"),
    RepresentativeSymbol("051910", "LG화학",           "KOSPI", "chemical"),
    RepresentativeSymbol("068270", "셀트리온",         "KOSPI", "biotech"),
    RepresentativeSymbol("373220", "LG에너지솔루션",   "KOSPI", "battery"),
    RepresentativeSymbol("105560", "KB금융",           "KOSPI", "finance"),
    RepresentativeSymbol("055550", "신한지주",         "KOSPI", "finance"),
)


def representative_symbol_codes() -> list[str]:
    """리스트 형태로 6자리 코드만 추출 — 백테스트 input 으로 직접 전달용."""
    return [s.symbol for s in REPRESENTATIVE_SYMBOLS]


def find_symbol(code: str) -> RepresentativeSymbol | None:
    """카탈로그에 등록된 symbol 조회. 미등록이면 None — caller 가 처리."""
    for s in REPRESENTATIVE_SYMBOLS:
        if s.symbol == code:
            return s
    return None


# 2차 확장 정책 — 본 PR 에서는 *문자열로만 정의*. 자동 fetch 0건.
EXTENSION_POLICY = (
    "1차 (본 PR): 위 10종 고정 카탈로그. "
    "2차: 거래대금 / 유동성 상위 50~100 종목 확장 — 별도 PR + 운영자 옵트인. "
    "3차: 데이터 부족 / 거래정지 / 관리종목 / 신규상장 / 거래량 부족 종목은 "
    "필터 통과 종목만 사용."
)
