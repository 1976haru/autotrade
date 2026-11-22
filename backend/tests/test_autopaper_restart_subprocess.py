"""실제 프로세스 재시작 회귀 — 승인 세션 자동 복구의 fail-closed 정책.

각 단계는 *새 Python 프로세스* 에서 실제 FastAPI lifespan(시작 시 복구 판단, 종료 시
재시작 허가 기록)을 돈다. 상태는 tmp 의 DB / 승인 파일 / APPDATA 에만 남는다.
실제 KIS 호출·주문 0건 (tests/_restart_harness.py 참고).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


HARNESS = Path(__file__).with_name("_restart_harness.py")


def run(tmp_path: Path, *steps: str, db_down: bool = False) -> dict:
    env = dict(os.environ)
    env["HARNESS_DIR"] = str(tmp_path)
    # 운영자 %APPDATA% 미접촉 — APPDATA 를 tmp 로, 사용자 site-packages 는 유지.
    real_appdata = os.environ.get("APPDATA")
    if real_appdata:
        env.setdefault("PYTHONUSERBASE", str(Path(real_appdata) / "Python"))
    env["APPDATA"] = str(tmp_path / "appdata")
    env["LOCALAPPDATA"] = str(tmp_path / "localappdata")
    env.pop("AUTOTRADE_DESKTOP_AUTH_TOKEN", None)
    if db_down:
        env["HARNESS_DB_DOWN"] = "1"
    proc = subprocess.run([sys.executable, str(HARNESS), *steps], env=env,
                          capture_output=True, text=True, timeout=240,
                          cwd=str(HARNESS.parents[1]))
    assert proc.returncode == 0, proc.stderr[-4000:]
    last = [ln for ln in proc.stdout.strip().splitlines() if ln.startswith("{")][-1]
    return json.loads(last)


def _db_set_status(tmp_path: Path, status: str, **fields) -> None:
    con = sqlite3.connect(tmp_path / "harness.db")
    try:
        (rid, record), = con.execute(
            "SELECT id, record FROM paper_session_approval ORDER BY id DESC LIMIT 1").fetchall()
        rec = json.loads(record)
        rec.update(status=status, **fields)
        con.execute("UPDATE paper_session_approval SET status=?, record=? WHERE id=?",
                    (status, json.dumps(rec), rid))
        con.commit()
    finally:
        con.close()




def test_clean_shutdown_restores_once_then_crash_requires_reapproval(tmp_path):
    p1 = run(tmp_path, "approve", "serve")
    assert p1["start_state"] in {"RUNNING", "WAITING_MARKET"}
    assert p1["permit_after_shutdown"] is True             # 정상 종료 → 허가 기록

    p2 = run(tmp_path, "probe_crash")                      # 재시작: 복구됨, 그 뒤 크래시
    assert p2["loop_state"] in {"RUNNING", "WAITING_MARKET"}
    assert p2["trading_authorized"] is True
    assert p2["buy"] == [None, "REACHED_BROKER"]            # BUY 게이트 통과(가짜 broker 에서 멈춤)
    assert p2["permit_exists"] is False                     # 허가는 일회용으로 소모

    p3 = run(tmp_path, "probe")                            # 크래시 후 재시작
    assert p3["loop_state"] == "PAUSED"                     # 자동 복구 안 됨
    assert p3["approval"]["status"] == "EXIT_ONLY"
    assert p3["approval"]["buy_block_reason"] == "RESTART_REAPPROVAL_REQUIRED"
    assert p3["trading_authorized"] is False and p3["exit_authorized"] is True
    assert p3["buy"] == [False, "RESTART_REAPPROVAL_REQUIRED"]


def test_emergency_persist_total_failure_then_real_restart_blocks(tmp_path):
    p1 = run(tmp_path, "approve", "serve_emergency_fail")
    assert p1["emergency_state"] == "EMERGENCY_STOP"
    assert p1["emergency_persisted"] is False               # 파일·DB 모두 저장 실패
    assert p1["permit_after_shutdown"] is False             # 정상 종료여도 허가 없음

    p2 = run(tmp_path, "probe")
    assert p2["loop_state"] == "PAUSED"                     # 이전 승인 자동 복구 안 됨
    assert p2["trading_authorized"] is False
    assert p2["buy"][0] is False
    assert p2["buy"][1] in {"RESTART_REAPPROVAL_REQUIRED", "EMERGENCY_STOP_SESSION_BLOCKED"}
    assert p2["exit_authorized"] is True                    # EOD 청산 경로는 유지

    p3 = run(tmp_path, "probe")                            # 다음 재시작에도 계속 차단
    assert p3["loop_state"] == "PAUSED" and p3["trading_authorized"] is False
    assert p3["buy"][0] is False


def test_db_blocked_but_db_unreadable_on_restart_blocks(tmp_path):
    p1 = run(tmp_path, "approve", "serve")
    assert p1["permit_after_shutdown"] is True
    # DB 에만 차단 기록 (파일은 오래된 SCHEDULED 그대로) + 재시작 때 DB 조회 실패.
    _db_set_status(tmp_path, "EXIT_ONLY", buy_blocked=True, emergency_blocked=True,
                   buy_block_reason="EMERGENCY_STOP_SESSION_BLOCKED")
    p2 = run(tmp_path, "probe", db_down=True)
    assert p2["loop_state"] == "PAUSED"                     # 허가가 있어도 복구 거부
    assert p2["trading_authorized"] is False
    assert p2["buy"][0] is False
