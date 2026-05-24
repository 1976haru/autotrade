"""#50 / 6-05: PostTradeReview 피드백 루프 테스트.

핵심 invariant:
- 승리/패배 요인 집계, 전략별 오류 집계.
- OVER_ENTRY / LATE_EXIT / LOW_DATA_QUALITY / MARKET_REGIME_MISMATCH 감지.
- threshold recommendation 생성 (반복 시) — auto_apply_allowed=false /
  requires_operator_approval=true.
- is_order_signal=false / is_live_authorization=false / contains_secret=false.
- broker/OrderExecutor/route_order import·호출 0건, DB write 0건, secret 노출 0건.
"""

from __future__ import annotations

import json

import pytest

from app.agents import post_trade_feedback as ptf
from app.agents.post_trade_feedback import (
    LATE_EXIT,
    LOSING_SETUP,
    LOW_DATA_QUALITY,
    MARKET_REGIME_MISMATCH,
    OVER_ENTRY,
    WINNING_SETUP,
    build_feedback_loop,
    feedback_penalty_for,
)


def _summary(**over):
    base = {
        "by_review_grade": {"GOOD": 6, "BAD": 4, "NEUTRAL": 2},
        "by_review_tag": {"WEAK_SIGNAL_ENTRY": 4, "LATE_EXIT": 3, "FALSE_BREAKOUT": 1},
        "by_outcome_label": {"PROFITABLE": 6, "LOSS": 4, "MISSED_OPPORTUNITY": 2},
        "by_data_status": {"OK": 8, "STALE": 3},
        "by_reason_code": {"PRICE_STALE": 3, "BLOCK_NEW_BUY": 3},
        "by_strategy": {"ORB": 5, "MOMENTUM": 4},
    }
    base.update(over)
    return base


def _tag(report, name):
    return next((t for t in report.feedback_tags if t["tag"] == name), None)


# ── 1~3. 승리/패배/전략 오류 집계 ────────────────────────────────────────────


def test_win_loss_neutral_counts():
    r = build_feedback_loop(_summary())
    assert r.win_count == 6
    assert r.loss_count == 4
    assert r.neutral_count == 2


def test_winning_and_losing_setup_tags():
    r = build_feedback_loop(_summary())
    assert _tag(r, WINNING_SETUP) is not None
    assert _tag(r, LOSING_SETUP) is not None


def test_strategy_errors_carried():
    r = build_feedback_loop(_summary(), strategy_errors={"ORB": 3})
    assert r.strategy_errors == {"ORB": 3}


# ── 4~7. 감지 ────────────────────────────────────────────────────────────────


def test_over_entry_detected():
    r = build_feedback_loop(_summary())
    t = _tag(r, OVER_ENTRY)
    assert t is not None and t["count"] >= 4


def test_late_exit_detected():
    r = build_feedback_loop(_summary())
    t = _tag(r, LATE_EXIT)
    assert t is not None and t["count"] >= 3


def test_low_data_quality_detected():
    r = build_feedback_loop(_summary())
    t = _tag(r, LOW_DATA_QUALITY)
    assert t is not None and t["count"] >= 3   # STALE 3 + PRICE_STALE 3.


def test_market_regime_mismatch_detected():
    r = build_feedback_loop(_summary())
    t = _tag(r, MARKET_REGIME_MISMATCH)
    assert t is not None and t["count"] >= 3


# ── 8~10. threshold recommendation + invariants ──────────────────────────────


def test_threshold_recommendations_generated():
    r = build_feedback_loop(_summary())
    codes = {rec["reason_code"] for rec in r.threshold_recommendations}
    # 반복(>=3)인 실패 태그가 추천으로 생성.
    assert LATE_EXIT in codes
    assert OVER_ENTRY in codes
    assert LOW_DATA_QUALITY in codes


def test_recommendations_not_auto_applied():
    r = build_feedback_loop(_summary())
    for rec in r.threshold_recommendations:
        assert rec["auto_apply_allowed"] is False
        assert rec["requires_operator_approval"] is True


def test_report_invariants():
    r = build_feedback_loop(_summary())
    assert r.auto_apply_allowed is False
    assert r.requires_operator_approval is True
    assert r.is_order_signal is False
    assert r.is_live_authorization is False
    assert r.contains_secret is False
    d = r.to_dict()
    assert d["auto_apply_allowed"] is False
    assert d["requires_operator_approval"] is True


def test_report_invariant_enforced():
    from app.agents.post_trade_feedback import FeedbackLoopReport
    with pytest.raises(ValueError):
        FeedbackLoopReport(
            status="OK", sample_count=0, win_count=0, loss_count=0, neutral_count=0,
            feedback_tags=[], strategy_errors={}, threshold_recommendations=[],
            feedback_summary="", auto_apply_allowed=True)


def test_insufficient_data():
    r = build_feedback_loop({})
    assert r.status == "INSUFFICIENT_DATA"
    assert r.sample_count == 0


def test_few_occurrences_no_recommendation():
    # LATE_EXIT 1회뿐 → 추천 생성 안 함 (recommend_min=3 미만).
    r = build_feedback_loop({"by_review_tag": {"LATE_EXIT": 1}})
    codes = {rec["reason_code"] for rec in r.threshold_recommendations}
    assert LATE_EXIT not in codes


# ── feedback penalty (quality 입력) ──────────────────────────────────────────


def test_feedback_penalty_increases_with_high_tags():
    r = build_feedback_loop(_summary())
    pen = feedback_penalty_for(r.to_dict())
    assert 0 < pen <= 30


def test_feedback_penalty_zero_when_empty():
    assert feedback_penalty_for({}) == 0
    assert feedback_penalty_for(None) == 0


# ── import / secret 가드 ─────────────────────────────────────────────────────


def test_no_forbidden_imports():
    src = open(ptf.__file__, encoding="utf-8").read()
    for tok in ("from app.brokers.kis", "from app.brokers.mock_broker",
                "from app.execution.order_router", "from app.execution.executor",
                "from app.execution.paper_trader", "import anthropic", "import openai",
                "import httpx", "import requests", ".place_order(", "route_order(",
                "db.add(", "db.commit("):
        assert tok not in src, f"forbidden token: {tok}"


def test_no_secret_in_report():
    text = json.dumps(build_feedback_loop(_summary()).to_dict(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in text


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_feedback_loop(client):
    r = client.get("/api/agents/feedback-loop")
    assert r.status_code == 200
    body = r.json()
    for k in ("feedback_tags", "threshold_recommendations", "auto_apply_allowed",
              "requires_operator_approval", "status"):
        assert k in body
    assert body["auto_apply_allowed"] is False
    assert body["requires_operator_approval"] is True
    assert body["is_live_authorization"] is False


def test_api_feedback_loop_no_broker_order(client):
    client.get("/api/agents/feedback-loop")
    assert len(client.test_broker.orders) == 0


def test_api_feedback_loop_no_secret(client):
    t = json.dumps(client.get("/api/agents/feedback-loop").json(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "access_token="):
        assert forbidden not in t
