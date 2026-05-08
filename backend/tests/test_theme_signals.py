"""ThemeSignal model + scoring + provider 테스트 (#22)."""

from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ThemeSignal
from app.themes.providers import (
    DisclosureProviderStub,
    GoogleTrendsAlphaProvider,
    ManualProvider,
    MockThemeProvider,
    NewsProviderStub,
    ThemeRecord,
    default_provider,
)
from app.themes.scoring import (
    GRADE_STRONG,
    GRADE_WATCH,
    GRADE_WEAK,
    compute_theme_score,
    grade_theme_signal,
)


def _session_factory():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False, expire_on_commit=False)


# ---------- DB model ----------


def test_theme_signal_used_for_order_default_false():
    Session = _session_factory()
    with Session() as db:
        row = ThemeSignal(
            theme="AI 반도체", keywords=["HBM"], related_symbols=["005930"],
            score=85, grade="STRONG", confidence=80,
            source="trends", provider="mock",
        )
        db.add(row)
        db.commit()

        fetched = db.execute(select(ThemeSignal)).scalar_one()
        assert fetched.used_for_order is False
        assert fetched.theme == "AI 반도체"
        assert fetched.keywords == ["HBM"]


def test_theme_signal_persists_full_payload():
    Session = _session_factory()
    with Session() as db:
        row = ThemeSignal(
            theme="2차 전지",
            keywords=["양극재", "리튬"],
            related_symbols=["247540", "066970"],
            score=65, grade="WATCH", confidence=70,
            source="news", provider="news_stub",
            summary="모멘텀 약화",
            raw={"src_count": 12},
        )
        db.add(row)
        db.commit()

        out = db.execute(select(ThemeSignal)).scalar_one()
        assert out.summary == "모멘텀 약화"
        assert out.raw == {"src_count": 12}


# ---------- scoring ----------


def test_compute_theme_score_basic():
    s = compute_theme_score(raw_score=80, confidence=80,
                            related_symbol_count=2, keyword_count=2)
    assert 70 <= s <= 90  # 80 베이스, 보너스 없음


def test_compute_theme_score_low_confidence_dampens():
    """confidence < 50이면 base × 0.6."""
    s_high = compute_theme_score(raw_score=80, confidence=80)
    s_low  = compute_theme_score(raw_score=80, confidence=30)
    assert s_low < s_high
    assert s_low <= 60


def test_compute_theme_score_diversity_bonus():
    base    = compute_theme_score(raw_score=70, confidence=80,
                                  related_symbol_count=1, keyword_count=1)
    diverse = compute_theme_score(raw_score=70, confidence=80,
                                  related_symbol_count=5, keyword_count=5)
    assert diverse > base


def test_compute_theme_score_clamped_to_100():
    s = compute_theme_score(raw_score=200, confidence=100,
                            related_symbol_count=10, keyword_count=10)
    assert s == 100


def test_compute_theme_score_clamped_to_zero():
    s = compute_theme_score(raw_score=-50, confidence=0)
    assert s == 0


def test_grade_theme_signal_boundaries():
    assert grade_theme_signal(GRADE_STRONG) == "STRONG"
    assert grade_theme_signal(GRADE_WATCH)  == "WATCH"
    assert grade_theme_signal(GRADE_WEAK)   == "WEAK"
    assert grade_theme_signal(GRADE_WEAK - 1) == "IGNORE"
    assert grade_theme_signal(95) == "STRONG"
    assert grade_theme_signal(70) == "WATCH"
    assert grade_theme_signal(40) == "WEAK"
    assert grade_theme_signal(10) == "IGNORE"


# ---------- providers ----------


def test_mock_provider_is_enabled_and_returns_records():
    provider = MockThemeProvider()
    assert provider.is_enabled() is True

    records = provider.scan(now=datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc))
    assert isinstance(records, list)
    assert len(records) > 0
    for r in records:
        assert isinstance(r, ThemeRecord)
        assert r.provider == "mock"
        assert r.created_at is not None


def test_mock_provider_is_deterministic_under_same_now():
    """같은 (now, universe)에 대해 결과 동일 — CI 안정성."""
    p = MockThemeProvider()
    now = datetime(2026, 5, 20, 12, 0, tzinfo=timezone.utc)
    a = p.scan(universe=["005930", "247540"], now=now)
    b = p.scan(universe=["005930", "247540"], now=now)
    assert [r.theme for r in a] == [r.theme for r in b]
    assert [r.score for r in a] == [r.score for r in b]


def test_mock_provider_filters_by_universe():
    p = MockThemeProvider()
    out = p.scan(universe=["005930"])
    for r in out:
        for sym in r.related_symbols:
            assert sym == "005930"


def test_mock_provider_score_desc_order():
    records = MockThemeProvider().scan()
    scores = [r.score for r in records]
    assert scores == sorted(scores, reverse=True)


def test_mock_provider_no_external_calls(monkeypatch):
    """안전 측 — provider가 어떤 외부 import도 시도하지 않는지 간접 검증.

    requests / httpx가 import되어 있더라도 본 provider는 호출하지 않는다는
    invariant은 코드 검사로 보장 (직접 확인 — 본 모듈은 broker/network import 0건).
    """
    import app.themes.providers as mod
    src = mod.__file__
    with open(src, encoding="utf-8") as f:
        text = f.read()
    assert "import requests" not in text
    assert "import httpx" not in text
    assert "urllib" not in text


def test_google_trends_alpha_is_disabled_by_default():
    p = GoogleTrendsAlphaProvider()
    assert p.is_enabled() is False
    assert p.scan() == []


def test_google_trends_alpha_with_key_still_disabled_in_this_pr():
    """본 PR 단계에서는 API key가 주입돼도 영구 disabled."""
    p = GoogleTrendsAlphaProvider(api_key="alpha-test-key-do-not-use")
    assert p.is_enabled() is False


def test_news_and_disclosure_stubs_are_disabled():
    assert NewsProviderStub().is_enabled() is False
    assert DisclosureProviderStub().is_enabled() is False
    assert NewsProviderStub().scan() == []
    assert DisclosureProviderStub().scan() == []


def test_manual_provider_returns_injected_records():
    rec = ThemeRecord(
        theme="t", keywords=["k"], related_symbols=["005930"],
        score=85, grade="STRONG", confidence=80,
        source="manual", provider="manual",
    )
    p = ManualProvider([rec])
    assert p.is_enabled() is True
    out = p.scan()
    assert len(out) == 1
    assert out[0].theme == "t"


def test_default_provider_is_mock():
    """본 PR 기본 — Mock. alpha 옵트인은 별도 PR."""
    assert default_provider().name == "mock"
