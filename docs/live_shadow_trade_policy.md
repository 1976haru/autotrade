# LIVE_SHADOW ShadowTrade 정책 (#43)

본 문서는 `LIVE_SHADOW` 모드에서 동작하는 **`shadow_trade`** 영구 기록의 설계와 invariant를 정의합니다. CLAUDE.md 절대 원칙(특히 5/7) + `risk_manager_contract.md` + `order_executor_contract.md`의 하위 정책으로, 그 위에서 *추가*되는 read-only 추정 기록이라는 점이 핵심입니다.

운영자 절차 + 환경 설정은 [`shadow_mode.md`](shadow_mode.md)에 있습니다.

## 1. 한 줄 요약

`LIVE_SHADOW`에서 들어온 모든 주문 후보는 `OrderAuditLog`에 `decision=REJECTED`로 기록되는 동시에, **`shadow_trade`** 테이블에 *would-have 정보 + 추정 체결가*가 기록됩니다. broker.place_order는 호출되지 않습니다.

## 2. 왜 별도 테이블이 필요한가

`OrderAuditLog`만 보면 LIVE_SHADOW 행은 모두 `decision=REJECTED`이며, reason에는 항상 `"LIVE_SHADOW records signals only; live orders disabled"`가 들어갑니다. 이 상태로는 운영자가:

- “LIVE_SHADOW gate를 빼면 실제로 통과했을 후보 vs 다른 가드(긴급정지/포지션 한도)에서도 거부됐을 후보”를 구분할 수 없음
- 추정 체결가를 `OrderAuditLog`에 직접 적으면 “실 체결 결과”와 혼동될 위험

`shadow_trade`는 이 두 정보를 **별도 테이블**에 분리해서 유지합니다. 운영자가 사후 분석할 때 “실제 체결 가능성”과 “추정 체결”의 경계가 코드 단에서 명확합니다.

## 3. 절대 invariant

| 항목 | 보장 |
|---|---|
| `actual_broker_order_sent` | **항상 False** (column default + 본 PR의 어떤 코드 경로도 True로 set하지 않음) |
| broker.place_order 호출 수 | **0** (RiskManager가 LIVE_SHADOW를 항상 REJECTED로 변환 → OrderExecutor의 `_EXECUTABLE_DECISIONS` 가드 backstop이 마지막 차단) |
| `routes_shadow.py`가 broker / AI client를 import | **금지** (`test_shadow_trade.py::test_routes_shadow_does_not_import_broker`로 강제) |
| 비-`LIVE_SHADOW` 모드에서 `shadow_trade` row 작성 | **금지** (`test_simulation_mode_does_not_write_shadow_trade`로 강제) |

LIVE_SHADOW 가드 체인은 다음 3단계로 deepen된 상태이며, 본 PR에서 어느 한 층도 약화하지 않았습니다:

1. **RiskManager** — LIVE_SHADOW의 모든 주문에 `SHADOW_RECORD_ONLY_REASON`을 reason으로 누적 → `decision=REJECTED`
2. **OrderExecutor `_EXECUTABLE_DECISIONS`** — REJECTED는 broker로 도달 X (`UnauthorizedOrderError`)
3. **KisBrokerAdapter.place_order** — `is_paper=False`에서 `NotImplementedError`

`shadow_trade`는 이 가드 체인 *밖*에 있는 기록 채널이므로, 위 가드 약화 없이도 추정 정보를 영구화합니다.

## 4. would_have_decision 산출 contract

`route_order`가 `shadow_trade` row를 만들 때 다음 규칙을 적용합니다:

```python
SHADOW_GATE_REASONS = frozenset({
    SHADOW_RECORD_ONLY_REASON,
    "live trading is disabled by global safety flag",
})

non_shadow_reasons = [r for r in decision.reasons if r not in SHADOW_GATE_REASONS]
shadow_would_have = "APPROVED" if not non_shadow_reasons else "REJECTED"
```

즉:

- `would_have_decision=APPROVED` ⟺ RiskManager의 reason이 *오직* LIVE_SHADOW 운영 게이트(상단 frozenset)뿐
- `would_have_decision=REJECTED` ⟺ 다른 risk rule(긴급정지/포지션 한도/시세 stale/일일 손실 등)도 함께 거부

`would_have_reasons`에는 위 frozenset의 reason은 *제외*하고, 그 외 reason만 carry합니다 — 운영자가 다음 단계로 승격(예: `LIVE_MANUAL_APPROVAL`)했을 때 자연스럽게 사라지는 reason은 분석에서 빼는 것이 자연스럽습니다.

### 분석 예시

| `would_have_decision` | 의미 |
|---|---|
| `APPROVED` | RiskManager의 모든 “실제 risk rule”을 통과한 후보. PAPER/LIVE_MANUAL_APPROVAL 단계에서 실 체결 가능성이 가장 높음 |
| `REJECTED` (`would_have_reasons=["emergency_stop"]`) | LIVE_SHADOW 게이트가 없어도 긴급정지에 의해 거부될 후보 |
| `REJECTED` (`would_have_reasons=["per_order_notional_max"]`) | 1회 주문 한도 초과 — 운영자가 한도를 넓히지 않으면 LIVE에서도 거부될 후보 |

`avg_estimated_slippage_bps` 같은 통계는 `would_have_decision=APPROVED` row에만 의미가 있습니다.

## 5. 추정 체결가 (estimated_fill_price)

본 PR의 추정 모델은 가장 단순한 proxy 1단계입니다:

| 필드 | 값 | 의미 |
|---|---|---|
| `estimation_method` | `"latest_price_proxy"` | latest_price를 그대로 추정 체결가로 사용 |
| `estimated_fill_price` | `latest_price` | proxy — orderbook depth 미반영 |
| `estimated_slippage_bps` | `0.0` | proxy — 슬리피지 가정 없음 |
| `confidence_note` | `"추정치 — 실 체결과 다를 수 있다 …"` | UI/문서에 동일 문구 노출 |

향후 orderbook depth / partial fill / TIF 모델을 추가할 때는 새 `estimation_method` 문자열을 부여하고, 본 문서에 method 비교표를 추가합니다. 기존 row의 method는 immutable — backfill 금지.

### 실 체결과 다를 수 있는 이유

- **Orderbook depth** — 1호가 매물이 1주뿐인데 100주 주문하면 다단계 호가에 걸쳐 체결됨
- **호가 공백** — KOSPI 호가 단위(가격 구간별 1/5/10/50/100/500원) 사이의 점프
- **부분체결** — `filled_quantity < quantity`로 끝날 수 있음 (특히 IOC)
- **시장 충격** — 같은 가격대 매물을 다 먹으면 다음 호가로 넘어감
- **TIF** — IOC/FOK는 즉시 체결되지 않으면 취소
- **수수료** — taker fee, 거래세, 매도 시 0.23% 등

따라서 LIVE_SHADOW 통계는 **전략 통계의 상한선** 정도로만 해석합니다. 실 체결 reconciliation은 PAPER 단계의 `app/reconciliation/` 모듈에서 별도 측정합니다.

## 6. 비-LIVE_SHADOW 모드는 작성하지 않음

`shadow_trade` row는 `route_order`가 `mode == OperationMode.LIVE_SHADOW` *그리고* `decision == RiskDecision.REJECTED`인 경우에만 작성됩니다.

- `SIMULATION` / `PAPER` / `LIVE_MANUAL_APPROVAL` 등 다른 모드에서의 REJECTED 주문은 `OrderAuditLog`에만 기록됨
- 같은 모드의 APPROVED / NEEDS_APPROVAL은 `shadow_trade`와 무관

이 분리는 LIVE_SHADOW 분석 surface가 다른 모드 데이터로 “희석”되는 것을 막습니다. 테스트로 강제: `test_simulation_mode_does_not_write_shadow_trade`, `test_simulation_rejection_does_not_write_shadow_trade`.

## 7. API surface

| Endpoint | 메서드 | 의미 |
|---|---|---|
| `/api/shadow/trades` | GET | 목록 (created_at desc), `symbol` / `strategy` / `would_have_decision` 필터 |
| `/api/shadow/summary` | GET | 카운트 + 평균 슬리피지 + invariant note |

두 endpoint 모두 **DB SELECT only**. broker / AI client / RiskManager / PermissionGate 어떤 것도 호출하지 않습니다 (`test_routes_shadow_does_not_import_broker`로 강제).

## 8. UI

- **Dashboard Shadow 카드** ([`ShadowSummaryCard.jsx`](../frontend/src/components/tabs/ShadowSummaryCard.jsx))
  - 총 기록 / would-have 통과 / 다른 가드 거부 카운트
  - `actual_broker_orders_sent` 타일 — 0이 아닐 시 붉은색 + “invariant 위반” 배지
  - 평균 추정 슬리피지 (bps, 소수점 2자리)
  - “실제 주문 아님” 배지 (회색)
  - LIVE_SHADOW disclaimer 문구

별도 list 탭은 본 PR 범위 밖 — 운영자가 상세 row를 보려면 `/api/shadow/trades?...` 직접 호출하거나 다음 PR에서 추가합니다.

## 9. 다음 단계 (#44 이후, 본 PR 범위 외)

본 PR에서 다루지 않는 항목 (44번 이후):

- Orderbook 기반 slippage 추정 (`estimation_method` 추가)
- Shadow trade dedicated 탭 (필터/정렬/검색)
- ShadowTrade vs PAPER 체결 reconciliation report
- LIVE_MANUAL_APPROVAL 라우팅 (KIS LIVE place_order/cancel_order 활성화) — 별도 옵트인 PR

## 10. 변경 시 동기화

다음 변경은 본 문서를 함께 업데이트해야 합니다:

- `SHADOW_GATE_REASONS` 상수에 reason 추가/제거
- `estimation_method` 신규 또는 기존 method 의미 변경
- `shadow_trade` 컬럼 추가/삭제
- `actual_broker_order_sent` invariant를 깨는 새 코드 경로 (현재 0건)
- `/api/shadow/*` 신규 endpoint
