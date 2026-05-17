"""체크리스트 #70 — MonitoringService / routes / api metrics.

CLAUDE.md 절대 원칙 invariant 강제:
- 모니터링 모듈이 broker / OrderExecutor / route_order / live order API를
  *import하지 않는다*.
- DB는 read-only — INSERT/UPDATE/DELETE 0건.
- 응답에 Secret 패턴이 새지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from app.db.models import EmergencyStopEvent, OrderAuditLog, PendingApproval
from app.monitoring.api_metrics import ApiMetricsRegistry, get_api_metrics
from app.monitoring.service import (
    MonitoringService,
    notify_alerts,
)
from app.monitoring.types import (
    AlertCandidate,
    Metric,
    MetricStatus,
    MonitoringSnapshot,
)


# ---------- helpers ----------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _insert_order(
    db,
    *,
    decision: str,
    minutes_ago: int = 1,
    symbol: str = "005930",
) -> None:
    row = OrderAuditLog(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        mode="SIMULATION",
        requested_by_ai=False,
        symbol=symbol,
        side="BUY",
        quantity=10,
        order_type="MARKET",
        latest_price=70_000,
        decision=decision,
        reasons=[],
        executed=False,
        message="test",
    )
    db.add(row)
    db.commit()


def _insert_pending(db, *, minutes_ago: int) -> None:
    audit = OrderAuditLog(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        mode="LIVE_MANUAL_APPROVAL",
        requested_by_ai=False,
        symbol="005930",
        side="BUY",
        quantity=10,
        order_type="MARKET",
        latest_price=70_000,
        decision="NEEDS_APPROVAL",
        reasons=[],
        executed=False,
        message="pending",
    )
    db.add(audit)
    db.commit()
    pend = PendingApproval(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        audit_id=audit.id,
        symbol="005930",
        side="BUY",
        quantity=10,
        order_type="MARKET",
        mode="LIVE_MANUAL_APPROVAL",
        status="PENDING",
    )
    db.add(pend)
    db.commit()


def _insert_emergency(db, *, minutes_ago: int) -> None:
    row = EmergencyStopEvent(
        created_at=_utcnow() - timedelta(minutes=minutes_ago),
        enabled=True,
        decided_by="test",
        note="test",
    )
    db.add(row)
    db.commit()


# ---------- MetricStatus / DTOs ----------


def test_metric_status_worst_orders_critical_above_all():
    assert MetricStatus.worst([MetricStatus.OK, MetricStatus.WARN]) is MetricStatus.WARN
    assert MetricStatus.worst([MetricStatus.OK, MetricStatus.CRITICAL]) is MetricStatus.CRITICAL
    assert MetricStatus.worst([MetricStatus.UNKNOWN, MetricStatus.OK]) is MetricStatus.UNKNOWN
    assert MetricStatus.worst([]) is MetricStatus.UNKNOWN


def test_metric_to_dict_contains_status_value_and_iso_time():
    m = Metric(name="x", status=MetricStatus.OK, value=1, message="ok")
    d = m.to_dict()
    assert d["status"] == "OK"
    assert d["value"] == 1
    assert "T" in d["measured_at"]


def test_alert_candidate_to_dict_round_trip():
    a = AlertCandidate(
        severity="WARN", kind="x", title="t", message="m", dedupe_key="k",
    )
    d = a.to_dict()
    assert d == {
        "severity": "WARN", "kind": "x",
        "title": "t", "message": "m", "dedupe_key": "k",
    }


# ---------- ApiMetricsRegistry ----------


def test_api_metrics_records_calls_and_errors():
    reg = ApiMetricsRegistry(max_records=100, default_window_seconds=300)
    reg.record(path="/api/x", method="GET", status_code=200, latency_ms=10.0)
    reg.record(path="/api/x", method="GET", status_code=500, latency_ms=20.0)
    reg.record(path="/api/y", method="POST", status_code=200, latency_ms=5.0)
    snap = reg.snapshot()
    assert snap["calls"] == 3
    assert snap["errors"] == 1
    assert snap["error_rate"] == round(1 / 3, 4)
    assert snap["total_calls"] == 3
    assert snap["total_errors"] == 1


def test_api_metrics_record_does_not_raise_on_bad_inputs():
    reg = ApiMetricsRegistry()
    # status_code=None은 int 변환 실패. fail-open 보장.
    reg.record(path="/x", method="GET", status_code=None, latency_ms="bad")  # type: ignore[arg-type]
    # 정상 record는 그대로 진행.
    reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0)
    assert reg.snapshot()["calls"] == 1


def test_api_metrics_window_filters_old_records():
    reg = ApiMetricsRegistry()
    import time as _time
    now = _time.time()
    reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0, at_epoch=now - 1000)
    reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0, at_epoch=now)
    snap = reg.snapshot(window_seconds=10)
    assert snap["calls"] == 1


def test_api_metrics_reset_clears_records():
    reg = ApiMetricsRegistry()
    reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0)
    reg.reset()
    assert reg.snapshot()["calls"] == 0
    assert reg.snapshot()["total_calls"] == 0


def test_get_api_metrics_returns_singleton():
    a = get_api_metrics()
    b = get_api_metrics()
    assert a is b


# ---------- MonitoringService — individual collectors ----------


def test_collect_server_is_always_ok():
    svc = MonitoringService()
    m = svc.collect_server()
    assert m.status is MetricStatus.OK
    assert "uptime_seconds" in m.value


def _make_db_session(client):
    return client.test_db_factory()


def test_collect_db_ok_on_healthy_session(client):
    db = _make_db_session(client)
    try:
        svc = MonitoringService()
        m = svc.collect_db(db)
        assert m.status is MetricStatus.OK
        assert m.value["reachable"] is True
    finally:
        db.close()


def test_collect_db_critical_on_broken_session():
    class _BrokenDB:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db closed")

    svc = MonitoringService()
    m = svc.collect_db(_BrokenDB())  # type: ignore[arg-type]
    assert m.status is MetricStatus.CRITICAL
    assert "DB ping 실패" in m.message


def test_collect_api_error_rate_unknown_when_no_calls():
    reg = ApiMetricsRegistry()
    svc = MonitoringService(api_metrics=reg)
    m = svc.collect_api_error_rate()
    assert m.status is MetricStatus.UNKNOWN


def test_collect_api_error_rate_warn_at_threshold():
    reg = ApiMetricsRegistry()
    # 100 calls, 10 errors → 10% — WARN threshold = 5%
    for _ in range(90):
        reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0)
    for _ in range(10):
        reg.record(path="/x", method="GET", status_code=500, latency_ms=1.0)
    svc = MonitoringService(api_metrics=reg)
    m = svc.collect_api_error_rate()
    assert m.status is MetricStatus.WARN


def test_collect_api_error_rate_critical():
    reg = ApiMetricsRegistry()
    for _ in range(7):
        reg.record(path="/x", method="GET", status_code=200, latency_ms=1.0)
    for _ in range(3):
        reg.record(path="/x", method="GET", status_code=500, latency_ms=1.0)
    svc = MonitoringService(api_metrics=reg)
    m = svc.collect_api_error_rate()
    assert m.status is MetricStatus.CRITICAL


def test_collect_order_failure_rate_unknown_when_below_min_orders(client):
    db = _make_db_session(client)
    try:
        # 4 rows total (default min_orders=5) — UNKNOWN
        for _ in range(4):
            _insert_order(db, decision="APPROVED")
        svc = MonitoringService()
        m = svc.collect_order_failure_rate(db)
        assert m.status is MetricStatus.UNKNOWN
    finally:
        db.close()


def test_collect_order_failure_rate_critical(client):
    db = _make_db_session(client)
    try:
        # 10 rows, 8 REJECTED → 80% — CRITICAL (>= 60%)
        for _ in range(8):
            _insert_order(db, decision="REJECTED")
        for _ in range(2):
            _insert_order(db, decision="APPROVED")
        svc = MonitoringService()
        m = svc.collect_order_failure_rate(db)
        assert m.status is MetricStatus.CRITICAL
        assert m.value["failed"] == 8
    finally:
        db.close()


def test_collect_approval_queue_ok_when_empty(client):
    db = _make_db_session(client)
    try:
        svc = MonitoringService()
        m = svc.collect_approval_queue(db)
        assert m.status is MetricStatus.OK
        assert m.value["pending_count"] == 0
    finally:
        db.close()


def test_collect_approval_queue_critical_when_old(client):
    db = _make_db_session(client)
    try:
        _insert_pending(db, minutes_ago=45)
        svc = MonitoringService()
        m = svc.collect_approval_queue(db)
        assert m.status is MetricStatus.CRITICAL
        assert m.value["oldest_age_minutes"] >= 30
    finally:
        db.close()


def test_collect_risk_events_warn_at_threshold(client):
    db = _make_db_session(client)
    try:
        for _ in range(3):
            _insert_emergency(db, minutes_ago=5)
        svc = MonitoringService()
        m = svc.collect_risk_events(db)
        assert m.status is MetricStatus.WARN
    finally:
        db.close()


def test_collect_data_freshness_unknown_without_sample():
    svc = MonitoringService()
    m = svc.collect_data_freshness(
        provider="mock", stale_max_age=60, sample_status=None,
    )
    assert m.status is MetricStatus.UNKNOWN


def test_collect_data_freshness_warn_when_stale():
    svc = MonitoringService()
    m = svc.collect_data_freshness(
        provider="mock", stale_max_age=60,
        sample_status={"is_stale": True, "age_seconds": 999},
    )
    assert m.status is MetricStatus.WARN


def test_collect_notification_warn_when_unconfigured():
    svc = MonitoringService()
    m = svc.collect_notification({
        "enabled": True, "channel_configured": False,
        "channel": "telegram", "min_severity": 20, "min_severity_name": "WARN",
        "dedupe_window_seconds": 60, "always_send_critical": True,
    })
    assert m.status is MetricStatus.WARN
    assert "알림 설정 필요" in m.message


def test_collect_notification_ok_when_disabled():
    svc = MonitoringService()
    m = svc.collect_notification({"enabled": False, "channel_configured": False})
    assert m.status is MetricStatus.OK


# ---------- snapshot ----------


def test_snapshot_returns_all_collectors(client):
    db = _make_db_session(client)
    try:
        svc = MonitoringService()
        snap = svc.snapshot(db)
        names = {m.name for m in snap.metrics}
        assert names == {
            "server", "database", "api_error_rate",
            "order_failure_rate", "approval_queue", "risk_events",
            "data_freshness", "notification",
        }
        assert isinstance(snap, MonitoringSnapshot)
    finally:
        db.close()


def test_snapshot_overall_critical_when_one_collector_critical(client):
    db = _make_db_session(client)
    try:
        for _ in range(10):
            _insert_order(db, decision="REJECTED")
        svc = MonitoringService()
        snap = svc.snapshot(db)
        assert snap.overall is MetricStatus.CRITICAL
        kinds = {a.kind for a in snap.alerts}
        assert "order_failure_rate" in kinds
    finally:
        db.close()


def test_snapshot_does_not_raise_on_empty_db(client):
    db = _make_db_session(client)
    try:
        svc = MonitoringService()
        snap = svc.snapshot(db)
        assert snap.overall in (MetricStatus.OK, MetricStatus.UNKNOWN, MetricStatus.WARN)
    finally:
        db.close()


# ---------- routes ----------


def test_route_health_returns_overall_and_summary(client):
    res = client.get("/api/monitoring/health")
    assert res.status_code == 200
    body = res.json()
    assert "overall" in body
    assert "metrics_summary" in body
    names = {m["name"] for m in body["metrics_summary"]}
    assert "server" in names and "api_error_rate" in names


def test_route_metrics_returns_full_snapshot(client):
    res = client.get("/api/monitoring/metrics")
    assert res.status_code == 200
    body = res.json()
    assert "metrics" in body and len(body["metrics"]) >= 7
    assert "overall" in body
    assert "alerts" in body


def test_route_alerts_returns_list(client):
    res = client.get("/api/monitoring/alerts")
    assert res.status_code == 200
    body = res.json()
    assert "alerts" in body
    assert isinstance(body["alerts"], list)


def test_route_health_response_has_no_secret_patterns(client):
    res = client.get("/api/monitoring/health")
    body_text = res.text.lower()
    for needle in [
        "kis_app_key", "kis_app_secret", "kis_account_no",
        "anthropic_api_key", "openai_api_key",
        "telegram_bot_token", "sk-", "bearer ",
    ]:
        assert needle not in body_text, f"secret pattern leaked: {needle}"


# ---------- notify_alerts helper ----------


def test_notify_alerts_returns_empty_when_no_service():
    out = notify_alerts(None, [
        AlertCandidate(
            severity="WARN", kind="x",
            title="t", message="m", dedupe_key="k",
        ),
    ])
    assert out == []


def test_notify_alerts_calls_service_for_each_candidate():
    class _Recording:
        def __init__(self):
            self.events = []

        def notify(self, event):
            self.events.append(event)
            class _R:
                ok = True
                def to_dict(self):
                    return {"ok": True}
            return _R()

    rec = _Recording()
    out = notify_alerts(rec, [
        AlertCandidate(
            severity="CRITICAL", kind="database",
            title="DB", message="DB 다운", dedupe_key="m:db",
        ),
        AlertCandidate(
            severity="WARN", kind="api_error_rate",
            title="API", message="오류 증가", dedupe_key="m:api",
        ),
    ])
    assert len(rec.events) == 2
    assert all(o["ok"] for o in out)


def test_notify_alerts_swallows_service_errors():
    class _Broken:
        def notify(self, event):
            raise RuntimeError("channel down")

    out = notify_alerts(_Broken(), [
        AlertCandidate(
            severity="WARN", kind="x",
            title="t", message="m", dedupe_key="k",
        ),
    ])
    assert out and out[0]["ok"] is False


# ---------- invariants — static grep guards ----------


_MODULE_PATHS = [
    Path("backend/app/monitoring/__init__.py"),
    Path("backend/app/monitoring/types.py"),
    Path("backend/app/monitoring/api_metrics.py"),
    Path("backend/app/monitoring/middleware.py"),
    Path("backend/app/monitoring/service.py"),
    Path("backend/app/api/routes_monitoring.py"),
]


def _resolve(path: Path) -> Path:
    """tests are run from backend/. project root is one dir up."""
    if path.exists():
        return path
    alt = Path(__file__).resolve().parents[2] / path
    return alt


_FORBIDDEN_IMPORTS = [
    "from app.brokers.kis",
    "from app.brokers.mock_broker",
    "from app.execution.order_router",
    "from app.execution.order_executor",
    "from app.execution.executor",
    "import app.brokers.kis",
    "import app.brokers.mock_broker",
]


def test_monitoring_does_not_import_broker_or_executor():
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN_IMPORTS:
            assert needle not in src, (
                f"{rel} imports forbidden module: {needle!r}"
            )


_FORBIDDEN_CALLS = [
    "broker.place_order(",
    "broker.cancel_order(",
    "route_order(",
    ".execute_order(",
    "OrderExecutor(",
]


def test_monitoring_does_not_call_order_routing_or_broker():
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN_CALLS:
            assert needle not in src, (
                f"{rel} contains forbidden call: {needle!r}"
            )


def test_monitoring_does_not_write_to_db():
    # SELECT만 허용 — INSERT/UPDATE/DELETE/db.add/db.commit/db.flush 호출 0건.
    write_patterns = [
        ".add(", ".add_all(", ".commit(", ".flush(",
        ".delete(", ".update(",
        "INSERT INTO", "UPDATE ", "DELETE FROM",
    ]
    for rel in _MODULE_PATHS:
        path = _resolve(rel)
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        for needle in write_patterns:
            assert needle not in src, (
                f"{rel} contains DB write call: {needle!r}"
            )


def test_routes_monitoring_does_not_change_safety_flags():
    rel = Path("backend/app/api/routes_monitoring.py")
    path = _resolve(rel)
    src = path.read_text(encoding="utf-8")
    forbidden = [
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "ENABLE_FUTURES_LIVE_TRADING",
        "settings.enable_live_trading =",
        "emergency_stop =",
    ]
    for needle in forbidden:
        assert needle not in src, f"routes_monitoring touches safety: {needle}"
