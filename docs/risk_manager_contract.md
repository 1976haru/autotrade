# RiskManager Contract — 표준 진입점 (#34)

> 코드: [`backend/app/risk/risk_manager.py`](../backend/app/risk/risk_manager.py)
> 우회 방지 가드: [`backend/app/execution/executor.py`](../backend/app/execution/executor.py) — `UnauthorizedOrderError`
> 테스트: [`backend/tests/test_risk_manager_bypass.py`](../backend/tests/test_risk_manager_bypass.py)

## 1. 목적

> **모든 주문성 요청은 RiskManager.check_order(order, context)를 통과해야 한다.**

전략(Strategy) / AI Agent / 운영자 수동주문 — 어떤 경로로 만들어진 주문이라도
broker.place_order로 가기 전에 반드시 RiskManager 평가를 거친다. 본 PR(#34)
은 표준 진입점 `check_order(order, context)`를 추가해 호출자가 일관된
컨텍스트를 넘기도록 하고, 마지막 backstop으로 OrderExecutor에 audit row
decision 가드를 추가했다.

## 2. 표준 경로

```text
Strategy / Agent / Manual
        │
        ▼
   route_order
        │
        ├─► RiskManager.check_order  ◄── 표준 진입점 (#34)
        │       │
        │       ▼
        │   RiskCheckResult { decision, reasons, blocked_by, ... }
        │
        ├─► (NEEDS_APPROVAL) → PermissionGate.submit
        │                             │
        │                             ▼
        │                    PermissionGate.approve  ◄── re-evaluation
        │                             │
        ▼                             ▼
   (APPROVED)                  OrderExecutor.execute
                                      │
                                      ▼ (audit.decision in {APPROVED,
                                      │   NEEDS_APPROVAL} 만 통과)
                              BrokerAdapter.place_order
```

## 3. 금지 경로

다음 경로는 **테스트 + 가드로 차단**:

- ❌ Strategy → BrokerAdapter 직접 호출
- ❌ Agent → BrokerAdapter 직접 호출
- ❌ API route → BrokerAdapter.place_order 직접 호출
- ❌ Filter / SignalQuality / Explainability → BrokerAdapter 호출
- ❌ OrderExecutor 직접 호출 (RiskDecision 없는 audit row로)

테스트 가드 (`tests/test_risk_manager_bypass.py`):
- `TestNoDirectBrokerCalls` — 전략/필터/설명/마켓/신호품질 모듈에 `.place_order(`
  substring 없음.
- `TestExecutorBypass` — `audit.decision ∉ {APPROVED, NEEDS_APPROVAL}`이면
  `UnauthorizedOrderError` 발생.

## 4. check_order 입력/출력

### 4.1 입력 — `RiskContext`

```python
@dataclass
class RiskContext:
    mode:                   OperationMode
    balance:                Balance
    positions:              list[Position]
    latest_price:           int
    latest_price_timestamp: datetime | None = None
    requested_by_ai:        bool = False
    market_regime:          str | None = None        # advisory (#32)
    market_regime_decision: str | None = None        # ALLOW/REDUCE_SIZE/WATCH_ONLY/BLOCK_NEW_BUY
    emergency_stop_override: bool | None = None
    operator_id:            str | None = None
    metadata:               dict[str, Any] | None = None
```

### 4.2 출력 — `RiskCheckResult`

```python
@dataclass
class RiskCheckResult:
    decision:         RiskDecision   # APPROVED / REJECTED / NEEDS_APPROVAL / REDUCED / BLOCKED
    reasons:          list[str]      # FAIL/BLOCKED 사유 (운영자/audit surface)
    passed:           list[str]      # 통과한 가드 (디버깅 + UI 'PASS' 카드)
    warnings:         list[str]      # advisory note (예: REDUCE_SIZE 권고)
    risk_score:       int | None     # 0-100 종합 점수 (옵션)
    blocked_by:       str | None     # emergency_stop / stale_price / market_regime / ai_kill_switch / live_trading_disabled / policy_violation 등
    required_action:  str | None     # OPERATOR_RESET / WAIT_FOR_FRESH_DATA / MANUAL_APPROVAL 등
    normalized_order: OrderRequest | None  # REDUCED 시 사이즈 축소된 주문
    evaluated_at:     datetime
    # 속성:
    allowed:  bool   # decision == APPROVED
    status:   str    # decision.value (직렬화용)
    to_dict() -> dict
```

### 4.3 RiskDecision 값

| 값 | 의미 | OrderExecutor 실행 가능 |
|---|---|---|
| `APPROVED` | 모든 가드 통과 | ✅ |
| `NEEDS_APPROVAL` | 운영자 승인 필요 (LIVE_MANUAL_APPROVAL 등) | ✅ (PermissionGate.approve 경로) |
| `REJECTED` | 정책 위반으로 거부 | ❌ |
| `BLOCKED` | hard 가드(emergency_stop / stale price / kill-switch / market regime BLOCK_NEW_BUY) | ❌ |
| `REDUCED` | 사이즈 축소가 필요 — 호출자가 normalized_order로 재요청 | ❌ |

## 5. 차단 조건 (모든 BUY 기준)

`check_order`가 `evaluate_order`를 위임 + 추가 분류:

| 조건 | 결정 | blocked_by |
|---|---|---|
| `emergency_stop` ON | BLOCKED | `emergency_stop` |
| `emergency_stop_override` 명시 | BLOCKED | `emergency_stop_override` |
| stale price (timestamp 초과) | BLOCKED | `stale_price` |
| AI kill-switch ON + AI order | BLOCKED | `ai_kill_switch` |
| LIVE flag OFF + LIVE 모드 | BLOCKED | `live_trading_disabled` |
| AI execution flag OFF + AI 경로 | BLOCKED | `ai_execution_disabled` |
| `max_order_notional` 초과 | REJECTED | `policy_violation` |
| `max_position_size_pct` 초과 | REJECTED | `policy_violation` |
| `max_daily_loss` 도달 | REJECTED | `policy_violation` |
| 자금 부족 | REJECTED | `policy_violation` |
| `max_positions` / `max_symbol_exposure` 초과 | REJECTED | `policy_violation` |
| `max_total_exposure` 초과 | REJECTED | `policy_violation` |
| `symbol_whitelist` 미등록 | REJECTED | `policy_violation` |
| 시장 시간 외 | REJECTED | `policy_violation` |
| AI rate limit / global rate limit (route_order) | REJECTED | (route_order에서) |
| max_orders_per_day (route_order) | REJECTED | (route_order에서) |
| **#32 market regime BLOCK_NEW_BUY** | BLOCKED | `market_regime` |
| **#32 market regime WATCH_ONLY** | BLOCKED | `market_regime` |
| `LIVE_MANUAL_APPROVAL` / `LIVE_AI_ASSIST` | NEEDS_APPROVAL | (required_action=`MANUAL_APPROVAL`) |

## 6. BUY와 SELL 차이

**SELL/청산 주문은 신규 BUY와 별도 정책**. 리스크 축소 주문을 regime 가드로
막으면 시장이 악화되는 와중 손절을 못 하게 되어 더 위험하다.

`check_order`의 `market_regime_decision` 가드:

- `BLOCK_NEW_BUY`: BUY만 차단, SELL은 통과.
- `WATCH_ONLY`: BUY만 차단, SELL은 통과.
- `REDUCE_SIZE`: BUY는 advisory warning만 추가, action은 그대로.

기존 `evaluate_order`의 SELL 정책도 동일:
- `max_positions` / `max_symbol_exposure` / `insufficient_cash`는 BUY에만
  적용 (기존 코드 참조).
- emergency_stop / stale price / live trading flag는 모든 side에 적용.

## 7. Audit 연계

- 모든 `route_order` 호출은 `OrderAuditLog` 한 행을 기록 — APPROVED /
  REJECTED / NEEDS_APPROVAL / BLOCKED 모두.
- `audit.decision` = `RiskDecision.value`. `audit.reasons` = 사유 list.
- `RiskCheckResult.to_dict()` 결과는 explainability layer (#33) 가
  `extract_reasons_from_audit_row`로 다시 합성 → `/api/signals/{id}/explain`
  에서 PASS/WARN/FAIL/BLOCKED 카드로 surface.
- 차단된 주문도 audit에 남아 운영자가 사후 분석 가능.

## 8. 우회 방지 backstop

`OrderExecutor.execute(order, audit)`는:

1. `audit is None` → `ValueError`
2. `audit.decision ∉ {APPROVED, NEEDS_APPROVAL}` → `UnauthorizedOrderError`

이 가드 덕분에 누군가가:
- RiskManager 거치지 않고 audit row를 직접 만들어서
- broker.place_order를 우회 호출

하려는 시도는 즉시 차단된다. PermissionGate.approve는 운영자 승인 + 재평가
후 호출하므로 NEEDS_APPROVAL audit row의 정상 경로 — 이 한 가지 예외만
허용된다. (자세한 컨트랙트는 `executor.py` 주석 + test_virtual_flow_e2e
참조).

## 9. 호환성

- 기존 `evaluate_order(order, mode, balance, positions, latest_price, ...)`
  시그니처는 그대로 유지. `check_order`가 내부적으로 호출.
- 기존 27 가드 / 174 / 175 / 176 / 177 / 178 / 179 / 181 / 182 / 183 등
  로직은 변경 없음.
- `RiskCheckResult`는 dataclass에 optional 필드만 추가 — 기존 호출자가 새
  필드를 무시해도 동작.
- `RiskDecision` enum에 `REDUCED` / `BLOCKED` 추가는 additive — 기존 매칭
  로직은 그대로.

## 10. 향후 과제

- **duplicate order detector** — 현재는 client_order_id 기반 idempotency만.
  fingerprint 기반(symbol+side+qty+price+window) detector 추가.
- **Redis 기반 risk state** — daily_realized_pnl / emergency_stop을 다중
  프로세스에서 공유. 현재는 in-memory.
- **broker reconciliation 연계** (#212) — drift 감지되면 자동 emergency_stop.
- **market regime hard gate** — 현재는 context.market_regime_decision으로
  명시 주입. 호출자가 `MarketRegimeFilter`를 자동 호출해 채우게 통합.
- **REDUCED decision의 자동 재요청** — normalized_order로 다시 check_order
  호출하는 wrapper.
- **RiskDecision per-strategy override** — 전략별 더 보수적/공격적 임계.

## 11. 안전 invariant

- broker / RiskManager / PermissionGate / OrderExecutor 호출은 단일 진입점
  `route_order`만이 수행.
- 어떤 분기에서도 `audit.executed=True`는 RiskCheckResult의 명시 승인 후에만
  설정.
- LIVE 활성화 / AI 자동 실행 / 선물 라이브는 본 PR에서 변경 0건.
- `backend/.env` 변경 0건. API Key / Secret / 계좌번호 변경 0건.
