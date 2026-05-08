"""Order Executor — 표준 주문 실행 진입점 (#40).

체크리스트 #40는 `app/execution/order_executor.py` 파일명을 요구한다. 본
모듈은 *기존* `app/execution/executor.py`의 `OrderExecutor` /
`UnauthorizedOrderError`를 그대로 re-export 하면서 (backwards compat),
주문 source 분류 helper(`OrderSource`, `derive_order_source`)를 새로 노출한다.

주문은 다음 단일 흐름만 통과한다 (CLAUDE.md 절대 원칙 2):

```
Strategy / AI / Manual
        │
        ▼
   route_order
        │
        ├─► OrderGuard.check        (#38)  ─── duplicate / cooldown / pending
        │
        ├─► RiskManager.check_order (#34)  ─── 한도 / 손실 / regime / kill
        │
        ├─► PermissionGate.submit          ─── NEEDS_APPROVAL 큐
        │
        ▼
   OrderExecutor.execute (#40 — 본 모듈)
        │
        ▼
   BrokerAdapter.place_order
```

`OrderExecutor` 외 어떤 모듈도 `BrokerAdapter.place_order`를 직접 호출하지
않는다 — 정적 검증은 `tests/test_order_executor.py`와 `tests/test_risk_
manager_bypass.py::TestNoDirectBrokerCalls`가 grep으로 강제. 실제 주문 흐름
변경 없음 (본 PR은 alias + helper + 테스트 강화).
"""

from __future__ import annotations

from enum import StrEnum

from app.brokers.base import OrderRequest

# 기존 모듈에서 핵심 기능 re-export. 신규 호출자는 `app.execution.order_executor`
# 에서 import 권장 — 기존 import (`from app.execution.executor import ...`)도
# 모두 그대로 동작.
from app.execution.executor import (
    OrderExecutor,
    UnauthorizedOrderError,
    _EXECUTABLE_DECISIONS,
)


class OrderSource(StrEnum):
    """주문이 어디서 만들어졌는지 분류.

    - `STRATEGY`: 전략 엔진(LiveStrategyEngine)이 시그널을 주문으로 변환.
    - `AI`: AI Agent / VirtualAiAgent 등이 만든 주문.
    - `MANUAL`: 운영자 수동 주문 (frontend 또는 직접 API 호출).
    - `OPERATOR_OVERRIDE`: 운영자가 청산 / 긴급 처리로 명시 입력 — 추후 옵트인.
    - `UNKNOWN`: 기존 호출자가 source를 명시 안 한 경우 (legacy / migration 이전).
    """
    STRATEGY          = "STRATEGY"
    AI                = "AI"
    MANUAL            = "MANUAL"
    OPERATOR_OVERRIDE = "OPERATOR_OVERRIDE"
    UNKNOWN           = "UNKNOWN"


def derive_order_source(
    order:           OrderRequest,
    *,
    requested_by_ai: bool,
    explicit_source: str | None = None,
) -> OrderSource:
    """`OrderRequest`와 호출 컨텍스트로부터 source를 추정.

    분기 우선순위 (먼저 매칭이 우선):
    1. `explicit_source`가 유효한 enum 값이면 그대로.
    2. `requested_by_ai=True` → AI.
    3. `order.strategy`가 있으면 STRATEGY.
    4. 그 외 MANUAL.

    `UNKNOWN`은 본 헬퍼가 *반환하지 않는다* — 항상 위 4 분기 중 하나로 결정.
    explicit_source가 잘못된 값이면 무시하고 휴리스틱으로 fallback.
    """
    if explicit_source:
        try:
            return OrderSource(explicit_source)
        except ValueError:
            pass  # invalid value → fall through to heuristic
    if requested_by_ai:
        return OrderSource.AI
    if order.strategy:
        return OrderSource.STRATEGY
    return OrderSource.MANUAL


__all__ = [
    "OrderExecutor",
    "UnauthorizedOrderError",
    "OrderSource",
    "derive_order_source",
    "_EXECUTABLE_DECISIONS",
]
