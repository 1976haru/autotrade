"""ASGI middleware — 모든 요청을 ApiMetricsRegistry에 기록 (#70).

CLAUDE.md 절대 원칙:
- 본 middleware는 *기록*만 한다. 어떤 핸들러 응답도 변형하지 않는다.
- 실패해도 응답은 그대로 진행 (fail-open) — 모니터링이 운영을 막으면 안 된다.
"""

from __future__ import annotations

import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.monitoring.api_metrics import ApiMetricsRegistry, get_api_metrics


class ApiMetricsMiddleware(BaseHTTPMiddleware):
    """FastAPI / Starlette middleware. dispatch에서 응답 status_code + 처리시간 측정."""

    def __init__(self, app, registry: ApiMetricsRegistry | None = None) -> None:
        super().__init__(app)
        self._registry = registry or get_api_metrics()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(response.status_code)
            return response
        except Exception:  # noqa: BLE001 — re-raise, but record as 500
            status_code = 500
            raise
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            try:
                self._registry.record(
                    path=request.url.path,
                    method=request.method,
                    status_code=status_code,
                    latency_ms=elapsed_ms,
                )
            except Exception:  # noqa: BLE001 — never break response
                pass
