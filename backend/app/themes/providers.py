"""테마/뉴스/공시/트렌드 데이터 제공자 abstraction (#22).

본 모듈은 외부 네트워크 / 유료 API를 호출하지 않는다 — 본 PR 단계에서는
deterministic stub만 동작. 실 Google Trends alpha / News API / DART 공시
연동은 운영자 옵트인 후 별도 PR.

provider 식별자:
- mock                — deterministic, 외부 호출 0건 (테스트/데모/CI 기본)
- google_trends_alpha — alpha 접근 권한이 없으면 disabled, scan 호출 시 빈 결과
- news_<vendor>       — 미구현 stub (rate limit + 약관 검토 필요)
- disclosure_dart     — 미구현 stub (DART OpenAPI는 별도 가입)
- manual              — 운영자 수동 입력 — UI/CSV 등으로 채움 (본 PR 범위 외)

CLAUDE.md 절대 원칙 — 본 모듈은 broker / RiskManager / PermissionGate /
OrderExecutor를 import하지 않는다. ThemeFilter / 라우트와도 분리.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from app.themes.scoring import compute_theme_score, grade_theme_signal


@dataclass(frozen=True)
class ThemeRecord:
    """provider가 반환하는 단일 테마 레코드 (DB 미저장 형태)."""
    theme:           str
    keywords:        list[str]
    related_symbols: list[str]
    score:           int     # 0~100
    grade:           str     # STRONG / WATCH / WEAK / IGNORE
    confidence:      int     # 0~100
    source:          str     # 분류 — "trends" / "news" / "disclosure" / "manual"
    provider:        str     # 식별자 — "mock" / "google_trends_alpha" / ...
    summary:         str | None = None
    raw:             dict | None = None
    created_at:      datetime | None = None  # provider가 채우거나 호출자가 채움

    def to_dict(self) -> dict:
        return {
            "theme":            self.theme,
            "keywords":         list(self.keywords),
            "related_symbols":  list(self.related_symbols),
            "score":            self.score,
            "grade":            self.grade,
            "confidence":       self.confidence,
            "source":           self.source,
            "provider":         self.provider,
            "summary":          self.summary,
            "raw":              self.raw,
            "created_at":       self.created_at.isoformat() if self.created_at else None,
        }


class ThemeProvider(ABC):
    """테마 시그널 데이터 제공자 ABC.

    `scan(...)`은 provider별로 raw 신호를 가져와 ThemeRecord 리스트로 반환한다.
    실 외부 API를 부르는 구현은 본 PR에서 활성화하지 않는다 — 모두 disabled.
    """

    name: str = "abstract"

    @abstractmethod
    def is_enabled(self) -> bool:
        """이 provider가 활성화돼 있는지. 외부 API alpha 권한 / 키 미설정 시 False."""

    @abstractmethod
    def scan(
        self,
        *,
        universe: list[str] | None = None,
        limit:    int = 10,
        now:      datetime | None = None,
    ) -> list[ThemeRecord]:
        """후보군 universe(symbol 리스트)와 한도를 받아 ThemeRecord 리스트 반환."""


# ---------- MockThemeProvider — 테스트/데모/CI 기본 ----------


class MockThemeProvider(ThemeProvider):
    """결정론적 합성 테마 시그널 — 외부 호출 0건.

    같은 (now, universe)에 대해 항상 같은 결과. 운영자가 demo 화면을 보거나
    CI가 외부 네트워크 없이 통합 테스트할 때 사용한다.
    """

    name = "mock"

    # 데모용 고정 테마 (한국 시장 단타 운영 관점). 실제 종목 마스터와 무관 —
    # 운영 watchlist/active universe로 필터링하는 책임은 ThemeFilter에 있다.
    _CATALOG: list[dict] = [
        {"theme": "AI 반도체",      "keywords": ["HBM", "AI 칩", "엔비디아 공급망"],
         "related_symbols": ["005930", "000660"], "raw_score": 85, "confidence": 80,
         "summary": "AI 가속기 수요 지속 — HBM 비중 큰 종목 후보"},
        {"theme": "2차 전지",       "keywords": ["양극재", "전기차", "리튬"],
         "related_symbols": ["247540", "066970"], "raw_score": 65, "confidence": 70,
         "summary": "전기차 수요 둔화 우려 vs 신규 모델 출시 기대 — WATCH"},
        {"theme": "조선",           "keywords": ["LNG선", "수주"],
         "related_symbols": ["009540", "010140"], "raw_score": 55, "confidence": 60,
         "summary": "신규 수주 모멘텀 — 단기 모니터링"},
        {"theme": "방산",           "keywords": ["국산 무기 수출"],
         "related_symbols": ["047810", "012450"], "raw_score": 40, "confidence": 55,
         "summary": "WEAK 수준 — 대형 호재 시 재평가"},
        {"theme": "엔터테인먼트",   "keywords": ["K-pop", "콘서트"],
         "related_symbols": ["035900", "041510"], "raw_score": 25, "confidence": 50,
         "summary": "IGNORE 수준 — 단타 후보 아님"},
    ]

    def is_enabled(self) -> bool:
        return True

    def scan(
        self,
        *,
        universe: list[str] | None = None,
        limit:    int = 10,
        now:      datetime | None = None,
    ) -> list[ThemeRecord]:
        if now is None:
            now = datetime.now(timezone.utc)

        records: list[ThemeRecord] = []
        for item in self._CATALOG:
            related = list(item["related_symbols"])
            if universe is not None:
                # universe로 좁힘 — 비어있으면 빈 리스트(후보 0)이지만 테마 자체는 유지.
                u = set(universe)
                related = [s for s in related if s in u]
            score = compute_theme_score(
                raw_score=item["raw_score"],
                confidence=item["confidence"],
                related_symbol_count=len(related) or 1,
                keyword_count=len(item["keywords"]),
            )
            grade = grade_theme_signal(score)
            records.append(ThemeRecord(
                theme=item["theme"],
                keywords=list(item["keywords"]),
                related_symbols=related,
                score=score,
                grade=grade,
                confidence=item["confidence"],
                source="trends",
                provider=self.name,
                summary=item["summary"],
                raw={"raw_score": item["raw_score"]},
                created_at=now,
            ))
        # score desc + 한도.
        records.sort(key=lambda r: r.score, reverse=True)
        return records[:limit]


# ---------- GoogleTrendsAlphaProvider — alpha 미접근 시 disabled stub ----------


class GoogleTrendsAlphaProvider(ThemeProvider):
    """Google Trends API alpha 어댑터.

    본 PR에서는 *항상 disabled*. 실 API 호출은 운영자가 alpha 권한 + API key를
    획득하고 별도 옵트인 PR을 머지해야 활성화된다. `scan()`은 활성화돼 있더라도
    본 모듈에서는 NotImplementedError로 차단해 실수로 호출되지 않게 한다.

    `is_enabled()=False`인 동안 호출자(ThemeFilter / 라우트)는 fallback으로
    MockThemeProvider를 사용한다.
    """

    name = "google_trends_alpha"

    def __init__(self, *, api_key: str | None = None):
        # API key가 주입되더라도 본 PR에서는 활성화하지 않는다 — 정책 안전.
        self._api_key = api_key

    def is_enabled(self) -> bool:
        return False  # 영구 disabled — 활성화는 별도 옵트인 PR.

    def scan(
        self,
        *,
        universe: list[str] | None = None,
        limit:    int = 10,
        now:      datetime | None = None,
    ) -> list[ThemeRecord]:
        if not self.is_enabled():
            return []
        # 실 API 호출이 활성화되더라도 본 PR에서는 차단.
        raise NotImplementedError(
            "Google Trends alpha 호출은 별도 옵트인 PR 후 활성화. "
            "본 PR 단계에서는 MockThemeProvider 사용."
        )


# ---------- News / Disclosure / Manual stub ----------


class NewsProviderStub(ThemeProvider):
    """뉴스 provider stub. 실 API 호출 0건 — 본 PR 비활성."""

    name = "news_stub"

    def is_enabled(self) -> bool:
        return False

    def scan(self, *, universe=None, limit=10, now=None) -> list[ThemeRecord]:
        return []


class DisclosureProviderStub(ThemeProvider):
    """공시(DART) provider stub. 실 API 호출 0건 — 본 PR 비활성."""

    name = "disclosure_dart_stub"

    def is_enabled(self) -> bool:
        return False

    def scan(self, *, universe=None, limit=10, now=None) -> list[ThemeRecord]:
        return []


class ManualProvider(ThemeProvider):
    """운영자 수동 입력 provider — 호출자가 ThemeRecord 리스트를 직접 주입."""

    name = "manual"

    def __init__(self, records: list[ThemeRecord] | None = None):
        self._records = records or []

    def is_enabled(self) -> bool:
        return bool(self._records)

    def scan(self, *, universe=None, limit=10, now=None) -> list[ThemeRecord]:
        records = list(self._records)
        if universe is not None:
            u = set(universe)
            records = [
                ThemeRecord(
                    theme=r.theme, keywords=r.keywords,
                    related_symbols=[s for s in r.related_symbols if s in u],
                    score=r.score, grade=r.grade, confidence=r.confidence,
                    source=r.source, provider=r.provider,
                    summary=r.summary, raw=r.raw, created_at=r.created_at,
                )
                for r in records
            ]
        records.sort(key=lambda r: r.score, reverse=True)
        return records[:limit]


# ---------- 기본 provider 선택기 ----------


def default_provider() -> ThemeProvider:
    """본 PR 기본 — Mock. alpha 활성화는 운영자 옵트인 후 별도 PR."""
    return MockThemeProvider()
