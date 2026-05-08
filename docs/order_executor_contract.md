# Order Executor Contract (#40)

> 코드: [`backend/app/execution/order_executor.py`](../backend/app/execution/order_executor.py) (alias) + [`backend/app/execution/executor.py`](../backend/app/execution/executor.py) (구현)
> route_order: [`backend/app/execution/order_router.py`](../backend/app/execution/order_router.py)
> 마이그레이션: [`backend/alembic/versions/20260522_0018_order_audit_source.py`](../backend/alembic/versions/20260522_0018_order_audit_source.py)
> 테스트: [`backend/tests/test_order_executor.py`](../backend/tests/test_order_executor.py)

## 1. 목적

> **OrderExecutor를 표준 주문 실행 계층으로 강화한다.** 전략/AI/수동 어떤
> 출처의 주문이든 동일한 흐름을 따르고, frontend나 AI/Strategy 코드가
> BrokerAdapter를 직접 호출하지 못하게 한다.

## 2. OrderExecutor 계약

### 2.1 시그니처

```python
class OrderExecutor:
    def __init__(self, broker: BrokerAdapter, db: Session): ...
    async def execute(
        self,
        order: OrderRequest,
        audit: OrderAuditLog,
    ) -> OrderResult: ...
```

### 2.2 가드 (계약)
- `audit is None` → `ValueError("audit row is required")`
- `audit.decision ∉ {APPROVED, NEEDS_APPROVAL}` → `UnauthorizedOrderError`
- 그 외 → `broker.place_order(order)` 호출 + audit row 갱신.

`NEEDS_APPROVAL`이 허용되는 이유: PermissionGate.approve가 운영자 승인 +
re-evaluation 후 호출하는 정상 경로. audit row의 decision은 RiskManager
원래 판정(`NEEDS_APPROVAL`)을 보존하는 contract — 운영자 결정은
`PendingApproval.status` 별도 행에 기록.

### 2.3 트랜잭션 경계
호출자(route_order / PermissionGate)가 commit 시점을 결정. `execute()`는
audit row 갱신을 stage만 하고 commit하지 않는다.

## 3. 표준 주문 경로

```text
Strategy / AI / Manual
        │
        ▼
   route_order  ◄── 단일 진입점
        │
        ├─► OrderGuard.check        (#38)  ─── duplicate / cooldown / pending
        │
        ├─► RiskManager.check_order (#34)  ─── 한도 / 손실 / regime / kill switch
        │
        ├─► (NEEDS_APPROVAL) PermissionGate.submit ─── 운영자 승인 큐
        │
        ▼
   OrderExecutor.execute (#40)
        │
        ▼
   BrokerAdapter.place_order   ◄── *오직 OrderExecutor만 호출*
```

### 3.1 우회 차단
- `OrderExecutor.execute`만 `broker.place_order()`를 호출. 다른 모듈은 0건.
- 운영자가 manual API를 호출해도 `route_order`를 거친다 — `/api/broker/orders`
  POST 핸들러도 `route_order`를 사용 (기존 `routes_broker.py`).

### 3.2 audit row 단일 진실
모든 주문 흐름은 `OrderAuditLog` 한 행을 만든다 — 결정(APPROVED / REJECTED /
NEEDS_APPROVAL / BLOCKED), 사유, 체결 결과, source 모두 한 행에서 시간순으로
재구성 가능.

## 4. 직접 broker 호출 금지 보장 방식

다음을 코드 단에서 강제:

1. **단일 호출 지점**: `app/execution/executor.py`의 `OrderExecutor.execute`
   가 `broker.place_order()`를 호출하는 *유일한* 코드.
2. **모듈 grep 가드** (`tests/test_order_executor.py::TestNoDirectBrokerCalls`):
   - 16개 API 라우트 모듈
   - 6개 strategy 모듈 + 6개 concrete 전략
   - AI / Filter / Market / Explainability / Risk / Permission 전부
   - 모두 `broker.place_order(` / `BrokerAdapter.place_order(` substring 0건
3. **단일 진입점 가드** (`TestSinglePlaceOrderEntryPoint`):
   - `app.permission.gate`는 broker 직접 호출 X, OrderExecutor 경유 only.
   - `app.execution.order_router`도 broker 직접 호출 X.
4. **Audit decision 가드** (#34 backstop): OrderExecutor 자체가 `audit.decision
   ∉ {APPROVED, NEEDS_APPROVAL}`이면 `UnauthorizedOrderError`로 즉시 차단.

## 5. OrderSource 분류

`OrderRequest`와 호출 컨텍스트로부터 source를 derive해 audit row에 carry.

| Source | 조건 |
|---|---|
| `AI` | `requested_by_ai=True` (가장 강한 우선순위) |
| `STRATEGY` | not AI + `order.strategy` 설정 |
| `MANUAL` | not AI + no strategy |
| `OPERATOR_OVERRIDE` | `explicit_source="OPERATOR_OVERRIDE"` (예: 청산 / 긴급 처리) |
| `UNKNOWN` | legacy row (0018 마이그레이션 이전) — frontend에서 'UNKNOWN'으로 surface |

`derive_order_source(order, *, requested_by_ai, explicit_source=None) -> OrderSource`:
- `explicit_source`가 유효한 enum 값이면 그대로.
- 잘못된 값이면 휴리스틱 fallback (조용히 무시).

### 5.1 DB / API surface
- `OrderAuditLog.source` (nullable String(32), index) — 0018 마이그레이션 추가.
- `OrderAuditOut.source` (Pydantic, optional) — `/api/audit/orders` 응답에 carry.
- legacy row(NULL)는 그대로 None 반환 — frontend는 'UNKNOWN' 표시 권장.

## 6. 기존 호환성

- `from app.execution.executor import OrderExecutor` 그대로 작동.
- `OrderExecutor.execute(order, audit)` 시그니처 변경 0건.
- `OrderAuditLog` 다른 컬럼 / 인덱스 변경 0건.
- `OrderAuditOut` 응답 필드 추가만 (`source` optional) — 기존 클라이언트가
  무시해도 동작.
- `route_order` API contract 변경 0건.
- `RiskManager` / `PermissionGate` / `OrderGuard` 어떤 함수도 변경 0건.

## 7. 향후 과제 (Order Executor backlog)

- **risk_result 객체를 audit에 직접 carry** — 현재는 `reasons` list만 저장.
  RiskCheckResult를 JSON 컬럼으로 영구화해 사후 재구성을 정확히.
- **Frontend source filter** — `/api/audit/orders` 응답의 source로 행 필터링
  (Strategy 주문만 보기 등).
- **Per-source scoreboard** — 출처별 win_rate / 손익 분석.
- **OPERATOR_OVERRIDE 감사 강화** — 운영자가 명시 override한 주문의 별도
  로그 트레일.
- **broker statement reconciliation** — broker 발급 order_id ↔ audit 결합 (#212
  reconciliation과 통합).

## 8. 안전 invariant

- 실제 broker live order 호출 0건 (테스트 환경 + KIS_IS_PAPER=true).
- `app.execution.order_executor` 모듈에 `broker.place_order(` / `BrokerAdapter.
  place_order(` 호출 0건 (re-export only). 실제 호출은 `app.execution.executor`
  단 하나.
- LIVE flag / API Key / Secret / 계좌번호 변경 0건.
- 본 PR은 *additive* — 기존 271+ risk/route/permission/virtual 테스트 무수정
  통과.
