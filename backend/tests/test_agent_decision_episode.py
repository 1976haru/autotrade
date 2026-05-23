"""P-21: Agent Decision Episode 이벤트 기록 테스트.

검증:
 - BUY/SELL/HOLD episode 생성 + symbol/risk_profile/market_snapshot/votes/council
   저장
 - RiskManager/PermissionGate/KIS Paper 결과 연결 + broker_order_no/audit_id/
   decision_log_id 연결
 - outcome placeholder + attach_outcome
 - secret sanitize fail-closed
 - is_live_authorization=False invariant
 - read-only API
 - kis_paper_auto_tick 통합 시 episode 기록 + decision_episode_id carry
 - 정적 grep: 저장 모듈 broker/route_order/OrderExecutor import 0건
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agents.decision_episode as de
from app.agents.agent_memory import SecretLeakError
from app.agents.decision_episode import (
    attach_outcome,
    get_episode,
    list_episodes,
    new_episode_id,
    record_episode,
    summarize_episodes,
)
from app.brokers.mock_broker import MockBrokerAdapter
from app.db.base import Base
from app.db.models import AgentDecisionEpisode
from app.db.session import get_db
from app.kis_paper.auto_executor import KisPaperAutoDecision
from app.kis_paper.driver_bridge import kis_paper_auto_tick
from app.main import app
from app.risk.risk_manager import RiskDecision

_MODULE = Path(de.__file__).resolve()
OPEN_TIME = datetime(2026, 5, 27, 5, 0, 0, tzinfo=timezone.utc)  # 14:00 KST


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


# ─────────────────────────────────────────────────────────────────────────────
# store — BUY/SELL/HOLD + fields
# ─────────────────────────────────────────────────────────────────────────────


def _council(action="BUY"):
    return {
        "final_action": action, "selected_strategies": ["MOMENTUM", "VWAP"],
        "risk_profile": "BALANCED", "market_regime": "TREND_UP",
        "votes": [{"strategy": "MOMENTUM", "signal": action, "score": 80}],
    }


class TestStore:
    @pytest.mark.parametrize("action", ["BUY", "SELL", "HOLD"])
    def test_episode_created_per_action(self, db, action):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action=action, symbol="005930",
                       council=_council(action))
        db.commit()
        ep = get_episode(db, eid)
        assert ep is not None
        assert ep["final_action"] == action
        assert ep["symbol"] == "005930"

    def test_episode_stores_market_snapshot_and_risk_profile(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       market_snapshot={"symbol": "005930", "current_price": 75000,
                                        "market_regime": "TREND_UP"},
                       council=_council("BUY"))
        db.commit()
        ep = get_episode(db, eid)
        assert ep["market_snapshot"]["current_price"] == 75000
        assert ep["council"]["risk_profile"] == "BALANCED"

    def test_episode_stores_votes(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="X",
                       votes=[{"strategy": "ORB", "signal": "BUY", "score": 70},
                              {"strategy": "GAP", "signal": "HOLD", "score": 40}])
        db.commit()
        assert len(get_episode(db, eid)["votes"]) == 2

    def test_episode_connects_risk_permission_kis_results(self, db):
        eid = new_episode_id()
        record_episode(
            db, episode_id=eid, final_action="BUY", symbol="005930",
            reason_code="KIS_PAPER_SUBMITTED",
            risk_result={"reason_code": "KIS_PAPER_SUBMITTED", "approved": True},
            permission_result={"needs_approval": False},
            kis_order_result={"reason_code": "KIS_PAPER_SUBMITTED", "submitted": True,
                              "broker_order_no": "PAPER-9", "broker_order_type": "KIS_PAPER"},
            broker_order_no="PAPER-9", audit_id=11, decision_log_id=22)
        db.commit()
        ep = get_episode(db, eid)
        assert ep["risk_result"]["approved"] is True
        assert ep["permission_result"]["needs_approval"] is False
        assert ep["kis_order_result"]["submitted"] is True
        assert ep["broker_order_no"] == "PAPER-9"
        assert ep["audit_id"] == 11 and ep["decision_log_id"] == 22

    def test_outcome_placeholder_and_attach(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="X")
        db.commit()
        assert get_episode(db, eid)["outcome"] is None  # placeholder
        attach_outcome(db, eid, {"pnl": 5000, "label": "WIN"})
        db.commit()
        assert get_episode(db, eid)["outcome"]["label"] == "WIN"

    def test_invariant_is_live_authorization_false(self, db):
        eid = new_episode_id()
        row = record_episode(db, episode_id=eid, final_action="BUY", symbol="X")
        db.commit()
        assert row.is_live_authorization is False
        assert get_episode(db, eid)["is_live_authorization"] is False

    def test_secret_sanitize_fail_closed(self, db):
        with pytest.raises(SecretLeakError):
            record_episode(db, episode_id=new_episode_id(), final_action="BUY",
                           symbol="X", council={"note": "key sk-ant-abcdefghijklmnopqrst"})

    def test_no_secret_value_persisted(self, db):
        # 정상 데이터만 저장 — DB raw 에 secret 패턴 0건.
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       council=_council("BUY"))
        db.commit()
        raw = str(db.query(AgentDecisionEpisode).filter_by(episode_id=eid).one().__dict__)
        assert not re.search(r"sk-[A-Za-z0-9]{16}|\b\d{6,}-\d{2,}\b", raw)

    def test_list_and_summarize(self, db):
        for a in ("BUY", "SELL", "HOLD", "BUY"):
            record_episode(db, episode_id=new_episode_id(), final_action=a, symbol="X",
                           reason_code="KIS_PAPER_SUBMITTED" if a != "HOLD" else "NO_STRATEGY_SIGNAL")
        db.commit()
        assert len(list_episodes(db, limit=10)) == 4
        assert len(list_episodes(db, limit=10, action="BUY")) == 2
        s = summarize_episodes(db)
        assert s["total"] == 4
        assert s["by_action"]["BUY"] == 2
        assert s["is_live_authorization"] is False


# ─────────────────────────────────────────────────────────────────────────────
# kis_paper_auto_tick 통합 — episode 기록 + decision_episode_id carry
# ─────────────────────────────────────────────────────────────────────────────


def _approved_route():
    async def _fn(**kw):
        audit = SimpleNamespace(id=1, broker_order_id="PAPER-0001",
                                broker_status="FILLED", filled_quantity=13, executed=True)
        return SimpleNamespace(decision=RiskDecision.APPROVED, reasons=[], audit=audit)
    return _fn


def _settings():
    return SimpleNamespace(
        enable_kis_paper_auto_trading=True, kis_paper_auto_order_dry_run=False,
        kis_is_paper=True, enable_live_trading=False,
        kis_paper_auto_max_order_notional=1_000_000, kis_paper_auto_max_orders_per_day=10,
        kis_paper_auto_order_window_start="09:05", kis_paper_auto_order_window_end="14:50",
        kis_paper_auto_min_confidence=0.6, kis_paper_auto_min_quality_score=60,
        market_data_provider="mock",
    )


_DEC = KisPaperAutoDecision(
    symbol="005930", side="BUY", quantity=13, price=75_000,
    selected_strategies=["MOMENTUM", "VWAP"], confidence=0.74, quality_score=82,
    entry_reason="vol+momentum", has_exit_plan=True,
    exit_plan={"stop_loss_pct": 1.5, "take_profit_pct": 3.0},
)


class TestTickIntegration:
    def test_tick_records_episode_and_carries_id(self, engine):
        Session = sessionmaker(bind=engine)
        out = asyncio.run(kis_paper_auto_tick(
            session_factory=Session, broker=MockBrokerAdapter(), risk=object(),
            decision=_DEC, route_order_fn=_approved_route(),
            settings=_settings(), now=OPEN_TIME,
        ))
        assert "decision_episode_id" in out
        eid = out["decision_episode_id"]
        q = Session()
        try:
            ep = get_episode(q, eid)
        finally:
            q.close()
        assert ep is not None
        assert ep["symbol"] == "005930"
        assert ep["final_action"] == "BUY"
        assert ep["is_live_authorization"] is False
        # kis_order_result 연결 확인.
        assert ep["kis_order_result"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# read-only API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(engine):
    Session = sessionmaker(bind=engine)

    def _odb():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _odb
    # seed an episode.
    seed = Session()
    record_episode(seed, episode_id="ep-test-001", final_action="BUY", symbol="005930",
                   reason_code="KIS_PAPER_SUBMITTED", broker_order_no="PAPER-1",
                   council=_council("BUY"))
    seed.commit()
    seed.close()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestApi:
    def test_list_endpoint(self, client):
        r = client.get("/api/agents/decision-episodes")
        assert r.status_code == 200
        b = r.json()
        assert b["count"] >= 1
        assert b["is_live_authorization"] is False
        assert b["summary"]["total"] >= 1

    def test_detail_endpoint(self, client):
        r = client.get("/api/agents/decision-episodes/ep-test-001")
        assert r.status_code == 200
        assert r.json()["final_action"] == "BUY"

    def test_detail_404(self, client):
        assert client.get("/api/agents/decision-episodes/nope").status_code == 404

    def test_no_secret_in_response(self, client):
        raw = client.get("/api/agents/decision-episodes").text.lower()
        for banned in ("app_secret", "kis_app_secret", "account_no", "access_token"):
            assert banned not in raw


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_store_module_no_broker_executor_import(self):
        text = _MODULE.read_text(encoding="utf-8")
        for pat in (
            r"from app\.brokers", r"import app\.brokers",
            r"from app\.execution", r"route_order\s*\(",
            r"OrderExecutor\s*\(", r"\bbroker\.place_order\s*\(",
            r"import httpx", r"import requests",
        ):
            assert not re.search(pat, text), f"decision_episode.py 금지 패턴: /{pat}/"

    def test_store_module_no_delete(self):
        text = _MODULE.read_text(encoding="utf-8")
        for pat in (r"db\.delete\(", r"DELETE FROM", r"\.query\(.*\)\.delete\("):
            assert not re.search(pat, text), f"decision_episode.py DELETE 의심: /{pat}/"
