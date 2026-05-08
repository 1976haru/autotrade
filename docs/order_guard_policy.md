# Order Guard Policy (#38)

> 코드: [`backend/app/risk/order_guard.py`](../backend/app/risk/order_guard.py)
> 통합: [`backend/app/execution/order_router.py`](../backend/app/execution/order_router.py) — `route_order` 첫 단계에서 호출
> 테스트: [`backend/tests/test_order_guard.py`](../backend/tests/test_order_guard.py)

## 1. 목적

> **봇 오류 / 네트워크 재시도 / AI 반복 판단으로 같은 주문이 여러 번 broker에
> 도달하는 사고를 차단한다.**

RiskManager가 *한도 / 자본 / 손실 / regime* 차원 가드라면, OrderGuard는
*주문 흐름 자체*의 가드 — 같은 주문 반복, 짧은 시간 폭주, 미체결 위에 또 신규
주문을 막는다.

## 2. 네트워크 재시도 vs 중복 주문 구분

핵심 원칙: 두 케이스를 명확히 분리.

| 상황 | 판정 |
|---|---|
| 호출자가 같은 `client_order_id`로 재요청 | `RETRY_REPLAY` — 안전 (네트워크 재시도) |
| 다른 `client_order_id`인데 같은 fingerprint | `DUPLICATE` — 차단 (실제 중복 주문) |
| `client_order_id` 없는데 같은 fingerprint | `DUPLICATE` — 차단 |

`RETRY_REPLAY`는 새 주문을 만들지 않고 기존 audit_id를 carry — 호출자가 같은
결과를 재사용 가능. `DUPLICATE`는 audit row를 REJECTED로 작성하고 broker
호출 회피.

## 3. 중복 주문 기준 (fingerprint)

`build_order_fingerprint(order, mode, price_bucket_pct, agent_chain_id)`는
다음을 SHA-256 (12 hex prefix)로 hash:

| 필드 | 의미 |
|---|---|
| `symbol` | 종목 코드 |
| `side` | BUY/SELL |
| `quantity` | 주문 수량 |
| `order_type` | MARKET/LIMIT |
| `price_bucket` | limit 주문의 가격을 `price_bucket_pct`(기본 0.5%) 단위로 round. market은 무시 |
| `strategy` | 전략 이름 (None이면 빈 문자열) |
| `mode` | 운용모드 |
| `agent_chain_id` | Agent Council chain 식별자 (옵션) |

반환: `"of_<12-hex>"` 형식.

**Secret 미포함** — 계좌번호 / API key / 운영자 식별자는 입력으로 받지 않는다.
fingerprint는 audit row / UI에 그대로 carry 가능.

가격 bucket 정책:
- 너무 넓으면 정상 주문이 막힌다.
- 너무 좁으면 중복 방지가 안 된다.
- 기본 0.5%는 KOSPI 분봉 호가 단위 가정.

## 4. 쿨타임 정책

`OrderGuardConfig` 필드 (모두 default 0 = 비활성):

| 필드 | 의미 |
|---|---|
| `symbol_cooldown_seconds` | 같은 symbol에 마지막 주문 후 N초 cooldown |
| `strategy_symbol_cooldown_seconds` | (strategy, symbol)별 cooldown |
| `post_exit_cooldown_seconds` | SELL(=청산) 직후 같은 symbol BUY 차단 |
| `ai_extra_cooldown_seconds` | AI 경로 추가 cooldown (cooldown 위에 누적) |

post-exit는 BUY에만 적용 — SELL이 SELL을 막지 않는다 (리스크 축소 보호).
ai_extra는 manual 주문은 그대로 통과 — AI 흐름만 더 보수적.

## 5. Pending order guard

`block_when_pending_same_side=True`이면 같은 symbol + side의 미체결 / 승인
대기가 있을 때 신규 같은 방향 주문을 차단:

소스:
- `PendingApproval` 중 `status="PENDING"` (운영자 승인 큐).
- `OrderAuditLog` 중 `decision="NEEDS_APPROVAL"` (drift detector).

다른 side는 통과 — pending BUY가 SELL을 막지 않는다.

## 6. BUY와 SELL 차이

| 가드 | BUY 적용 | SELL 적용 |
|---|---|---|
| `duplicate_window` | ✅ | ✅ |
| `symbol_cooldown` | ✅ | ✅ |
| `strategy_symbol_cooldown` | ✅ | ✅ |
| `post_exit_cooldown` | ✅ | ❌ (skip) |
| `ai_extra_cooldown` | ✅ | ✅ |
| `pending guard` | ✅ (같은 BUY) | ✅ (같은 SELL) |

신규 BUY는 강하게 제한, SELL은 리스크 축소 목적 — 다만 *반복 SELL*은
duplicate / cooldown / pending이 막는다 (의도하지 않은 폭주 방어).

## 7. Audit 연계

OrderGuard가 ALLOW 외 결정을 반환하면 `route_order`이:
1. broker 호출 *없이* `OrderAuditLog` row를 작성 (decision=REJECTED).
2. `reasons`에 차단 사유 (cooldown remaining / fingerprint match / pending
   id 등) carry.
3. `OrderRoutingResult.decision=REJECTED`로 호출자에 surface.

이후 audit row는 `/api/audit/orders` / `#33 SignalExplainabilityPanel`에서
운영자에게 표시 가능. `RETRY_REPLAY`는 새 row 없이 기존 audit_id를 결과에
carry (별도 옵트인 PR로 호출자 측 처리 통합 예정).

## 8. RiskManager / route_order 통합

`route_order`의 호출 순서:

```
1. client_order_id 기반 idempotency (#140) — 같은 key 재요청 → DuplicateOrderError
2. OrderGuard.check (#38)              — fingerprint/cooldown/pending
3. AI rate limit (#161) / global rate limit (#177) / max_orders_per_day (#183)
4. broker.get_price/balance/positions
5. RiskManager.evaluate_order
6. PermissionGate.submit / OrderExecutor.execute
```

OrderGuard는 broker / risk 호출 비용을 들이기 *전*에 차단 — 효율 + 안전.

## 9. 향후 과제 (Order Guard backlog)

- **Redis 기반 분산 OrderGuard** — multi-instance 환경에서 fingerprint
  중복 방지를 process-local 메모리가 아닌 shared store로.
- **DB 기반 idempotency store** — `idempotency_request` 테이블에 client_order_id
  + 결과를 캐시해 RETRY_REPLAY 시 정확히 같은 응답 재사용.
- **broker order id reconciliation** — broker가 발급한 order_id와 audit row
  의 client_order_id mapping. broker 단의 중복 방지와 통합.
- **OrderAuditLog.fingerprint 컬럼** — 매 평가에서 in-memory 재계산 대신
  영구화. row 수가 많은 운영 환경에서 query 효율.
- **Frontend idempotency_key 자동 생성** — UUID 기반 client_order_id를 모든
  manual / virtual 주문에 자동 붙임. 같은 버튼 연타 → 같은 key → RETRY_REPLAY.
- **per-strategy cooldown override** — 전략별 더 보수적/공격적 cooldown.

## 10. 안전 invariant

- broker.place_order / broker.cancel_order / OrderExecutor / route_order 어떤
  함수도 `app/risk/order_guard.py`가 직접 호출하지 않음 — 테스트 가드.
- DB write 0건 — 본 가드는 read-only. audit row 작성은 호출자(`route_order`)
  의 책임.
- 기존 RiskCheckResult / RiskDecision 응답 contract 변경 0건 — 본 가드는
  RiskManager *전*에 동작.
- 모든 OrderGuardConfig 필드 default 0/False = 검사 비활성 → 기존 호출자
  / 테스트 무수정 통과.
- Secret(계좌번호/API key) 노출 0건 — fingerprint 입력에 포함되지 않는다.
- LIVE flag / API Key / 계좌번호 변경 0건.
