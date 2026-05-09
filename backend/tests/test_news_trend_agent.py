"""#53: News / Trend Agent tests.

Coverage:
- `NewsTrendOutput.is_order_signal=True` 시 ValueError (dataclass 가드)
- `summarize_themes` deterministic — 같은 입력에 같은 출력
- 데이터 부족 시 NO_DATA + friendly fallback (예외 X)
- top_themes / rising_keywords / related_candidates 산출
- overheating warnings (score >= 90 + signal_count >= 5)
- used_for_order=True row 발견 시 invariant 위반 경고
- caution_themes (confidence 낮음)
- recommended_action 결정 매트릭스
- DB helpers (read-only)
- `NewsTrendAgent` (#51 AgentBase 호환) 호출
- `/api/agents/news-trend` endpoint
- 정적 가드: news_trend_agent 모듈은 broker / OrderExecutor / route_order /
  외부 HTTP client / DB 변경 import 0건
- BUY/SELL/HOLD 결정 0건
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents.base import AgentContext, AgentDecision, AgentRole
from app.agents.news_trend_agent import (
    GoogleTrendsAlphaProvider,
    MockTrendProvider,
    NewsProvider,
    DisclosureProvider,
    NewsTrendAction,
    NewsTrendAgent,
    NewsTrendOutput,
    load_recent_theme_signals,
    load_theme_signals_by_theme,
    summarize_themes,
)


# ====================================================================
# 1. Output dataclass guard
# ====================================================================


def test_output_rejects_is_order_signal_true():
    with pytest.raises(ValueError, match="is_order_signal"):
        NewsTrendOutput(
            recommended_action=NewsTrendAction.MONITOR,
            summary_lines=[],
            top_themes=[], rising_keywords=[], related_candidates=[],
            caution_themes=[], overheating_warnings=[],
            used_for_order_warnings=[], total_signal_count=0,
            is_order_signal=True,
        )


def test_no_data_emits_friendly_fallback():
    out = summarize_themes([])
    assert out.recommended_action == NewsTrendAction.NO_DATA
    assert out.is_order_signal is False
    assert len(out.summary_lines) >= 2
    # 응답 dict에 BUY/SELL/HOLD 키 없음.
    d = out.to_dict()
    forbidden = {"buy", "sell", "hold", "order", "side", "decision"}
    assert forbidden.isdisjoint(d.keys())


# ====================================================================
# 2. Pure summarizer
# ====================================================================


class _FakeSignal:
    """ThemeSignal과 동일 attribute를 갖는 fake — DB 의존 없는 unit test용."""
    def __init__(self, *, id, theme, score, grade, confidence,
                 keywords=None, related_symbols=None, summary=None,
                 provider="mock", used_for_order=False):
        self.id = id
        self.theme = theme
        self.score = score
        self.grade = grade
        self.confidence = confidence
        self.keywords = keywords or []
        self.related_symbols = related_symbols or []
        self.summary = summary
        self.provider = provider
        self.used_for_order = used_for_order


def _signal(id=1, theme="2차전지", score=70, **kw):
    return _FakeSignal(
        id=id, theme=theme, score=score,
        grade=kw.pop("grade", "WATCH"),
        confidence=kw.pop("confidence", 60),
        **kw,
    )


def test_summarize_groups_by_theme():
    signals = [
        _signal(id=1, theme="2차전지", score=85),
        _signal(id=2, theme="2차전지", score=70),
        _signal(id=3, theme="반도체", score=75),
    ]
    out = summarize_themes(signals)
    themes = {t.theme for t in out.top_themes}
    assert themes == {"2차전지", "반도체"}
    # 2차전지 score는 max=85.
    sec = [t for t in out.top_themes if t.theme == "2차전지"][0]
    assert sec.score == 85
    assert sec.signal_count == 2


def test_summarize_aggregates_keywords_and_symbols():
    signals = [
        _signal(id=1, theme="AI", keywords=["ai", "gpu"],
                 related_symbols=["005930", "000660"]),
        _signal(id=2, theme="반도체", keywords=["gpu", "chip"],
                 related_symbols=["000660", "035720"]),
    ]
    out = summarize_themes(signals)
    keywords = {kw for kw, _ in out.rising_keywords}
    assert {"ai", "gpu", "chip"} <= keywords
    # 000660은 두 신호에 등장.
    cand = {c.symbol: c.occurrence for c in out.related_candidates}
    assert cand.get("000660") == 2


def test_summarize_emits_overheating_warning():
    """score >= 90 + signal_count >= 5 → overheating warn."""
    signals = [
        _signal(id=i, theme="AI", score=92, grade="STRONG")
        for i in range(1, 7)  # 6개
    ]
    out = summarize_themes(signals)
    assert out.recommended_action == NewsTrendAction.OVERHEAT_WARN
    assert len(out.overheating_warnings) == 1
    assert "AI" in out.overheating_warnings[0]


def test_summarize_emits_caution_when_confidence_low():
    signals = [
        _signal(id=1, theme="루머", score=40, confidence=10),
        _signal(id=2, theme="루머", score=35, confidence=20),
    ]
    out = summarize_themes(signals)
    caution_names = {t.theme for t in out.caution_themes}
    assert "루머" in caution_names


def test_summarize_emits_used_for_order_warning():
    """used_for_order=True인 row가 있으면 invariant 위반 의심 경고 +
    recommended_action=CAUTION."""
    signals = [
        _signal(id=1, theme="AI", score=80, used_for_order=True),
        _signal(id=2, theme="2차전지", score=70),
    ]
    out = summarize_themes(signals)
    assert len(out.used_for_order_warnings) == 1
    assert "id=1" in out.used_for_order_warnings[0]
    assert out.recommended_action == NewsTrendAction.CAUTION


def test_summarize_recommends_monitor_for_strong_themes():
    signals = [
        _signal(id=1, theme="AI", score=80, grade="STRONG", confidence=70),
    ]
    out = summarize_themes(signals)
    assert out.recommended_action == NewsTrendAction.MONITOR


def test_summarize_is_deterministic():
    signals = [
        _signal(id=1, theme="AI", score=80),
        _signal(id=2, theme="2차전지", score=70),
    ]
    a = summarize_themes(signals)
    b = summarize_themes(signals)
    da = {k: v for k, v in a.to_dict().items() if k != "created_at"}
    db = {k: v for k, v in b.to_dict().items() if k != "created_at"}
    assert da == db


def test_summary_contains_invariant_disclaimer():
    signals = [_signal(id=1, theme="AI", score=80)]
    out = summarize_themes(signals)
    joined = " ".join(out.summary_lines)
    assert "주문 신호가 아닙니다" in joined or "후보 필터" in joined


# ====================================================================
# 3. Provider stubs — disabled, no external calls
# ====================================================================


def test_disabled_providers_return_empty():
    """Google Trends / News / Disclosure provider 모두 default disabled.
    어떤 외부 호출도 하지 않는다."""
    for cls in (GoogleTrendsAlphaProvider, NewsProvider, DisclosureProvider):
        p = cls()
        assert p.enabled is False
        assert p.fetch_keywords() == []
        assert p.fetch_news() == []
        assert p.fetch_disclosures() == []


def test_mock_provider_returns_fixtures_only():
    p = MockTrendProvider(fixtures=[
        {"keyword": "ai"}, {"keyword": "gpu"},
    ])
    assert p.fetch_keywords() == ["ai", "gpu"]
    # news / disclosure는 여전히 disabled.
    assert p.fetch_news() == []
    assert p.fetch_disclosures() == []


# ====================================================================
# 4. DB helpers (read-only)
# ====================================================================


def test_load_recent_theme_signals_returns_empty_when_no_rows(client):
    with client.test_db_factory() as db:
        rows = load_recent_theme_signals(db, limit=10)
        assert rows == []


def test_load_recent_theme_signals_filters_by_min_score(client):
    from app.db.models import ThemeSignal
    with client.test_db_factory() as db:
        db.add_all([
            ThemeSignal(theme="A", keywords=[], related_symbols=[],
                         score=80, grade="WATCH", confidence=60,
                         source="mock", provider="mock"),
            ThemeSignal(theme="B", keywords=[], related_symbols=[],
                         score=30, grade="WEAK", confidence=20,
                         source="mock", provider="mock"),
        ])
        db.commit()
        high = load_recent_theme_signals(db, min_score=50)
        assert len(high) == 1
        assert high[0].theme == "A"


def test_load_theme_signals_by_theme_filters_by_theme(client):
    from app.db.models import ThemeSignal
    with client.test_db_factory() as db:
        db.add_all([
            ThemeSignal(theme="AI", keywords=[], related_symbols=[],
                         score=70, grade="WATCH", confidence=60,
                         source="mock", provider="mock"),
            ThemeSignal(theme="2차전지", keywords=[], related_symbols=[],
                         score=60, grade="WATCH", confidence=60,
                         source="mock", provider="mock"),
        ])
        db.commit()
        ai_only = load_theme_signals_by_theme(db, "AI")
        assert len(ai_only) == 1
        assert ai_only[0].theme == "AI"


# ====================================================================
# 5. NewsTrendAgent (#51 AgentBase 호환)
# ====================================================================


def test_agent_metadata_marks_no_execute():
    agent = NewsTrendAgent()
    md = agent.metadata
    assert md.role == AgentRole.OBSERVER
    assert md.can_execute_order is False
    forbidden_text = " ".join(md.forbidden)
    assert "BUY" in forbidden_text or "주문 신호" in forbidden_text
    assert "broker" in forbidden_text


def test_agent_run_returns_observe_decision():
    agent = NewsTrendAgent()
    out = agent.run(AgentContext(extra={"theme_signals": [
        _signal(id=1, theme="AI", score=80, grade="STRONG"),
    ]}))
    assert out.role == AgentRole.OBSERVER
    assert out.decision == AgentDecision.OBSERVE
    assert out.is_order_intent is False
    assert out.can_execute_order is False
    # NewsTrendOutput dict이 metadata에 carry.
    assert "top_themes" in out.metadata
    assert out.metadata["is_order_signal"] is False


def test_agent_run_carries_overheating_risk_flag():
    agent = NewsTrendAgent()
    overheated = [
        _signal(id=i, theme="AI", score=95, grade="STRONG")
        for i in range(1, 7)
    ]
    out = agent.run(AgentContext(extra={"theme_signals": overheated}))
    assert "overheating" in out.risk_flags


# ====================================================================
# 6. /api/agents/news-trend endpoint
# ====================================================================


def test_api_news_trend_empty_returns_no_data(client):
    res = client.get("/api/agents/news-trend")
    assert res.status_code == 200
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["recommended_action"] == "NO_DATA"
    assert body["total_signal_count"] == 0


def test_api_news_trend_with_seeded_data(client):
    from app.db.models import ThemeSignal
    with client.test_db_factory() as db:
        db.add_all([
            ThemeSignal(theme="AI", keywords=["gpu"],
                         related_symbols=["005930"],
                         score=85, grade="STRONG", confidence=70,
                         source="mock", provider="mock"),
            ThemeSignal(theme="2차전지", keywords=["배터리"],
                         related_symbols=["005930"],
                         score=70, grade="WATCH", confidence=60,
                         source="mock", provider="mock"),
        ])
        db.commit()
    res = client.get("/api/agents/news-trend")
    body = res.json()
    assert body["is_order_signal"] is False
    assert body["total_signal_count"] == 2
    assert len(body["top_themes"]) == 2


def test_api_news_trend_does_not_mutate_db(client):
    """API 호출이 DB row를 변경하지 않는다 — read-only invariant."""
    from app.db.models import OrderAuditLog, PendingApproval
    client.get("/api/agents/news-trend")
    with client.test_db_factory() as db:
        assert db.execute(select(OrderAuditLog)).scalars().all() == []
        assert db.execute(select(PendingApproval)).scalars().all() == []


# ====================================================================
# 7. Static guards
# ====================================================================


def test_module_does_not_import_broker_or_executor():
    import app.agents.news_trend_agent as mod
    src_path = mod.__file__
    assert src_path is not None
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "from app.brokers",
        "import app.brokers",
        "from app.execution.executor",
        "from app.execution.order_router",
        "from app.permission.gate",
        "broker.place_order(",
        ".place_order(",
        ".cancel_order(",
        "route_order(",
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"app.agents.news_trend_agent must not contain '{snippet}'"
        )


def test_module_does_not_import_external_http_clients():
    """Google Trends / News API 호출이 발생할 수 있는 HTTP client import 금지."""
    import app.agents.news_trend_agent as mod
    src_path = mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden = (
        "import httpx",
        "import requests",
        "import urllib3",
        "import urllib.request",
        "from httpx",
        "from requests",
        "pytrends",   # Google Trends 비공식 라이브러리
    )
    for snippet in forbidden:
        assert snippet not in src, (
            f"news_trend_agent must not import HTTP client '{snippet}' — "
            "외부 API 호출은 별도 옵트인 PR."
        )


def test_module_does_not_emit_db_writes():
    """DB INSERT / UPDATE / DELETE 호출 금지 — SELECT만."""
    import app.agents.news_trend_agent as mod
    src_path = mod.__file__
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    forbidden_calls = (
        "db.add(", "db.add_all(", "db.delete(", "db.commit(",
        ".update().where", "session.add(", "session.add_all(",
    )
    for snippet in forbidden_calls:
        assert snippet not in src, (
            f"news_trend_agent must not contain DB write '{snippet}'"
        )


def test_no_buy_sell_hold_in_module_logic():
    """모듈 코드 안에 BUY/SELL/HOLD 결정 분기가 없음을 검증.

    docstring / 주석 / disclaimer 문자열에 BUY/SELL/HOLD가 등장하는 것은 OK
    (정책 설명). 그러나 enum 값이나 결정 분기 코드로 등장하면 안 된다.
    """
    from app.agents.news_trend_agent import NewsTrendAction
    enum_values = {e.value for e in NewsTrendAction}
    assert "BUY" not in enum_values
    assert "SELL" not in enum_values
    assert "HOLD" not in enum_values
