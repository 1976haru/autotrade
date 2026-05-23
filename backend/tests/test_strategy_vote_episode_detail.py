"""P-23: 4전략(ORB/MOMENTUM/GAP/VWAP) vote 상세 episode 저장 테스트.

검증:
 - 각 episode 에 4전략 vote 모두 저장 (BUY/SELL/HOLD 공통, 누락 0건)
 - 각 vote: signal/score/confidence/reason_code/reason/risk_flags/weight/
   weighted_score
 - council: buy_score/sell_score/hold_score/selected_strategies/threshold_snapshot
 - placeholder vote (전략 평가 불가 시 4개 보장)
 - canonical 대문자 전략명, confidence 0~1, score 0~100
 - 상세 API votes 전체 + 목록 API vote_summary + summary by_strategy/by_signal
 - secret 0건, is_live_authorization=False, broker/route_order import 0건
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agents.agent_council as council_mod
import app.agents.decision_episode as de_mod
from app.agents.agent_council import (
    STRATEGY_ORDER,
    StrategyMarketInput,
    placeholder_strategy_votes,
    run_agent_council,
)
from app.agents.decision_episode import (
    get_episode,
    list_episodes,
    new_episode_id,
    record_episode,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app

_EXPECTED = {"ORB", "MOMENTUM", "GAP", "VWAP"}


def _market_input():
    return StrategyMarketInput(
        symbol="005930", current_price=78000, prev_close=74000, open_price=76000,
        vwap=75000, opening_range_high=75500, opening_range_low=74200,
        recent_closes=tuple(74000 + 800 * i for i in range(6)),
        current_volume=160000, avg_volume=100000,
        market_regime="TREND_UP", regime_decision="ALLOW",
    )


def _council_dict(**kw):
    return run_agent_council(_market_input(), **kw).to_dict()


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _record(db, *, action, council=None, votes=None):
    cd = council if council is not None else _council_dict()
    record_episode(db, episode_id=new_episode_id(), final_action=action,
                   symbol="005930", council=cd,
                   votes=(votes if votes is not None else cd["votes"]))
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# council 4 votes + 필드
# ─────────────────────────────────────────────────────────────────────────────


class TestCouncilVotes:
    def test_council_emits_four_strategies(self):
        cd = _council_dict()
        names = {v["strategy"] for v in cd["votes"]}
        assert names == _EXPECTED
        assert len(cd["votes"]) == 4

    def test_each_vote_has_all_fields(self):
        for v in _council_dict()["votes"]:
            for k in ("strategy", "signal", "score", "confidence", "reason_code",
                      "reason", "risk_flags", "weight", "weighted_score"):
                assert k in v, f"vote missing {k}: {v}"

    def test_strategy_names_uppercase_canonical(self):
        for v in _council_dict()["votes"]:
            assert v["strategy"] == v["strategy"].upper()
            assert v["strategy"] in _EXPECTED

    def test_confidence_and_score_ranges(self):
        for v in _council_dict()["votes"]:
            assert 0.0 <= v["confidence"] <= 1.0
            assert 0 <= v["score"] <= 100

    def test_signal_values(self):
        for v in _council_dict()["votes"]:
            assert v["signal"] in ("BUY", "SELL", "HOLD")

    def test_weighted_score_matches_weight(self):
        for v in _council_dict()["votes"]:
            assert abs(v["weighted_score"] - v["weight"] * (v["score"] / 100.0)) < 0.01

    def test_council_scores_and_threshold(self):
        cd = _council_dict(risk_profile="BALANCED")
        assert "buy_score" in cd and "sell_score" in cd and "hold_score" in cd
        ts = cd["threshold_snapshot"]
        assert ts["risk_profile"] == "BALANCED"
        assert ts["min_confidence"] is not None
        assert ts["min_quality_score"] is not None
        assert ts["max_risk_flags"] is not None


class TestPlaceholders:
    def test_placeholder_four_votes(self):
        pv = placeholder_strategy_votes()
        assert {v["strategy"] for v in pv} == _EXPECTED
        assert len(pv) == 4

    def test_placeholder_fields(self):
        for v in placeholder_strategy_votes():
            assert v["signal"] == "HOLD"
            assert v["score"] == 0
            assert v["reason_code"] == "STRATEGY_DATA_UNAVAILABLE"
            assert "DATA_UNAVAILABLE" in v["risk_flags"]
            assert v["weight"] > 0

    def test_strategy_order(self):
        assert STRATEGY_ORDER == ("ORB", "MOMENTUM", "GAP", "VWAP")


# ─────────────────────────────────────────────────────────────────────────────
# episode 저장 — BUY/SELL/HOLD 모두 4 votes
# ─────────────────────────────────────────────────────────────────────────────


class TestEpisodeVoteStorage:
    @pytest.mark.parametrize("action", ["BUY", "SELL", "HOLD"])
    def test_episode_stores_four_votes(self, db, action):
        cd = _council_dict()
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action=action, symbol="005930",
                       council=cd, votes=cd["votes"])
        db.commit()
        ep = get_episode(db, eid)
        names = {v["strategy"] for v in ep["votes"]}
        assert names == _EXPECTED, f"{action}: vote 누락 — {names}"
        assert len(ep["votes"]) == 4

    def test_episode_council_scores_and_selected(self, db):
        cd = _council_dict()
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action=cd["final_action"],
                       symbol="005930", council=cd, votes=cd["votes"],
                       confidence=int(cd["confidence"] * 100),
                       quality_score=cd["quality_score"])
        db.commit()
        ep = get_episode(db, eid)
        assert "buy_score" in ep["council"]
        assert "sell_score" in ep["council"]
        assert "hold_score" in ep["council"]
        assert "selected_strategies" in ep["council"]
        assert "threshold_snapshot" in ep["council"]

    def test_placeholder_episode_when_no_council(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="HOLD", symbol="X",
                       votes=placeholder_strategy_votes())
        db.commit()
        ep = get_episode(db, eid)
        assert {v["strategy"] for v in ep["votes"]} == _EXPECTED

    def test_vote_summary_in_episode(self, db):
        _record(db, action="HOLD")
        ep = list_episodes(db, limit=1)[0]
        vs = ep["vote_summary"]
        assert set(vs["strategies"]) == _EXPECTED
        assert vs["buy_vote_count"] + vs["sell_vote_count"] + vs["hold_vote_count"] == 4
        assert vs["top_strategy"] in _EXPECTED


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db):
    def _odb():
        yield db
    # seed
    _record(db, action="HOLD")
    app.dependency_overrides[get_db] = _odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestApi:
    def test_detail_has_full_votes(self, client, db):
        eid = list_episodes(db, limit=1)[0]["episode_id"]
        r = client.get(f"/api/agents/decision-episodes/{eid}")
        assert r.status_code == 200
        b = r.json()
        assert {v["strategy"] for v in b["votes"]} == _EXPECTED
        assert b["is_live_authorization"] is False

    def test_list_has_vote_summary(self, client):
        b = client.get("/api/agents/decision-episodes").json()
        ep = b["episodes"][0]
        assert "vote_summary" in ep
        assert set(ep["vote_summary"]["strategies"]) == _EXPECTED

    def test_summary_by_strategy_and_signal(self, client):
        b = client.get("/api/agents/decision-episodes").json()
        s = b["summary"]
        assert set(s["by_strategy"].keys()) == _EXPECTED
        assert "by_signal" in s

    def test_no_secret_in_response(self, client):
        raw = client.get("/api/agents/decision-episodes").text.lower()
        for banned in ("app_secret", "kis_app_secret", "account_no", "access_token"):
            assert banned not in raw


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_council_no_broker_route_order_import(self):
        text = Path(council_mod.__file__).resolve().read_text(encoding="utf-8")
        for pat in (r"^from app\.brokers", r"^import app\.brokers",
                    r"route_order\s*\(", r"OrderExecutor\s*\("):
            assert not re.search(pat, text, re.MULTILINE), f"agent_council 금지: /{pat}/"

    def test_episode_module_no_broker_import(self):
        text = Path(de_mod.__file__).resolve().read_text(encoding="utf-8")
        for pat in (r"from app\.brokers", r"from app\.execution",
                    r"route_order\s*\(", r"OrderExecutor\s*\("):
            assert not re.search(pat, text), f"decision_episode 금지: /{pat}/"
