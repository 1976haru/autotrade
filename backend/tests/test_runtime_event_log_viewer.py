"""#56 / 7-04 — 통합 오류/이벤트 로그 뷰어 테스트.

검증:
- redact_text 가 secret/계좌 패턴을 [REDACTED] 로 마스킹 (드롭 X)
- `GET /api/system/logs` 가 RuntimeEvent / AgentDecision / KIS order 를 병합
- source / severity / keyword 필터 동작
- 최근 100건 상한
- 응답·메시지에 secret/계좌 원문 0건 (free-text 마스킹)
- broker.place_order 호출 0건 (read-only)
- contains_secret=False / is_live_authorization=False 불변
"""

from __future__ import annotations

import json

from app.system import log_viewer as lv


# ── redact_text 단위 ─────────────────────────────────────────────────────────


def test_redact_masks_korean_account():
    out = lv.redact_text("주문계좌 12345678-01 거부됨")
    assert "12345678-01" not in out
    assert "[REDACTED]" in out


def test_redact_masks_tokens():
    for secret in [
        "token sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        "key sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAA",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123",
        "Bearer abcdefghijklmnopqrstuvwxyz0123",
        "access_token=ABCDEF0123456789abcd",
    ]:
        out = lv.redact_text(secret)
        assert "[REDACTED]" in out, secret


def test_redact_keeps_clean_text():
    assert lv.redact_text("조건에 맞는 신호 없음") == "조건에 맞는 신호 없음"


def test_redact_handles_none_and_nonstring():
    assert lv.redact_text(None) == ""
    assert lv.redact_text(123) == "123"


# ── 통합 수집 (collect_recent_logs) ──────────────────────────────────────────


def test_collect_limit_capped_at_100():
    res = lv.collect_recent_logs(db=None, limit=9999)
    assert res["limit"] == 100


def test_collect_safety_invariants():
    res = lv.collect_recent_logs(db=None)
    assert res["is_live_authorization"] is False
    assert res["contains_secret"] is False
    assert "logs" in res and "summary" in res


# ── endpoint 통합 (seed 후 조회) ─────────────────────────────────────────────


def _seed_event(**kw):
    from app.system.event_log import log_event
    return log_event(**kw)


def _seed_episode(db, **kw):
    from app.agents.decision_episode import record_episode
    ep = record_episode(db, **kw)
    db.commit()
    return ep


def _seed_order(db, **kw):
    from app.db.models import OrderAuditLog
    base = dict(mode="PAPER", symbol="005930", side="BUY", quantity=1,
                order_type="MARKET", latest_price=70000, decision="REJECTED",
                reasons=[], message="", broker_order_id="K-TEST-1")
    base.update(kw)
    row = OrderAuditLog(**base)
    db.add(row)
    db.commit()
    return row


def test_logs_endpoint_shows_runtime_event(client):
    from app.system.event_log import reset_runtime_event_log_for_tests
    reset_runtime_event_log_for_tests()
    _seed_event(level="ERROR", category="BACKEND", code="BACKEND_FAIL",
                message="backend 연결 실패")
    body = client.get("/api/system/logs?source=RUNTIME_EVENT").json()
    msgs = [e["message"] for e in body["logs"]]
    assert any("backend 연결 실패" in m for m in msgs)
    assert all(e["source"] == "RUNTIME_EVENT" for e in body["logs"])


def test_logs_endpoint_shows_agent_decision(client):
    db = client.test_db_factory()
    try:
        _seed_episode(db, episode_id="ep-log-1", final_action="HOLD",
                      symbol="005930", reason_code="NO_SIGNAL")
    finally:
        db.close()
    body = client.get("/api/system/logs?source=AGENT_DECISION").json()
    found = [e for e in body["logs"] if e["episode_id"] == "ep-log-1"]
    assert found, "decision episode not in logs"
    assert found[0]["action"] == "HOLD"
    assert found[0]["reason_code"] == "NO_SIGNAL"


def test_logs_endpoint_shows_kis_order_with_broker_order_no(client):
    db = client.test_db_factory()
    try:
        _seed_order(db, decision="REJECTED", message="주문 거절: 한도 초과",
                    broker_order_id="K-99")
    finally:
        db.close()
    body = client.get("/api/system/logs?source=KIS_ORDER").json()
    found = [e for e in body["logs"] if e["broker_order_no"] == "K-99"]
    assert found, "KIS order not in logs"
    assert found[0]["severity"] == "WARN"  # REJECTED → WARN


def test_logs_endpoint_redacts_account_in_message(client):
    db = client.test_db_factory()
    try:
        _seed_order(db, message="계좌 12345678-01 주문 거절", broker_order_id="K-RED")
    finally:
        db.close()
    text = json.dumps(client.get("/api/system/logs?source=KIS_ORDER").json(),
                      ensure_ascii=False)
    assert "12345678-01" not in text
    assert "[REDACTED]" in text


def test_logs_endpoint_severity_filter(client):
    from app.system.event_log import reset_runtime_event_log_for_tests
    reset_runtime_event_log_for_tests()
    _seed_event(level="INFO", category="SYSTEM", code="A", message="info msg")
    _seed_event(level="ERROR", category="SYSTEM", code="B", message="error msg")
    body = client.get("/api/system/logs?source=RUNTIME_EVENT&severity=ERROR").json()
    assert all(e["severity"] == "ERROR" for e in body["logs"])
    assert any("error msg" in e["message"] for e in body["logs"])


def test_logs_endpoint_keyword_filter(client):
    from app.system.event_log import reset_runtime_event_log_for_tests
    reset_runtime_event_log_for_tests()
    _seed_event(level="WARN", category="MARKET_DATA", code="NO_DATA",
                message="시장 데이터 없음 NO_MARKET_DATA")
    _seed_event(level="INFO", category="SYSTEM", code="OK", message="정상")
    body = client.get("/api/system/logs?q=NO_MARKET_DATA").json()
    assert body["count"] >= 1
    assert all("no_market_data" in
               (e["message"] + (e["reason_code"] or "")).lower()
               for e in body["logs"])


def test_logs_endpoint_all_sources_merged(client):
    from app.system.event_log import reset_runtime_event_log_for_tests
    reset_runtime_event_log_for_tests()
    _seed_event(level="INFO", category="SYSTEM", code="X", message="ev")
    db = client.test_db_factory()
    try:
        _seed_episode(db, episode_id="ep-all", final_action="BUY", symbol="005930",
                      reason_code="SIGNAL")
        _seed_order(db, broker_order_id="K-ALL", message="ok")
    finally:
        db.close()
    body = client.get("/api/system/logs?limit=100").json()
    sources = {e["source"] for e in body["logs"]}
    assert "RUNTIME_EVENT" in sources
    assert "AGENT_DECISION" in sources
    assert "KIS_ORDER" in sources
    assert body["count"] <= 100


def test_logs_endpoint_no_broker_order(client):
    client.get("/api/system/logs")
    assert len(client.test_broker.orders) == 0


def test_logs_endpoint_safety_invariants(client):
    body = client.get("/api/system/logs").json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False


def test_logs_endpoint_no_secret_strings(client):
    text = json.dumps(client.get("/api/system/logs").json(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "Bearer ", "sk-ant-", "refresh_token="):
        assert forbidden not in text
