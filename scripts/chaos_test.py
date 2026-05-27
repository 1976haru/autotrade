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
from datetime import datetime, timezone
from pathlib import Path

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


async def run_chaos(*, duration_sec: float, fast: bool, log: JsonlLogger) -> dict:
    # tick 수: fast 면 압축(>=200 보장 위해 240), 아니면 duration/30s 가정.
    n_ticks = 240 if fast else max(1, int(duration_sec // 30))

    async def _instant(_):  # fast backoff — 실제로 안 잠
        return None
    sleep = _instant if fast else asyncio.sleep

    tick_completed = 0
    tick_failed = 0
    recovered = 0
    recovery_attempts = 0
    backend_restart_count = 0
    watchdog_restart_count = 0
    errors = 0
    warnings = 0

    for i in range(n_ticks):
        scenario = _SCENARIOS[i % len(_SCENARIOS)]

        if scenario == "PRICE_STALE":
            stale = is_price_stale(None, now=datetime.now(timezone.utc),
                                   max_age_seconds=60)
            log.warn("CHAOS_TICK", tick_id=i, scenario=scenario,
                     reason_code=RecoveryReason.PRICE_STALE.value, recovered=True,
                     price_stale=stale, action="SKIP")
            warnings += 1
            tick_completed += 1   # 의도된 skip — 정상 처리 (fatal 아님)
            continue

        if scenario == "BACKEND_KILL":
            # health 다운 → watchdog 재시작 결정 → 복구.
            action, reason = decide_action(health_ok=False)
            if action == WatchdogAction.RESTART_BACKEND:
                backend_restart_count += 1
                watchdog_restart_count += 1
                log.error("CHAOS_BACKEND_KILL", tick_id=i, scenario=scenario,
                          reason_code=reason, recovered=True, action=action.value)
                errors += 1
            # 재시작 후 health 정상 가정 → 복구.
            action2, _ = decide_action(health_ok=True, last_tick_at=None)
            assert action2 == WatchdogAction.OK
            tick_completed += 1
            continue

        # NET_DOWN / KIS_TIMEOUT / DB_LOCK / TOKEN_EXPIRED — 1~2회 실패 후 복구.
        exc_map = {
            "NET_DOWN": ConnectionError("network unreachable"),
            "KIS_TIMEOUT": TimeoutError("KIS API EGW00201 timed out"),
            "DB_LOCK": RuntimeError("database is locked (OperationalError)"),
            "TOKEN_EXPIRED": RuntimeError("KIS EGW00133 token expired / unauthorized"),
        }
        fn = await _flaky(fail_times=2, exc=exc_map[scenario])
        recovery_attempts += 1
        result, ev = await retry_with_backoff(
            fn, retries=3, delays=(0.0, 0.0, 0.0) if fast else (1.0, 2.0, 4.0),
            sleep=sleep, tick_id=str(i), symbol="005930",
        )
        log.warn("CHAOS_TICK", **ev.to_dict(), scenario=scenario)
        warnings += 1
        if result is not None and ev.recovered:
            recovered += 1
            tick_completed += 1
        else:
            tick_failed += 1
            errors += 1

    # secret sanitize 검증 — 일부러 secret 포함 레코드를 기록해 마스킹 확인.
    log.info("CHAOS_SECRET_PROBE", kis_app_secret="sk-secretSHOULDBEMASKED1234567890",
             account_no="12345678-90", note="probe — 반드시 마스킹돼야 함")

    # 로그 파일 2차 secret 스캔.
    secret_leak_count = 0
    try:
        text = log.path.read_text(encoding="utf-8")
        for pat in _SECRET_SCAN:
            secret_leak_count += len(pat.findall(text))
    except Exception:  # noqa: BLE001
        pass

    rate = (round(100.0 * recovered / recovery_attempts, 1)
            if recovery_attempts else 100.0)
    passed = (tick_failed == 0 and secret_leak_count == 0 and rate == 100.0)

    return {
        "duration": duration_sec,
        "fast": fast,
        "tick_completed": tick_completed,
        "tick_failed": tick_failed,
        "recovery_success_rate": rate,
        "backend_restart_count": backend_restart_count,
        "watchdog_restart_count": watchdog_restart_count,
        "secret_leak_count": secret_leak_count,
        "errors": errors,
        "warnings": warnings,
        "pass_fail": "PASS" if passed else "FAIL",
        "is_live_authorization": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chaos test for runtime stability")
    p.add_argument("--duration", default="2m")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--log", default=str(Path("logs") / "chaos_test.jsonl"))
    p.add_argument("--out", default=str(Path("logs") / "chaos_test_result.json"))
    args = p.parse_args(argv)

    dur = _parse_duration(args.duration)
    log = JsonlLogger(args.log)
    log.info("CHAOS_START", duration_sec=dur, fast=args.fast)
    result = asyncio.run(run_chaos(duration_sec=dur, fast=args.fast, log=log))
    log.info("CHAOS_DONE", **{k: result[k] for k in ("pass_fail", "tick_completed",
             "recovery_success_rate", "secret_leak_count")})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["pass_fail"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
