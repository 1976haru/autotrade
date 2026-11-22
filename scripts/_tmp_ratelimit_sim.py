#!/usr/bin/env python3
"""TEMP — PART1: 50종목 스캔 rate-limit 시뮬 (mock, 실주문 0, KIS 호출 0).

실제 `SlidingWindowRateLimiter`(config 값 그대로)를 써서 50종목 1 cycle 의
quote 호출 타이밍을 시뮬. EGW00201 회피 기준 = 초당 호출 ≤ KIS 한도(2/s).

비교: 기존 default(5/1.0=5 req/s) vs fix 76fcfd9(2/1.1≈1.82 req/s).
- "EGW00201 없이 50종목 완주" = 모든 호출이 limiter throttle 후 KIS 한도 안.
- 측정: 50호출 총 소요시간 + 임의 1초 윈도 최대 호출수.

순수 타이밍 시뮬 — broker/route_order/place_order/KIS API import·호출 0.
"""
from __future__ import annotations
import asyncio, json, time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.rate_limiter import SlidingWindowRateLimiter  # noqa: E402

OUT = Path("reports/backtest/_ratelimit_sim.json")
N_SYMBOLS = 50
KIS_LIMIT_PER_SEC = 2  # KIS 모의 quote API 문서상 한도


async def simulate(max_calls: int, window: float, n: int) -> dict:
    """n개 종목 각 1회 quote 호출을 limiter 통해 발사. 호출 시각(monotonic) 기록."""
    rl = SlidingWindowRateLimiter(max_calls=max_calls, window_seconds=window)
    call_times: list[float] = []
    t0 = time.monotonic()
    for _ in range(n):
        await rl.acquire()          # 실제 limiter throttle
        call_times.append(time.monotonic() - t0)
        # quote 자체는 mock — 즉시 반환(0ms), 순수 limiter 타이밍만 측정
    elapsed = time.monotonic() - t0

    # 임의 1초 슬라이딩 윈도 최대 호출수 (KIS 한도 위반 검출)
    max_in_1s = 0
    for ct in call_times:
        cnt = sum(1 for x in call_times if ct <= x < ct + 1.0)
        max_in_1s = max(max_in_1s, cnt)
    # window_seconds 윈도 내 최대 (limiter 자기 기준)
    max_in_window = 0
    for ct in call_times:
        cnt = sum(1 for x in call_times if ct <= x < ct + window)
        max_in_window = max(max_in_window, cnt)

    egw_risk = max_in_1s > KIS_LIMIT_PER_SEC
    return {
        "config": f"{max_calls}/{window}s",
        "max_calls": max_calls, "window_seconds": window,
        "symbols_completed": len(call_times),
        "all_50_completed": len(call_times) == n,
        "elapsed_sec": round(elapsed, 3),
        "effective_rate_per_sec": round(n / elapsed, 3) if elapsed > 0 else None,
        "max_calls_in_any_1s": max_in_1s,
        "max_calls_in_window": max_in_window,
        "kis_limit_per_sec": KIS_LIMIT_PER_SEC,
        "egw00201_risk": egw_risk,
        "verdict": "FAIL_EGW_RISK" if egw_risk else "PASS_NO_EGW",
    }


async def main():
    old = await simulate(5, 1.0, N_SYMBOLS)     # 기존 default (fix 전)
    fixed = await simulate(2, 1.1, N_SYMBOLS)    # 76fcfd9 fix
    res = {
        "scenario": "50-symbol scan, 1 quote/symbol, mock (no KIS, no orders)",
        "kis_documented_limit_per_sec": KIS_LIMIT_PER_SEC,
        "before_fix_5_1.0": old,
        "after_fix_2_1.1": fixed,
        "fix_resolves_egw": (old["egw00201_risk"] and not fixed["egw00201_risk"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print("WROTE", OUT)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
