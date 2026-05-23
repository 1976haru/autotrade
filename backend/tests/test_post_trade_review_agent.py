"""P-27: PostTradeReviewAgent 테스트.

review_episode 의 10개 태그 산출 + 등급 + 개선 제안 + DATA_INSUFFICIENT +
episode.review 저장 + summary 집계 + secret 미노출 + 정적 가드 + 결정성.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.agents.post_trade_review import (
    AVOIDED_LOSS,
    BAD_DECISION,
    EARLY_ENTRY,
    FALSE_BREAKOUT,
    GOOD_DECISION,
    GRADE_BAD,
    GRADE_DATA_INSUFFICIENT,
    GRADE_GOOD,
    IMPROVE_EXIT_TIMING,
    LATE_EXIT,
    MISSED_OPPORTUNITY,
    RISK_SAVED_TRADE,
    STATUS_COMPLETE,
    STATUS_DATA_INSUFFICIENT,
    WEAK_SIGNAL_ENTRY,
    PostTradeReview,
    review_episode,
    review_summary_for,
)

_MODULE = Path(__file__).resolve().parents[1] / "app" / "agents" / "post_trade_review.py"


def _ep(action, outcome, **kw):
    return dict(final_action=action, outcome=outcome, **kw)


# ── 1~10. 각 태그 산출 ──

def test_good_decision_tag():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 1.2, "max_favorable_excursion": 1.4},
                            selected_strategies=["MOMENTUM", "VWAP"]))
    assert r.grade == GRADE_GOOD
    assert GOOD_DECISION in r.tags
    assert r.primary_tag == GOOD_DECISION


def test_bad_decision_tag():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "LOSS",
                                   "return_close": -1.5, "max_favorable_excursion": 0.1},
                            confidence=80, quality_score=80, selected_strategies=["MOMENTUM"]))
    assert r.grade == GRADE_BAD
    assert BAD_DECISION in r.tags
    assert r.primary_tag == BAD_DECISION


def test_late_exit_and_improve_exit_timing_tags():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 0.3, "max_favorable_excursion": 2.5},
                            selected_strategies=["VWAP"]))
    assert LATE_EXIT in r.tags
    assert IMPROVE_EXIT_TIMING in r.tags


def test_early_entry_tag():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_5m": -0.8, "return_close": 0.9,
                                   "max_favorable_excursion": 1.0}))
    assert EARLY_ENTRY in r.tags


def test_false_breakout_tag():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "LOSS",
                                   "return_5m": -0.7, "return_close": -1.0},
                            confidence=80, quality_score=80, selected_strategies=["ORB"]))
    assert FALSE_BREAKOUT in r.tags
    assert r.primary_tag == FALSE_BREAKOUT


def test_missed_opportunity_tag():
    r = review_episode(_ep("HOLD", {"status": "COMPLETE", "label": "MISSED_OPPORTUNITY",
                                    "return_close": 1.5}))
    assert MISSED_OPPORTUNITY in r.tags
    assert r.grade == GRADE_BAD


def test_avoided_loss_tag():
    r = review_episode(_ep("HOLD", {"status": "COMPLETE", "label": "AVOIDED_LOSS",
                                    "return_close": -1.2}))
    assert AVOIDED_LOSS in r.tags
    assert r.grade == GRADE_GOOD


def test_weak_signal_entry_tag():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "LOSS",
                                   "return_close": -0.8},
                            confidence=40, quality_score=45, selected_strategies=["GAP"]))
    assert WEAK_SIGNAL_ENTRY in r.tags


def test_risk_saved_trade_tag():
    r = review_episode(_ep("SELL", {"status": "COMPLETE", "label": "PROFITABLE",
                                    "return_close": 1.0},
                            council={"sell_reason": {"reason_code": "STOP_LOSS"}}))
    assert RISK_SAVED_TRADE in r.tags
    assert r.primary_tag == RISK_SAVED_TRADE
    assert r.grade == GRADE_GOOD


def test_improve_exit_timing_in_suggestions():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 0.3, "max_favorable_excursion": 2.5}))
    assert any("청산" in s or "익절" in s for s in r.improvement_suggestions)


# ── 11. DATA_INSUFFICIENT ──

def test_data_insufficient_when_outcome_pending():
    r = review_episode(_ep("BUY", {"status": "PENDING"}))
    assert r.review_status == STATUS_DATA_INSUFFICIENT
    assert r.grade == GRADE_DATA_INSUFFICIENT
    assert r.primary_tag is None


def test_data_insufficient_when_outcome_missing():
    assert review_episode(_ep("BUY", None)).review_status == STATUS_DATA_INSUFFICIENT


def test_data_insufficient_when_unavailable():
    r = review_episode(_ep("SELL", {"status": "UNAVAILABLE"}))
    assert r.review_status == STATUS_DATA_INSUFFICIENT


# ── 개선 제안 / 요약 ──

def test_improvement_suggestions_present_for_negative():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "LOSS",
                                   "return_close": -1.0},
                            confidence=40, quality_score=45, selected_strategies=["ORB"],
                            return_5m=-0.6))
    assert r.improvement_suggestions
    assert r.summary


def test_complete_status_for_real_outcome():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 1.0}))
    assert r.review_status == STATUS_COMPLETE


# ── invariant / 결정성 ──

def test_invariants_locked():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 1.0}))
    assert r.is_order_signal is False
    assert r.is_live_authorization is False
    assert r.auto_apply_allowed is False
    assert r.contains_secret is False
    d = r.to_dict()
    assert d["is_order_signal"] is False
    assert d["is_live_authorization"] is False
    assert d["auto_apply_allowed"] is False


def test_invariant_guard_rejects_true():
    with pytest.raises(ValueError):
        PostTradeReview(review_status=STATUS_COMPLETE, grade=GRADE_GOOD,
                        is_order_signal=True)
    with pytest.raises(ValueError):
        PostTradeReview(review_status=STATUS_COMPLETE, grade=GRADE_GOOD,
                        auto_apply_allowed=True)


def test_deterministic():
    ep = _ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                     "return_close": 0.3, "max_favorable_excursion": 2.5})
    assert review_episode(ep).to_dict() == review_episode(ep).to_dict()


def test_review_summary_helper():
    r = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 1.0}))
    s = review_summary_for(r.to_dict())
    assert set(s) == {"review_status", "grade", "primary_tag", "summary"}
    assert review_summary_for(None) == {}


# ── episode 연결 (DB) ──

@pytest.fixture()
def db_session():
    from app.db.session import SessionLocal
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def test_attach_review_and_surface(db_session):
    from app.agents.decision_episode import (
        attach_review,
        episode_to_dict,
        get_episode,
        new_episode_id,
        record_episode,
        summarize_episodes,
    )
    eid = new_episode_id()
    row = record_episode(
        db_session, episode_id=eid, final_action="BUY", symbol="005930",
        outcome={"status": "COMPLETE", "label": "PROFITABLE", "return_close": 1.2,
                 "max_favorable_excursion": 1.4},
        council={"selected_strategies": ["MOMENTUM", "VWAP"]},
    )
    db_session.commit()
    review = review_episode(episode_to_dict(row)).to_dict()
    attach_review(db_session, eid, review)
    db_session.commit()
    d = get_episode(db_session, eid)
    assert d["review"]["grade"] == GRADE_GOOD
    assert d["review_summary"]["grade"] == GRADE_GOOD
    assert d["review_summary"]["primary_tag"] == GOOD_DECISION
    # summary 집계.
    summ = summarize_episodes(db_session, limit=200)
    assert summ["by_review_grade"].get(GRADE_GOOD, 0) >= 1
    assert summ["by_review_tag"].get(GOOD_DECISION, 0) >= 1


# ── API ──

def test_review_episode_api(db_session):
    from fastapi.testclient import TestClient

    from app.agents.decision_episode import new_episode_id, record_episode
    from app.main import app
    eid = new_episode_id()
    record_episode(
        db_session, episode_id=eid, final_action="BUY", symbol="005930",
        outcome={"status": "COMPLETE", "label": "PROFITABLE", "return_close": 1.2},
    )
    db_session.commit()
    c = TestClient(app)
    r = c.post(f"/api/agents/decision-episodes/{eid}/review")
    assert r.status_code == 200
    body = r.json()
    assert body["review"]["grade"] == GRADE_GOOD
    assert body["is_order_signal"] is False
    assert body["is_live_authorization"] is False


def test_review_api_404():
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    assert c.post("/api/agents/decision-episodes/nope-xyz/review").status_code == 404


def test_review_completed_api(db_session):
    from fastapi.testclient import TestClient

    from app.agents.decision_episode import new_episode_id, record_episode
    from app.main import app
    record_episode(db_session, episode_id=new_episode_id(), final_action="BUY",
                   symbol="005930",
                   outcome={"status": "COMPLETE", "label": "PROFITABLE", "return_close": 1.0})
    record_episode(db_session, episode_id=new_episode_id(), final_action="HOLD",
                   symbol="000660", outcome={"status": "PENDING"})
    db_session.commit()
    c = TestClient(app)
    r = c.post("/api/agents/decision-episodes/review-completed?limit=100")
    assert r.status_code == 200
    body = r.json()
    assert body["reviewed_count"] >= 1
    assert body["skipped_count"] >= 1  # PENDING 은 skip.
    assert body["is_order_signal"] is False


# ── secret 미노출 / 정적 가드 ──

def test_review_to_dict_no_secret_keys():
    d = review_episode(_ep("BUY", {"status": "COMPLETE", "label": "PROFITABLE",
                                   "return_close": 1.0},
                           market_snapshot={"app_secret": "x", "account_no": "999"})).to_dict()
    for bad in ("app_secret", "account_no", "access_token", "api_key"):
        assert bad not in [k.lower() for k in d.keys()]
    assert "app_secret" not in str(d).lower()


def test_module_no_forbidden_imports():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    forbidden = ("app.brokers", "app.execution", "broker", "httpx", "requests",
                 "anthropic", "openai", "app.ai.assist", "app.ai.client")
    for imp in imported:
        for bad in forbidden:
            assert not imp.startswith(bad), f"forbidden import: {imp}"


def test_module_no_order_code_symbols():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for bad in ("place_order", "cancel_order", "route_order", "OrderExecutor",
                "OrderRequest"):
        assert bad not in names, f"forbidden code symbol: {bad}"
