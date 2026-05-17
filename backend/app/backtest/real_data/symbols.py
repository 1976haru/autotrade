"""대표 종목 카탈로그 (3-02) — 1차 검증용 10종.

표기 규칙:
- ``symbol``        — 6자리 종목코드 (e.g. ``"005930"``). *코드 매매 / 백테스트
                      엔진 / CSV 파일명* 모두 이 형식을 사용.
- ``yahoo_ticker()`` — yfinance API 호출용 ``"005930.KS"`` 형식. KOSPI 는 ``.KS``,
                       KOSDAQ 는 ``.KQ``.

코드에서는 항상 ``symbol`` (6자리) 기준으로 처리하고, yfinance fetch 시점에만
``yahoo_ticker()`` 로 변환. 본 분리 덕분에 *CSV 파일명 / 로그 / 리포트* 가
plain 6-digit 으로 통일.

1차 검증 카탈로그 → 2차 확장 정책:
- 본 PR (3-02): 거래대금 / 대표성 위주 10종 고정.
- 후속 PR: 거래대금 / 유동성 상위 50~100 종목으로 확장 가능 — 운영자 옵트인.
- 최종 운용: *유동성 / 거래대금 필터 통과 종목* 만 사용. 거래정지 / 관리종목 /
  신규상장 / 거래량 부족 종목은 제외 또는 INSUFFICIENT_DATA 처리.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RepresentativeSymbol:
    """대표 종목 메타.

    ``symbol`` 만 매매 로직에 사용. ``display_ko`` / ``sector_hint`` 는 운영자
    리포트용 라벨 — 가중치 / 점수 계산에 사용 금지.
    """

    symbol:      str        # 6자리 종목코드 (필수)
    display_ko:  str        # 한글 표시명 (리포트용)
    market:      str        # "KOSPI" | "KOSDAQ"
    sector_hint: str        # 운영자 검토용 sector 라벨

    def __post_init__(self) -> None:
        if not (len(self.symbol) == 6 and self.symbol.isdigit()):
            raise ValueError(
                f"symbol must be 6-digit Korean stock code: {self.symbol!r}"
            )
        if self.market not in ("KOSPI", "KOSDAQ"):
            raise ValueError(f"market must be KOSPI or KOSDAQ: {self.market!r}")


# 1차 검증 — 거래대금 / 대표성 위주로 KOSPI 10종 고정.
# 본 카탈로그 변경 시 별도 PR + 백테스트 재실행 필요.
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
    """6자리 코드 리스트 — 백테스트 input 으로 직접 전달."""
    return [s.symbol for s in REPRESENTATIVE_SYMBOLS]


def yahoo_ticker(symbol: str) -> str:
    """6자리 코드 → yfinance ticker 변환.

    Examples:
        >>> yahoo_ticker("005930")     # KOSPI default
        '005930.KS'
        >>> yahoo_ticker("005930.KS")  # 이미 suffix 있으면 그대로
        '005930.KS'

    카탈로그에 등록된 symbol 은 market 정보에 따라 ``.KS`` 또는 ``.KQ`` 부여.
    미등록 symbol 은 6자리이면 ``.KS`` default (KOSPI 가정).
    """
    if "." in symbol:
        return symbol
    if not (len(symbol) == 6 and symbol.isdigit()):
        return symbol
    # 카탈로그 조회 — 등록된 symbol 은 market 기반 suffix.
    for s in REPRESENTATIVE_SYMBOLS:
        if s.symbol == symbol:
            return f"{symbol}.KQ" if s.market == "KOSDAQ" else f"{symbol}.KS"
    # 미등록 — KOSPI default (운영자 검토 권장).
    return f"{symbol}.KS"


def find_symbol(code: str) -> RepresentativeSymbol | None:
    """등록된 카탈로그에서 symbol 조회."""
    for s in REPRESENTATIVE_SYMBOLS:
        if s.symbol == code:
            return s
    return None
