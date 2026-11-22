"""V3: strategy-performance period=daily — 오늘 episode 만(누적 수백 개 오표기 방지)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import AgentDecisionEpisode
from app.agents.decision_episode import list_episodes


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _ep(db, *, eid, at, action="BUY"):
    db.add(AgentDecisionEpisode(episode_id=eid, final_action=action, created_at=at, mode="PAPER"))


def test_list_episodes_since_filters_to_today(db):
    now = datetime.now(timezone.utc)
    _ep(db, eid="old-1", at=now - timedelta(days=3))   # 과거
    _ep(db, eid="old-2", at=now - timedelta(days=1))   # 어제
    _ep(db, eid="today-1", at=now)                      # 오늘
    db.commit()

    # since 없음 → 누적 3건
    assert len(list_episodes(db, limit=500)) == 3
    # since=오늘 0시(UTC 근사) → 오늘 1건만
    since = now - timedelta(hours=2)
    today = list_episodes(db, limit=500, since=since)
    assert len(today) == 1
    assert today[0]["episode_id"] == "today-1"


def test_endpoint_daily_scopes_and_flags_weekend(monkeypatch):
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, expire_on_commit=False)

    def _ov():
        s = TS()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _ov
    try:
        with TestClient(app) as c:
            r = c.get("/api/agents/strategy-performance?period=daily")
        assert r.status_code == 200
        b = r.json()
        assert b["period"] == "daily"
        assert b["episodes_analyzed"] == 0          # 오늘 episode 0 → 누적 아님
        assert "market_closed_today" in b           # 휴장 플래그 carry
    finally:
        app.dependency_overrides.pop(get_db, None)
