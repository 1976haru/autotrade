"""API 요청 / 에러 카운터 — #70.

목표:
- 라우터 단의 응답 status_code, latency를 *in-memory*로 ring-buffer에 기록.
- monitoring service가 최근 N분 / N건 윈도우에서 에러율을 계산할 수 있게 노출.

본 모듈은 broker / DB / 외부 HTTP 어떤 것도 import 0건. 단순 counter +
collections.deque. 멀티 인스턴스 / 영구화는 후속 (Redis / Prometheus).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque


@dataclass(frozen=True)
class ApiCallRecord:
    """단일 요청 기록 — Secret 미포함 (path / method / status / latency만)."""
    path:        str
    method:      str
    status_code: int
    latency_ms:  float
    at_epoch:    float        # time.time()

    @property
    def is_error(self) -> bool:
        # 5xx는 명확한 server error. 4xx는 client error지만 모니터링 관점에서
        # 운영 이상 신호로 함께 본다 (지나친 400 spike도 운영 이상).
        return self.status_code >= 500 or self.status_code == 0


class ApiMetricsRegistry:
    """API 요청 / 에러 ring buffer.

    멤버:
    - max_records: ring buffer 크기 (default 2000)
    - default_window_seconds: snapshot()이 사용하는 기본 시간 윈도우 (default 300)

    invariants:
    - thread-safe (Lock으로 보호).
    - 본 클래스는 어떤 외부 시스템에도 출력하지 않는다 — *수집*만.
    """

    def __init__(
        self,
        max_records: int = 2000,
        default_window_seconds: int = 300,
    ) -> None:
        self._max_records  = int(max_records)
        self._window       = int(default_window_seconds)
        self._records:     Deque[ApiCallRecord] = deque(maxlen=self._max_records)
        self._lock         = threading.Lock()
        # 누적 카운터 — 재시작 시 0. 운영자가 *총* 호출을 보고 싶을 때 사용.
        self._total_calls  = 0
        self._total_errors = 0

    def record(
        self,
        *,
        path:        str,
        method:      str,
        status_code: int,
        latency_ms:  float,
        at_epoch:    float | None = None,
    ) -> None:
        """단일 요청 기록. raise 금지 — middleware가 fail-open."""
        try:
            rec = ApiCallRecord(
                path=str(path)[:200],
                method=str(method)[:10],
                status_code=int(status_code),
                latency_ms=float(latency_ms),
                at_epoch=float(at_epoch) if at_epoch is not None else time.time(),
            )
            with self._lock:
                self._records.append(rec)
                self._total_calls += 1
                if rec.is_error:
                    self._total_errors += 1
        except Exception:  # noqa: BLE001 — monitoring must not break responses
            pass

    def reset(self) -> None:
        """테스트 용도. 운영 코드는 호출하지 않는다."""
        with self._lock:
            self._records.clear()
            self._total_calls  = 0
            self._total_errors = 0

    def snapshot(self, window_seconds: int | None = None) -> dict:
        """최근 N초 윈도우의 집계.

        return:
        {
          "window_seconds": int,
          "calls":          int,
          "errors":         int,
          "error_rate":     float (0.0 ~ 1.0),
          "avg_latency_ms": float,
          "p95_latency_ms": float,
          "by_path":        [{"path": ..., "calls": ..., "errors": ...}, ...],
          "total_calls":    int (since process start),
          "total_errors":   int,
        }
        """
        win = int(window_seconds if window_seconds is not None else self._window)
        cutoff = time.time() - max(1, win)
        with self._lock:
            recs = [r for r in self._records if r.at_epoch >= cutoff]
            total_calls  = self._total_calls
            total_errors = self._total_errors

        calls  = len(recs)
        errors = sum(1 for r in recs if r.is_error)
        rate   = (errors / calls) if calls else 0.0

        if recs:
            latencies = sorted(r.latency_ms for r in recs)
            avg = sum(latencies) / len(latencies)
            # P95 — 작은 샘플에서는 max에 수렴.
            idx = max(0, int(round(0.95 * (len(latencies) - 1))))
            p95 = latencies[idx]
        else:
            avg = 0.0
            p95 = 0.0

        # path별 — 상위 5개만 surface.
        by_path: dict[str, dict[str, int]] = {}
        for r in recs:
            slot = by_path.setdefault(r.path, {"calls": 0, "errors": 0})
            slot["calls"]  += 1
            if r.is_error:
                slot["errors"] += 1
        top = sorted(
            ({"path": p, **v} for p, v in by_path.items()),
            key=lambda d: (-d["errors"], -d["calls"]),
        )[:5]

        return {
            "window_seconds": win,
            "calls":          calls,
            "errors":         errors,
            "error_rate":     round(rate, 4),
            "avg_latency_ms": round(avg, 2),
            "p95_latency_ms": round(p95, 2),
            "by_path":        top,
            "total_calls":    total_calls,
            "total_errors":   total_errors,
        }


# ---------- module-level singleton ----------

_REGISTRY: ApiMetricsRegistry | None = None


def get_api_metrics() -> ApiMetricsRegistry:
    """프로세스 단일 인스턴스. 테스트는 `reset()`로 초기화."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ApiMetricsRegistry()
    return _REGISTRY
