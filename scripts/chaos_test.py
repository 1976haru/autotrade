#!/usr/bin/env python3
"""Chaos Test — 장중 무중단 운용 가능성 검증 (예외 복구 / watchdog / secret).

시나리오: 네트워크 단절 / KIS timeout / DB lock / backend 강제종료 후 부활 /
가격 stale / token expired 를 합성 주입하고 복구가 동작하는지 측정.

  python scripts/chaos_test.py --duration 2m --fast     # 빠른 검증
  python scripts/chaos_test.py --duration 4h            # 4시간 무중단

결과: logs/chaos_test_result.json. **실 KIS 주문 / broker 호출 0건** — 합성
함수만 재시도(retry_with_backoff)하고, backend 재시작은 watchdog 결정 로직으로
시뮬레이션. ENABLE_* / KIS 안전 플래그 변경 0건.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# STABILITY_4H_READY 인정 임계 (초) — 14400(4h)에 근접.
_FOUR_HOURS_SEC = 14400.0
_FOUR_HOUR_NEAR_RATIO = 0.98

_BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(_BACKEND_DIR))

from app.system.recovery import (  # noqa: E402
    RecoveryReason,
    is_price_stale,
    retry_with_backoff,
)
from app.system.watchdog import WatchdogAction, decide_action  # noqa: E402
from app.system.jsonl_logger import JsonlLogger  # noqa: E402

_SCENARIOS = ("NET_DOWN", "KIS_TIMEOUT", "DB_LOCK", "BACKEND_KILL",
              "PRICE_STALE", "TOKEN_EXPIRED")

# 결과 파일에서 secret 누출을 2차 스캔할 패턴.
_SECRET_SCAN = (
    re.compile(r"\bsk-[A-Za-z0-9]{20,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\b\d{8}-\d{2}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
)


def _parse_duration(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


async def _flaky(fail_times: int, exc: Exception):
    """fail_times 회 실패 후 성공하는 합성 함수 factory."""
    state = {"n": 0}

    async def fn():
        if state["n"] < fail_times:
            state["n"] += 1
            raise exc
        return {"ok": True}
    return fn


async def _instant(_):  # 즉시 backoff — 실제로 안 잠
    return None


async def _one_tick(i: int, c: dict, *, backoff_delays, backoff_sleep,
                    log: JsonlLogger) -> None:
    """단일 chaos tick 처리 — counters dict c 를 갱신. broker 호출 0건."""
    scenario = _SCENARIOS[i % len(_SCENARIOS)]

    if scenario == "PRICE_STALE":
        stale = is_price_stale(None, now=datetime.now(timezone.utc), max_age_seconds=60)
        log.warn("CHAOS_TICK", tick_id=i, scenario=scenario,
                 reason_code=RecoveryReason.PRICE_STALE.value, recovered=True,
                 price_stale=stale, action="SKIP")
        c["warnings"] += 1
        c["tick_completed"] += 1   # 의도된 skip — 정상 처리 (fatal 아님)
        return

    if scenario == "BACKEND_KILL":
        action, reason = decide_action(health_ok=False)
        if action == WatchdogAction.RESTART_BACKEND:
            c["backend_restart_count"] += 1
            c["watchdog_restart_count"] += 1
            log.error("CHAOS_BACKEND_KILL", tick_id=i, scenario=scenario,
                      reason_code=reason, recovered=True, action=action.value)
            c["errors"] += 1
        action2, _ = decide_action(health_ok=True, last_tick_at=None)
        assert action2 == WatchdogAction.OK   # 재시작 후 복구
        c["tick_completed"] += 1
        return

    exc_map = {
        "NET_DOWN": ConnectionError("network unreachable"),
        "KIS_TIMEOUT": TimeoutError("KIS API EGW00201 timed out"),
        "DB_LOCK": RuntimeError("database is locked (OperationalError)"),
        "TOKEN_EXPIRED": RuntimeError("KIS EGW00133 token expired / unauthorized"),
    }
    fn = await _flaky(fail_times=2, exc=exc_map[scenario])
    c["recovery_attempts"] += 1
    result, ev = await retry_with_backoff(
        fn, retries=3, delays=backoff_delays, sleep=backoff_sleep,
        tick_id=str(i), symbol="005930",
    )
    log.warn("CHAOS_TICK", **ev.to_dict(), scenario=scenario)
    c["warnings"] += 1
    if result is not None and ev.recovered:
        c["recovered"] += 1
        c["tick_completed"] += 1
    else:
        c["tick_failed"] += 1
        c["errors"] += 1


async def run_chaos(*, duration_sec: float, fast: bool, real_time: bool = False,
                    tick_sleep: float = 1.0, log: JsonlLogger) -> dict:
    """accelerated(기본) 또는 real_time(wall-clock soak) chaos 실행.

    - accelerated: tick 수 = fast?240 : duration/30. backoff 즉시(또는 1/2/4).
    - real_time: duration 동안 *실제 대기*(tick_sleep)하며 반복 — wall-clock soak.
    """
    mode = "real_time" if real_time else "accelerated"
    # real_time/fast 는 backoff 즉시 (wall-clock 은 tick_sleep 이 지배).
    backoff_sleep = _instant if (fast or real_time) else asyncio.sleep
    backoff_delays = (0.0, 0.0, 0.0) if (fast or real_time) else (1.0, 2.0, 4.0)

    c = {"tick_completed": 0, "tick_failed": 0, "recovered": 0,
         "recovery_attempts": 0, "backend_restart_count": 0,
         "watchdog_restart_count": 0, "errors": 0, "warnings": 0}

    start = time.monotonic()
    if real_time:
        i = 0
        while (time.monotonic() - start) < duration_sec:
            await _one_tick(i, c, backoff_delays=backoff_delays,
                            backoff_sleep=backoff_sleep, log=log)
            i += 1
            remaining = duration_sec - (time.monotonic() - start)
            if remaining <= 0:
                break
            await asyncio.sleep(min(max(0.01, tick_sleep), remaining))
    else:
        n_ticks = 240 if fast else max(1, int(duration_sec // 30))
        for i in range(n_ticks):
            await _one_tick(i, c, backoff_delays=backoff_delays,
                            backoff_sleep=backoff_sleep, log=log)
    actual_wall = round(time.monotonic() - start, 2)

    # secret sanitize 검증 — 일부러 *가짜* secret 레코드를 기록해 마스킹 확인.
    # (실제 secret 아님 — sanitize 가 ***MASKED*** 로 가려야 PASS. 소스 스캐너는
    #  의도된 fixture 라 ignore 마커로 제외.)
    log.info(
        "CHAOS_SECRET_PROBE",
        kis_app_secret="sk-secretSHOULDBEMASKED1234567890",  # security-scan: ignore
        account_no="12345678-90",  # security-scan: ignore
        note="probe — 반드시 마스킹돼야 함",
    )

    secret_leak_count = 0
    try:
        text = log.path.read_text(encoding="utf-8")
        for pat in _SECRET_SCAN:
            secret_leak_count += len(pat.findall(text))
    except Exception:  # noqa: BLE001
        pass

    rate = (round(100.0 * c["recovered"] / c["recovery_attempts"], 1)
            if c["recovery_attempts"] else 100.0)
    passed = (c["tick_failed"] == 0 and secret_leak_count == 0 and rate == 100.0)
    # STABILITY_4H_READY 는 *실제* wall-clock 이 4h 근접일 때만.
    stability_4h_ready = bool(
        real_time and passed and c["tick_completed"] >= 200
        and actual_wall >= _FOUR_HOURS_SEC * _FOUR_HOUR_NEAR_RATIO
    )

    return {
        "duration": duration_sec,
        "mode": mode,
        "fast": fast,
        "real_time": real_time,
        "tick_sleep": tick_sleep if real_time else None,
        "actual_wall_clock_seconds": actual_wall,
        "tick_completed": c["tick_completed"],
        "tick_failed": c["tick_failed"],
        "recovery_success_rate": rate,
        "backend_restart_count": c["backend_restart_count"],
        "watchdog_restart_count": c["watchdog_restart_count"],
        "secret_leak_count": secret_leak_count,
        "errors": c["errors"],
        "warnings": c["warnings"],
        "pass_fail": "PASS" if passed else "FAIL",
        "stability_4h_ready": stability_4h_ready,
        "is_live_authorization": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chaos test for runtime stability")
    p.add_argument("--duration", default="2m")
    p.add_argument("--fast", action="store_true",
                   help="accelerated — 즉시 실행(기본 검증용)")
    p.add_argument("--real-time", "--wall-clock", dest="real_time",
                   action="store_true",
                   help="실제 wall-clock soak — duration 동안 tick_sleep 간격으로 실제 대기")
    p.add_argument("--tick-sleep", type=float, default=1.0,
                   help="real_time tick 간 실제 대기(초). default 1.0")
    p.add_argument("--log", default=str(Path("logs") / "chaos_test.jsonl"))
    p.add_argument("--out", default=str(Path("logs") / "chaos_test_result.json"))
    args = p.parse_args(argv)

    dur = _parse_duration(args.duration)
    log = JsonlLogger(args.log)
    log.info("CHAOS_START", duration_sec=dur, fast=args.fast,
             real_time=args.real_time, tick_sleep=args.tick_sleep)
    result = asyncio.run(run_chaos(duration_sec=dur, fast=args.fast,
                                   real_time=args.real_time, tick_sleep=args.tick_sleep,
                                   log=log))
    log.info("CHAOS_DONE", **{k: result[k] for k in (
        "pass_fail", "mode", "actual_wall_clock_seconds", "tick_completed",
        "recovery_success_rate", "secret_leak_count", "stability_4h_ready")})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass_fail"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
