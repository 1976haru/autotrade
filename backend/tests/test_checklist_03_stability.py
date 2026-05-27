"""CHECKLIST-03 시스템 안정화 — 예외복구 / watchdog / JSONL 로그 / secret sanitize.

순수 단위 + fast chaos subprocess. broker / KIS 실 API / 실주문 0건.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.system.recovery import (
    RecoveryEvent,
    RecoveryReason,
    classify_exception,
    is_price_stale,
    retry_with_backoff,
)
from app.system.watchdog import WatchdogAction, decide_action, tick_is_stale
from app.system.jsonl_logger import JsonlLogger, sanitize_for_log


async def _nosleep(_):  # 테스트용 즉시 backoff
    return None


# ═══════════ 1) 예외 자동복구 ═══════════


def test_classify_exception_reason_codes():
    assert classify_exception(TimeoutError("timed out")) == RecoveryReason.KIS_TIMEOUT
    assert classify_exception(ConnectionError("network unreachable")) == RecoveryReason.NET_DOWN
    assert classify_exception(RuntimeError("database is locked")) == RecoveryReason.DB_LOCK
    assert classify_exception(RuntimeError("EGW00133 token expired")) == RecoveryReason.TOKEN_EXPIRED


def test_retry_recovers_after_transient_failures():
    state = {"n": 0}

    async def fn():
        if state["n"] < 2:
            state["n"] += 1
            raise TimeoutError("KIS timeout")
        return {"price": 70000}

    result, ev = asyncio.run(retry_with_backoff(
        fn, retries=3, delays=(0, 0, 0), sleep=_nosleep, tick_id="t1", symbol="005930"))
    assert result == {"price": 70000}
    assert ev.recovered is True
    assert ev.retry_count == 2
    assert ev.reason_code == RecoveryReason.RECOVERED.value
    assert ev.is_live_authorization is False


def test_retry_exhausts_and_skips():
    async def fn():
        raise ConnectionError("network down")

    result, ev = asyncio.run(retry_with_backoff(
        fn, retries=3, delays=(0, 0, 0), sleep=_nosleep, tick_id="t2"))
    assert result is None
    assert ev.recovered is False
    assert ev.reason_code == RecoveryReason.NET_DOWN.value
    assert ev.error_type == "ConnectionError"
    assert ev.retry_count == 3


def test_recovery_event_fields_and_invariant():
    ev = RecoveryEvent(reason_code="KIS_TIMEOUT", symbol="005930", tick_id="t",
                       decision_id="d", retry_count=1, recovered=True)
    d = ev.to_dict()
    for k in ("tick_id", "decision_id", "symbol", "reason_code", "error_type",
              "retry_count", "recovered", "created_at", "is_live_authorization"):
        assert k in d
    assert d["is_live_authorization"] is False
    with pytest.raises(ValueError):
        RecoveryEvent(reason_code="X", is_live_authorization=True)


def test_price_stale():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(seconds=10)
    old = now - timedelta(seconds=120)
    assert is_price_stale(fresh, now=now, max_age_seconds=60) is False
    assert is_price_stale(old, now=now, max_age_seconds=60) is True
    assert is_price_stale(None, now=now, max_age_seconds=60) is True


# ═══════════ 2) Watchdog 결정 로직 ═══════════


def test_watchdog_backend_down_restarts():
    a, r = decide_action(health_ok=False)
    assert a == WatchdogAction.RESTART_BACKEND
    assert r == "BACKEND_DOWN"


def test_watchdog_engine_stuck_restart():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    stuck = now - timedelta(seconds=400)
    a, r = decide_action(health_ok=True, engine_state="STUCK", stuck_since=stuck, now=now,
                         stuck_threshold_seconds=300)
    assert a == WatchdogAction.RESTART_ENGINE
    # 5분 미만이면 재시작 안 함.
    a2, _ = decide_action(health_ok=True, engine_state="STUCK",
                          stuck_since=now - timedelta(seconds=60), now=now)
    assert a2 != WatchdogAction.RESTART_ENGINE


def test_watchdog_tick_slow_warn():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    assert tick_is_stale(now - timedelta(seconds=200), interval_seconds=30, now=now) is True
    assert tick_is_stale(now - timedelta(seconds=20), interval_seconds=30, now=now) is False
    a, r = decide_action(health_ok=True, last_tick_at=now - timedelta(seconds=200),
                         interval_seconds=30, now=now)
    assert a == WatchdogAction.WARN_TICK_SLOW


def test_watchdog_ok_when_healthy():
    now = datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    a, _ = decide_action(health_ok=True, engine_state="RUNNING",
                         last_tick_at=now - timedelta(seconds=10),
                         interval_seconds=30, now=now)
    assert a == WatchdogAction.OK


# ═══════════ 3) JSONL 로그 + Secret sanitize ═══════════


def test_sanitize_masks_secret_keys():
    out = sanitize_for_log({
        "kis_app_secret": "REALSECRET", "app_key": "KEY", "account_no": "12345678-90",
        "token": "abc", "kis_credentials_present": True, "symbol": "005930",
    })
    assert out["kis_app_secret"] == "***MASKED***"
    assert out["app_key"] == "***MASKED***"
    assert out["account_no"] == "***MASKED***"
    assert out["token"] == "***MASKED***"
    assert out["kis_credentials_present"] is True   # *_present 허용
    assert out["symbol"] == "005930"


def test_sanitize_redacts_secret_value_patterns():
    out = sanitize_for_log({"msg": "leaked sk-abcdefghij1234567890ABCDEFGH and 12345678-90"})
    assert "sk-abcdefghij" not in out["msg"]
    assert "[REDACTED]" in out["msg"]


def test_jsonl_logger_writes_no_secret(tmp_path):
    log = JsonlLogger(tmp_path / "x.jsonl")
    log.info("TICK", symbol="005930", kis_app_secret="sk-shouldNotAppear1234567890XX",
             account_no="98765432-10")
    text = (tmp_path / "x.jsonl").read_text(encoding="utf-8")
    assert "sk-shouldNotAppear" not in text
    assert "98765432-10" not in text
    assert "***MASKED***" in text
    rec = json.loads(text.strip())
    assert rec["is_live_authorization"] is False
    assert rec["event"] == "TICK"


# ═══════════ 4) Fast chaos test (subprocess) ═══════════


def test_fast_chaos_passes(tmp_path):
    root = Path(__file__).resolve().parents[2]
    out = tmp_path / "chaos_result.json"
    log = tmp_path / "chaos.jsonl"
    r = subprocess.run(
        [sys.executable, str(root / "scripts" / "chaos_test.py"),
         "--duration", "2m", "--fast", "--out", str(out), "--log", str(log)],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["pass_fail"] == "PASS"
    assert result["tick_completed"] >= 200
    assert result["recovery_success_rate"] == 100.0
    assert result["secret_leak_count"] == 0
    assert result["tick_failed"] == 0
    assert result["backend_restart_count"] >= 1   # backend kill 시나리오 부활
    # 로그 파일에 secret 원문 0건.
    txt = log.read_text(encoding="utf-8")
    for pat in (r"sk-[A-Za-z0-9]{20,}", r"\b\d{8}-\d{2}\b"):
        assert not re.search(pat, txt), f"secret leak: {pat}"
