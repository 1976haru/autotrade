"""체크리스트 #68: /api/audit/events 라우트 테스트.

검증:
  - GET /events list + 필터
  - GET /events/{id} 단건
  - POST /events (OPERATOR_NOTE만, Secret 발견 시 400)
  - PATCH /events/{id}/archive — 멱등, archive만
  - DELETE 엔드포인트 0개 (정적 검증)
  - emergency-stop API hook이 audit_event row를 자동 INSERT (감사 hook)
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import AuditEvent


def _post_note(client, payload):
    return client.post("/api/audit/events", json=payload)


def test_list_audit_events_empty_envelope(client):
    r = client.get("/api/audit/events")
    assert r.status_code == 200
    assert r.json() == []


def test_post_operator_note_creates_event(client):
    r = _post_note(client, {
        "summary": "manual review of approval #42",
        "reason":  "post-mortem",
        "symbol":  "005930",
        "actor":   "ops1",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["event_type"] == "OPERATOR_NOTE"
    assert body["severity"]   == "INFO"
    assert body["source"]     == "OPERATOR"
    assert body["actor"]      == "ops1"
    assert body["summary"]    == "manual review of approval #42"
    assert body["archived"]   is False
    eid = body["id"]

    # list에 보임
    rows = client.get("/api/audit/events").json()
    assert any(row["id"] == eid for row in rows)


def test_post_operator_note_blocks_secret_leak(client):
    r = _post_note(client, {
        "summary": "leak KIS_APP_KEY=ABCDEFG1234567",
    })
    assert r.status_code == 400
    body = r.json()
    detail = body.get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("error") == "secret_leak_blocked"
    else:
        assert "secret" in str(detail).lower()


def test_get_event_by_id_404_for_missing(client):
    r = client.get("/api/audit/events/9999")
    assert r.status_code == 404


def test_get_event_by_id_returns_full_row(client):
    create = _post_note(client, {"summary": "single"}).json()
    eid = create["id"]
    r = client.get(f"/api/audit/events/{eid}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == eid
    assert body["summary"] == "single"


def test_patch_archive_marks_row_archived(client):
    create = _post_note(client, {"summary": "to be archived"}).json()
    eid = create["id"]
    r = client.patch(f"/api/audit/events/{eid}/archive",
                     json={"archived_by": "ops1", "note": "cold storage"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"]    is True
    assert body["archived_by"] == "ops1"
    assert body["archive_note"] == "cold storage"
    assert body["archived_at"] is not None


def test_patch_archive_is_idempotent_and_preserves_first_archiver(client):
    eid = _post_note(client, {"summary": "x"}).json()["id"]
    client.patch(f"/api/audit/events/{eid}/archive",
                 json={"archived_by": "first", "note": "first note"})
    r2 = client.patch(f"/api/audit/events/{eid}/archive",
                      json={"archived_by": "second", "note": "second note"})
    assert r2.status_code == 200
    body = r2.json()
    # 멱등 — 첫 archive 정보 보존
    assert body["archived_by"]  == "first"
    assert body["archive_note"] == "first note"


def test_patch_archive_404_for_missing(client):
    r = client.patch("/api/audit/events/9999/archive", json={})
    assert r.status_code == 404


def test_list_excludes_archived_by_default(client):
    eid_keep = _post_note(client, {"summary": "keep"}).json()["id"]
    eid_arch = _post_note(client, {"summary": "archive me"}).json()["id"]
    client.patch(f"/api/audit/events/{eid_arch}/archive", json={})

    default_list = client.get("/api/audit/events").json()
    ids_default = {row["id"] for row in default_list}
    assert eid_keep in ids_default
    assert eid_arch not in ids_default

    with_arch = client.get("/api/audit/events?include_archived=true").json()
    ids_arch = {row["id"] for row in with_arch}
    assert eid_keep in ids_arch
    assert eid_arch in ids_arch


def test_list_filters_by_event_type_severity_source(client):
    _post_note(client, {"summary": "note A", "symbol": "005930"})
    _post_note(client, {"summary": "note B", "symbol": "000660"})

    r = client.get("/api/audit/events?event_type=OPERATOR_NOTE&severity=INFO&source=OPERATOR")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 2
    for row in rows:
        assert row["event_type"] == "OPERATOR_NOTE"
        assert row["severity"]   == "INFO"
        assert row["source"]     == "OPERATOR"

    # symbol 필터
    rows_symbol = client.get("/api/audit/events?symbol=005930").json()
    for row in rows_symbol:
        assert row["symbol"] == "005930"


# ====================================================================
# DELETE 엔드포인트 미존재 — 정적 검증
# ====================================================================


def test_no_delete_endpoint_for_audit_events(client):
    """DELETE /api/audit/events/* 엔드포인트는 *없어야* 한다. 본 테스트가
    실패한다면 누군가 DELETE 핸들러를 추가했다는 뜻 — invariant 위반."""
    # OpenAPI schema에서 확인
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema.get("paths", {})
    for path, methods in paths.items():
        if "/audit/events" in path:
            # 본 path에 delete 메서드가 정의되어 있으면 안 됨
            assert "delete" not in methods, (
                f"forbidden DELETE method on {path} — audit는 append-only "
                f"(archive만 허용)"
            )


# ====================================================================
# emergency-stop API hook → audit_event 자동 INSERT
# ====================================================================


def test_emergency_stop_toggle_writes_audit_event(client):
    """POST /api/risk/emergency-stop이 audit_event 테이블에 EMERGENCY_STOP
    이벤트를 자동 추가하는지 확인."""
    r = client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1",
        "reason_code": "manual_operator", "level": "LEVEL_1",
    })
    assert r.status_code == 200, r.text
    # audit_event 테이블 직접 조회
    with client.test_db_factory() as db:
        rows = db.execute(
            select(AuditEvent).where(AuditEvent.event_type == "EMERGENCY_STOP")
        ).scalars().all()
        assert len(rows) >= 1
        row = rows[0]
        assert row.severity == "CRITICAL"   # ON은 CRITICAL
        assert row.actor    == "ops1"
        assert row.reason   == "manual_operator"
        assert "ENABLED" in row.summary


def test_emergency_stop_off_writes_audit_event_with_info_severity(client):
    """emergency stop OFF는 INFO 심각도로 기록."""
    # 먼저 ON 시킴
    client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1", "reason_code": "manual_operator",
    })
    # OFF
    r = client.post("/api/risk/emergency-stop", json={
        "enabled": False, "decided_by": "ops1",
    })
    assert r.status_code == 200
    with client.test_db_factory() as db:
        rows = db.execute(
            select(AuditEvent)
            .where(AuditEvent.event_type == "EMERGENCY_STOP")
            .order_by(AuditEvent.id.desc())
            .limit(1)
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].severity == "INFO"
        assert "DISABLED" in rows[0].summary


def test_audit_hook_failure_does_not_break_emergency_stop(client, monkeypatch):
    """audit hook이 raise해도 emergency-stop API는 200 응답을 유지한다."""
    from app.api import routes_risk  # noqa: F401 — ensure import done

    def bad_log(**kwargs):  # noqa: ARG001
        raise RuntimeError("audit facade crashed")

    monkeypatch.setattr("app.audit.events.log_audit_event", bad_log)
    r = client.post("/api/risk/emergency-stop", json={
        "enabled": True, "decided_by": "ops1", "reason_code": "manual_operator",
    })
    assert r.status_code == 200
    assert r.json()["emergency_stop"] is True
