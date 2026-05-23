"""P-25: 사후 성과 라벨링 테스트.

검증:
 - BUY/SELL/HOLD 5/10/30/60분 + 종가 수익률, MFE/MAE
 - PROFITABLE/LOSS/NEUTRAL/MISSED_OPPORTUNITY/AVOIDED_LOSS 라벨
 - PENDING/PARTIAL/COMPLETE/UNAVAILABLE 상태
 - SELL realized_pnl 우선
 - entry_price 우선순위(avg_fill_price→request_price→market_snapshot)
 - episode attach + updated_at 갱신, 상세 API outcome, 목록 outcome_summary,
   summary by_outcome_label/by_outcome_status
 - secret 0건, is_live_authorization/is_order_signal=False, broker import 0건
 - deterministic
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.agents.post_trade_outcome as pto_mod
from app.agents.decision_episode import (
    attach_outcome,
    get_episode,
    new_episode_id,
    record_episode,
    summarize_episodes,
)
from app.agents.post_trade_outcome import (
    LABEL_AVOIDED_LOSS,
    LABEL_LOSS,
    LABEL_MISSED_OPPORTUNITY,
    LABEL_PROFITABLE,
    STATUS_COMPLETE,
    STATUS_PARTIAL,
    STATUS_PENDING,
    STATUS_UNAVAILABLE,
    evaluate_outcome,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app

_MODULE = Path(pto_mod.__file__).resolve()


def _buy_ep(avg_fill=75000, req=None, snap=None):
    kor = {"order_quality": {"avg_fill_price": avg_fill}}
    if req is not None:
        kor["order_quality"] = {"request_price": req}
    ep = {"final_action": "BUY", "kis_order_result": kor}
    if snap is not None:
        ep["market_snapshot"] = {"price": snap}
    return ep


# ─────────────────────────────────────────────────────────────────────────────
# BUY
# ─────────────────────────────────────────────────────────────────────────────


class TestBuyOutcome:
    def test_pending_no_data(self):
        o = evaluate_outcome(episode=_buy_ep()).to_dict()
        assert o["status"] == STATUS_PENDING

    def test_returns_all_horizons(self):
        o = evaluate_outcome(
            episode=_buy_ep(75000),
            future_prices={5: 75240, 10: 75530, 30: 75940, 60: 75100},
            close_price=75710).to_dict()
        assert o["return_5m"] == round((75240 - 75000) / 75000 * 100, 4)
        assert o["return_10m"] is not None
        assert o["return_30m"] is not None
        assert o["return_60m"] is not None
        assert o["return_close"] is not None

    def test_mfe_mae(self):
        o = evaluate_outcome(
            episode=_buy_ep(75000),
            future_prices={5: 75240, 10: 75900, 30: 74800},
            close_price=75710).to_dict()
        assert o["max_favorable_excursion"] == max(
            o["return_5m"], o["return_10m"], o["return_30m"], o["return_close"])
        assert o["max_adverse_excursion"] == min(
            o["return_5m"], o["return_10m"], o["return_30m"], o["return_close"])

    def test_profitable_label(self):
        o = evaluate_outcome(episode=_buy_ep(75000), future_prices={5: 75500},
                             close_price=76000).to_dict()
        assert o["label"] == LABEL_PROFITABLE

    def test_loss_label(self):
        o = evaluate_outcome(episode=_buy_ep(75000), future_prices={5: 74500},
                             close_price=74000).to_dict()
        assert o["label"] == LABEL_LOSS

    def test_entry_price_priority(self):
        # avg_fill_price 우선.
        assert evaluate_outcome(episode=_buy_ep(75000), close_price=76000
                                ).to_dict()["entry_basis"] == "avg_fill_price"
        # request_price fallback.
        ep = {"final_action": "BUY",
              "kis_order_result": {"order_quality": {"request_price": 70000}}}
        assert evaluate_outcome(episode=ep, close_price=71000
                                ).to_dict()["entry_basis"] == "request_price"
        # market_snapshot fallback.
        ep2 = {"final_action": "BUY", "market_snapshot": {"price": 60000}}
        assert evaluate_outcome(episode=ep2, close_price=61000
                                ).to_dict()["entry_basis"] == "market_snapshot"


# ─────────────────────────────────────────────────────────────────────────────
# SELL / HOLD
# ─────────────────────────────────────────────────────────────────────────────


class TestSellHoldOutcome:
    def test_sell_realized_pnl_priority(self):
        ep = {"final_action": "SELL",
              "kis_order_result": {"order_quality": {"avg_fill_price": 75000}}}
        o = evaluate_outcome(episode=ep, future_prices={5: 74000},
                             close_price=73000, realized_pnl=5000).to_dict()
        assert o["realized_pnl"] == 5000.0
        assert o["label"] == LABEL_PROFITABLE  # realized_pnl > 0

    def test_sell_price_drop_is_favorable(self):
        ep = {"final_action": "SELL",
              "kis_order_result": {"order_quality": {"avg_fill_price": 75000}}}
        # 청산 후 하락 → favorable (return_close 양수).
        o = evaluate_outcome(episode=ep, close_price=73500).to_dict()
        assert o["return_close"] > 0

    def test_hold_up_missed_opportunity(self):
        ep = {"final_action": "HOLD", "market_snapshot": {"price": 70000}}
        o = evaluate_outcome(episode=ep, future_prices={5: 71000},
                             close_price=72000).to_dict()
        assert o["label"] == LABEL_MISSED_OPPORTUNITY

    def test_hold_down_avoided_loss(self):
        ep = {"final_action": "HOLD", "market_snapshot": {"price": 70000}}
        o = evaluate_outcome(episode=ep, future_prices={5: 69000},
                             close_price=68000).to_dict()
        assert o["label"] == LABEL_AVOIDED_LOSS


# ─────────────────────────────────────────────────────────────────────────────
# 상태 + 결정성
# ─────────────────────────────────────────────────────────────────────────────


class TestStatus:
    def test_partial(self):
        o = evaluate_outcome(episode=_buy_ep(75000), future_prices={5: 75100}).to_dict()
        assert o["status"] == STATUS_PARTIAL

    def test_pending_before_close(self):
        o = evaluate_outcome(episode=_buy_ep(75000), market_closed=False).to_dict()
        assert o["status"] == STATUS_PENDING

    def test_unavailable_after_close_no_data(self):
        o = evaluate_outcome(episode=_buy_ep(75000), market_closed=True).to_dict()
        assert o["status"] == STATUS_UNAVAILABLE

    def test_complete_with_close(self):
        o = evaluate_outcome(episode=_buy_ep(75000), future_prices={5: 75100},
                             close_price=75500).to_dict()
        assert o["status"] == STATUS_COMPLETE

    def test_deterministic(self):
        ep = _buy_ep(75000)
        a = evaluate_outcome(episode=ep, future_prices={5: 75300}, close_price=75800).to_dict()
        b = evaluate_outcome(episode=ep, future_prices={5: 75300}, close_price=75800).to_dict()
        assert a == b

    def test_invariants(self):
        o = evaluate_outcome(episode=_buy_ep(75000), close_price=76000).to_dict()
        assert o["contains_secret"] is False
        assert o["is_live_authorization"] is False
        assert o["is_order_signal"] is False


# ─────────────────────────────────────────────────────────────────────────────
# episode 연결 + summary
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


class TestEpisodeIntegration:
    def test_attach_updates_updated_at(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="005930",
                       kis_order_result={"order_quality": {"avg_fill_price": 75000}})
        db.commit()
        before = get_episode(db, eid)["updated_at"]
        o = evaluate_outcome(episode=get_episode(db, eid),
                             future_prices={5: 75300}, close_price=75800).to_dict()
        attach_outcome(db, eid, o)
        db.commit()
        ep = get_episode(db, eid)
        assert ep["outcome"]["label"] == LABEL_PROFITABLE
        assert ep["outcome_summary"]["status"] == STATUS_COMPLETE
        assert ep["updated_at"] >= before

    def test_market_data_missing_no_failure(self, db):
        eid = new_episode_id()
        record_episode(db, episode_id=eid, final_action="BUY", symbol="X")
        db.commit()
        o = evaluate_outcome(episode=get_episode(db, eid)).to_dict()
        attach_outcome(db, eid, o)
        db.commit()
        assert get_episode(db, eid)["outcome"]["status"] == STATUS_PENDING

    def test_summary_by_outcome(self, db):
        for fp, cp, act in [({5: 75500}, 76000, "BUY"), ({5: 74500}, 74000, "BUY")]:
            eid = new_episode_id()
            record_episode(db, episode_id=eid, final_action=act, symbol="X",
                           kis_order_result={"order_quality": {"avg_fill_price": 75000}})
            o = evaluate_outcome(episode=get_episode(db, eid),
                                 future_prices=fp, close_price=cp).to_dict()
            attach_outcome(db, eid, o)
        db.commit()
        s = summarize_episodes(db)
        assert "by_outcome_label" in s and "by_outcome_status" in s
        assert s["by_outcome_label"].get("PROFITABLE", 0) >= 1
        assert s["by_outcome_label"].get("LOSS", 0) >= 1
        assert s["by_outcome_status"].get("COMPLETE", 0) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db):
    def _odb():
        yield db
    record_episode(db, episode_id="ep-out-1", final_action="BUY", symbol="005930",
                   kis_order_result={"order_quality": {"avg_fill_price": 75000}})
    db.commit()
    app.dependency_overrides[get_db] = _odb
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


class TestApi:
    def test_evaluate_outcome_endpoint(self, client):
        r = client.post("/api/agents/decision-episodes/ep-out-1/evaluate-outcome",
                        json={"future_prices": {"5": 75300, "30": 75900},
                              "close_price": 75800})
        assert r.status_code == 200
        b = r.json()
        assert b["outcome"]["status"] == STATUS_COMPLETE
        assert b["outcome"]["label"] == LABEL_PROFITABLE
        assert b["is_live_authorization"] is False
        assert b["is_order_signal"] is False

    def test_evaluate_outcome_404(self, client):
        assert client.post(
            "/api/agents/decision-episodes/nope/evaluate-outcome", json={}
        ).status_code == 404

    def test_detail_has_outcome_after_eval(self, client):
        client.post("/api/agents/decision-episodes/ep-out-1/evaluate-outcome",
                    json={"future_prices": {"5": 75300}, "close_price": 75800})
        b = client.get("/api/agents/decision-episodes/ep-out-1").json()
        assert b["outcome"]["label"] == LABEL_PROFITABLE
        assert b["outcome_summary"]["status"] == STATUS_COMPLETE

    def test_list_has_outcome_summary(self, client):
        b = client.get("/api/agents/decision-episodes").json()
        assert "outcome_summary" in b["episodes"][0]

    def test_no_secret_in_response(self, client):
        client.post("/api/agents/decision-episodes/ep-out-1/evaluate-outcome",
                    json={"close_price": 75800})
        raw = client.get("/api/agents/decision-episodes").text.lower()
        for banned in ("app_secret", "kis_app_secret", "account_no", "access_token"):
            assert banned not in raw


# ─────────────────────────────────────────────────────────────────────────────
# static guards
# ─────────────────────────────────────────────────────────────────────────────


class TestStaticGuards:
    def test_module_no_broker_route_order_import(self):
        text = _MODULE.read_text(encoding="utf-8")
        for pat in (r"^from app\.brokers", r"^from app\.execution",
                    r"route_order\s*\(", r"OrderExecutor\s*\(",
                    r"\bbroker\.place_order\s*\(", r"import httpx", r"import requests"):
            assert not re.search(pat, text, re.MULTILINE), f"post_trade_outcome 금지: /{pat}/"

    def test_no_secret_value_pattern(self):
        o = evaluate_outcome(episode=_buy_ep(75000), close_price=75800).to_dict()
        joined = " ".join(f"{k}={v}" for k, v in o.items()).lower()
        for banned in ("app_secret", "account_no", "access_token", "api_key"):
            assert banned not in joined
