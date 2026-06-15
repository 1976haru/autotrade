#!/usr/bin/env python3
"""Watchdog CLI — backend health 폴링 + 멈춤/다운 감지 + 자동 재시작 + JSONL 로그.

사용:
  python scripts/watchdog.py \
    --backend-cmd "uvicorn app.main:app --host 0.0.0.0 --port 8000" \
    --health-url http://127.0.0.1:8000/api/health/full \
    --check-interval 15

옵션:
  --dry-run         재시작하지 않고 결정만 로그 (테스트/관찰용).
  --max-restarts N  backend 재시작 누적 한도 (초과 시 ERROR 로그 + 중단).
  --log <path>      watchdog JSONL 로그 경로 (default logs/watchdog.jsonl).

안전: 본 스크립트는 broker / 주문 API 를 호출하지 않는다 — backend 프로세스
관리 + health 폴링만. ENABLE_* / KIS 안전 플래그 변경 0건.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.watchdog import WatchdogAction, decide_action  # noqa: E402
from app.system.jsonl_logger import JsonlLogger  # noqa: E402


def _fetch_health(url: str, timeout: float = 5.0) -> tuple[bool, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            ok = resp.status == 200
            body = json.loads(resp.read().decode("utf-8"))
            return ok, body
    except Exception:  # noqa: BLE001
        return False, {}


def _start_backend(cmd: str) -> subprocess.Popen:
    return subprocess.Popen(cmd, shell=True, cwd=str(_BACKEND_DIR))  # noqa: S602


def _start_backend_script(script_path: str) -> subprocess.Popen:
    """2026-06-12: 경로에 공백이 있는 복구 .bat 를 *리스트 형식* 으로 실행 — Windows
    인자 인용을 Python 이 처리해 cmd 의 중첩따옴표 파싱 문제(=run_watchdog 실패)를 회피."""
    return subprocess.Popen(["cmd", "/c", script_path], cwd=str(_BACKEND_DIR))  # noqa: S603


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backend watchdog")
    p.add_argument("--backend-cmd", default="")
    p.add_argument("--backend-script", default="",
                   help="복구용 .bat 경로(공백 경로 안전 — 리스트형 실행). backend-cmd 보다 우선.")
    p.add_argument("--health-url", default="http://127.0.0.1:8000/api/health/full")
    p.add_argument("--check-interval", type=float, default=15.0)
    p.add_argument("--stuck-threshold", type=float, default=300.0)
    p.add_argument("--tick-interval", type=float, default=30.0)
    p.add_argument("--max-restarts", type=int, default=10)
    p.add_argument("--restart-grace", type=float, default=150.0,
                   help="재시작 후 이 초 동안은 추가 재시작 안 함(기동 중 backend 를 "
                        "반복 kill 하는 storm 방지 — 기동시간 > check-interval 일 때 필수).")
    p.add_argument("--max-iterations", type=int, default=0, help="0=무한 (테스트는 유한)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log", default=str(Path("logs") / "watchdog.jsonl"))
    args = p.parse_args(argv)

    log = JsonlLogger(args.log)
    log.info("WATCHDOG_START", health_url=args.health_url, dry_run=args.dry_run,
             backend_cmd_present=bool(args.backend_cmd))

    proc: subprocess.Popen | None = None
    restart_count = 0
    iterations = 0
    last_restart_ts: float | None = None   # storm 방지 — 마지막 재시작 monotonic 시각.

    while True:
        iterations += 1
        ok, body = _fetch_health(args.health_url)
        stab = (body or {}).get("stability", {}) if isinstance(body, dict) else {}
        last_tick = stab.get("last_tick_at")
        last_tick_dt = None
        if last_tick:
            try:
                last_tick_dt = datetime.fromisoformat(str(last_tick))
            except Exception:  # noqa: BLE001
                last_tick_dt = None

        action, reason = decide_action(
            health_ok=ok,
            engine_state=stab.get("engine_state"),
            last_tick_at=last_tick_dt,
            interval_seconds=args.tick_interval,
            stuck_threshold_seconds=args.stuck_threshold,
            now=datetime.now(timezone.utc),
        )

        if action == WatchdogAction.OK:
            log.info("WATCHDOG_CHECK", action=action.value, reason=reason, health_ok=ok)
        elif action == WatchdogAction.WARN_TICK_SLOW:
            log.warn("WATCHDOG_CHECK", action=action.value, reason=reason)
        elif action == WatchdogAction.RESTART_BACKEND:
            _restart_target = args.backend_script or args.backend_cmd
            _in_grace = (last_restart_ts is not None
                         and (time.monotonic() - last_restart_ts) < float(args.restart_grace))
            if _in_grace:
                # ★storm 방지: 직전 재시작이 아직 grace 안 — 기동 중일 수 있으므로 재시작 보류.
                log.warn("WATCHDOG_RESTART_IN_GRACE", reason=reason,
                         since_restart_sec=round(time.monotonic() - last_restart_ts, 1),
                         grace_sec=args.restart_grace)
            elif args.dry_run or not _restart_target:
                log.warn("WATCHDOG_RESTART_BACKEND_SKIPPED", reason=reason,
                         dry_run=args.dry_run, backend_cmd_present=bool(_restart_target))
            elif restart_count >= args.max_restarts:
                log.error("WATCHDOG_MAX_RESTARTS", reason=reason, restart_count=restart_count)
                return 1
            else:
                restart_count += 1
                log.error("BACKEND_RESTART", reason=reason, restart_count=restart_count,
                          last_restart_at=datetime.now(timezone.utc).isoformat())
                try:
                    if proc is not None:
                        proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
                proc = (_start_backend_script(args.backend_script) if args.backend_script
                        else _start_backend(args.backend_cmd))
                last_restart_ts = time.monotonic()   # grace 창 시작(storm 방지).
        elif action == WatchdogAction.RESTART_ENGINE:
            log.error("ENGINE_RESTART_RECOMMENDED", reason=reason)

        if args.max_iterations and iterations >= args.max_iterations:
            log.info("WATCHDOG_STOP", iterations=iterations, restart_count=restart_count)
            return 0
        time.sleep(max(1.0, args.check_interval))


if __name__ == "__main__":
    raise SystemExit(main())
