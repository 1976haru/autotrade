"""#63 / 8-01 — EXE Preflight Smoke Test 테스트.

검증:
- preflight 평가 로직: 안전 위반(live/ai/futures/kis_is_paper=false) → FAIL,
  자격 미구성 → WARN, MARKET_CLOSED → WARN, 정상 → PASS
- `GET /api/system/preflight` read-only + 안전 invariant + secret 0건 + 주문 0건
- CLI script: read-only GET 만 호출, unreachable → exit 2, FAIL → exit 1,
  secret/account 탐지 → FAIL, JSON output, 주문 endpoint 호출 0건
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.system import preflight as pf

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "exe_smoke_test.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("exe_smoke_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _safe_inputs(**over):
    base = dict(
        db_ready=True,
        enable_live_trading=False,
        enable_ai_execution=False,
        enable_futures_live_trading=False,
        kis_is_paper=True,
        default_mode="PAPER",
        is_live_authorization=False,
        credentials_present=True,
        missing_credentials=[],
        kis_paper_ready=True,
        kis_paper_blocked_reasons=[],
        kis_paper_auto_ready=True,
        broker_order_type="KIS_PAPER",
        loop_state="PAUSED",
        loop_last_error=None,
        agent_council_roles=6,
        decision_episode_count=3,
        no_trade_capable=True,
        build_info={"version": "1.0.0", "commit": "abc1234"},
        update_status="UNKNOWN",
        sidecar_status="RUNNING",
    )
    base.update(over)
    return base


def _status(checks, name):
    for c in checks:
        if c["name"] == name:
            return c["status"]
    return None


# ── preflight 평가 로직 ──────────────────────────────────────────────────────


def test_all_safe_inputs_pass():
    checks = pf.build_checks(_safe_inputs())
    s = pf.summarize(checks)
    assert s["fail_count"] == 0
    assert _status(checks, "safety_flags") == "PASS"
    assert _status(checks, "db_status") == "PASS"


def test_live_trading_true_is_fail():
    checks = pf.build_checks(_safe_inputs(enable_live_trading=True))
    assert _status(checks, "safety_flags") == "FAIL"
    assert pf.summarize(checks)["status"] == "FAIL"


def test_ai_execution_true_is_fail():
    checks = pf.build_checks(_safe_inputs(enable_ai_execution=True))
    assert _status(checks, "safety_flags") == "FAIL"


def test_kis_is_paper_false_is_fail():
    checks = pf.build_checks(_safe_inputs(kis_is_paper=False))
    assert _status(checks, "safety_flags") == "FAIL"


def test_default_mode_live_is_fail():
    checks = pf.build_checks(_safe_inputs(default_mode="LIVE_MANUAL_APPROVAL"))
    assert _status(checks, "default_mode") == "FAIL"


def test_db_not_ready_is_fail():
    checks = pf.build_checks(_safe_inputs(db_ready=False))
    assert _status(checks, "db_status") == "FAIL"


def test_is_live_authorization_true_is_fail():
    checks = pf.build_checks(_safe_inputs(is_live_authorization=True))
    assert _status(checks, "is_live_authorization") == "FAIL"


def test_credentials_missing_is_warn_not_fail():
    checks = pf.build_checks(_safe_inputs(
        credentials_present=False, missing_credentials=["KIS_APP_KEY"],
    ))
    assert _status(checks, "kis_credentials") == "WARN"


def test_readiness_blocked_by_live_flag_is_fail():
    checks = pf.build_checks(_safe_inputs(
        kis_paper_ready=False,
        kis_paper_blocked_reasons=["ENABLE_LIVE_TRADING_TRUE"],
    ))
    assert _status(checks, "kis_paper_readiness") == "FAIL"


def test_market_closed_loop_is_warn_not_fail():
    checks = pf.build_checks(_safe_inputs(loop_state="MARKET_CLOSED"))
    assert _status(checks, "auto_loop") == "WARN"
    # 장 닫힘은 치명 FAIL 이 아니어야 한다.
    assert pf.summarize(checks)["fail_count"] == 0


def test_non_paper_broker_is_fail():
    checks = pf.build_checks(_safe_inputs(broker_order_type="KIS_LIVE"))
    assert _status(checks, "broker_order_type") == "FAIL"


def test_mock_broker_is_pass():
    checks = pf.build_checks(_safe_inputs(broker_order_type="MOCK"))
    assert _status(checks, "broker_order_type") == "PASS"


def test_no_recent_episode_is_warn():
    checks = pf.build_checks(_safe_inputs(decision_episode_count=0))
    assert _status(checks, "decision_episode_api") == "WARN"


def test_build_info_unknown_is_warn():
    checks = pf.build_checks(_safe_inputs(build_info={"version": "unknown", "commit": "unknown"}))
    assert _status(checks, "build_info") == "WARN"


# ── /api/system/preflight endpoint ───────────────────────────────────────────


def test_preflight_endpoint_read_only_and_safe(client):
    r = client.get("/api/system/preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["is_live_authorization"] is False
    assert body["contains_secret"] is False
    assert "summary" in body and "checks" in body
    assert body["summary"]["status"] in ("PASS", "WARN", "FAIL")


def test_preflight_endpoint_no_broker_order(client):
    client.get("/api/system/preflight")
    assert len(client.test_broker.orders) == 0


def test_preflight_endpoint_no_secret_in_response(client):
    text = json.dumps(client.get("/api/system/preflight").json(), ensure_ascii=False)
    for forbidden in ("kis_app_secret", "access_token", "Bearer ", "sk-ant-"):
        assert forbidden not in text


# ── CLI script ───────────────────────────────────────────────────────────────


def test_script_scan_secrets_detects_patterns():
    mod = _load_script()
    assert "openai_key" in mod.scan_secrets({"x": "token sk-ABCDEFGHIJKLMNOPQRSTUV"})
    assert "kis_account_no" in mod.scan_secrets({"acct": "12345678-01"})
    assert mod.scan_secrets({"x": "all clean here"}) == []


def test_script_build_report_unreachable_exit_2():
    mod = _load_script()
    rep = mod.build_report(None, reachable=False, error="connection refused")
    assert rep["exit_code"] == mod.EXIT_UNREACHABLE
    assert rep["summary"]["status"] == "FAIL"


def test_script_build_report_fail_exit_1():
    mod = _load_script()
    preflight = {
        "checks": [
            {"name": "safety_flags", "status": "FAIL", "message": "live on"},
            {"name": "db_status", "status": "PASS", "message": "ok"},
        ],
        "generated_at": "2026-05-24T00:00:00+00:00",
    }
    rep = mod.build_report(preflight, reachable=True, error=None)
    assert rep["exit_code"] == mod.EXIT_FAIL
    assert rep["summary"]["status"] == "FAIL"


def test_script_build_report_pass_exit_0():
    mod = _load_script()
    preflight = {
        "checks": [{"name": "db_status", "status": "PASS", "message": "ok"}],
        "generated_at": "2026-05-24T00:00:00+00:00",
    }
    rep = mod.build_report(preflight, reachable=True, error=None)
    assert rep["exit_code"] == mod.EXIT_OK
    # backend_api_reachable + secret_scan PASS 추가됨.
    assert any(c["name"] == "backend_api_reachable" for c in rep["checks"])
    assert any(c["name"] == "secret_scan" for c in rep["checks"])


def test_script_build_report_secret_in_response_is_fail():
    mod = _load_script()
    preflight = {
        "checks": [{"name": "db_status", "status": "PASS", "message": "ok"}],
        # 비정상적으로 secret 이 섞인 응답 — client 측 스캔이 FAIL 로 잡아야.
        "leaked": "Bearer abcdefghijklmnopqrstuvwxyz123456",
    }
    rep = mod.build_report(preflight, reachable=True, error=None)
    assert rep["contains_secret"] is True
    assert rep["exit_code"] == mod.EXIT_FAIL


def test_script_run_only_calls_read_only_paths():
    mod = _load_script()
    called = []

    def fake_fetch(base, path, *, timeout=8.0):
        called.append(path)
        return 200, {"checks": [{"name": "db_status", "status": "PASS", "message": "ok"}],
                     "generated_at": "t"}, None

    mod.run("http://x", fetch=fake_fetch)
    # 오직 read-only preflight 만 (정상 시 health 도 호출 안 함).
    assert called == ["/api/system/preflight"]
    for p in called:
        assert p.startswith("/")
        assert "order" not in p.lower()


def test_script_run_unreachable_returns_exit_2():
    mod = _load_script()

    def dead_fetch(base, path, *, timeout=8.0):
        return 0, None, "connection refused"

    rep = mod.run("http://127.0.0.1:1", fetch=dead_fetch)
    assert rep["exit_code"] == mod.EXIT_UNREACHABLE


def test_script_main_unreachable_real_socket_exit_2():
    mod = _load_script()
    # 닫힌 포트 → 실제 urllib 연결 거부 → exit 2.
    rc = mod.main(["--base-url", "http://127.0.0.1:1", "--timeout", "2"])
    assert rc == mod.EXIT_UNREACHABLE


def test_script_main_writes_json(tmp_path):
    mod = _load_script()
    out = tmp_path / "preflight.json"

    # run 을 monkeypatch 하지 않고, fetch 주입이 안 되는 main 경로는 dead port
    # 로 unreachable JSON 을 생성한다 (주문 endpoint 호출 0건).
    rc = mod.main(["--base-url", "http://127.0.0.1:1", "--timeout", "2",
                   "--json", str(out)])
    assert rc == mod.EXIT_UNREACHABLE
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in data and "checks" in data
