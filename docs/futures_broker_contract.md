# Futures Broker Contract (#47)

본 문서는 주식 [`BrokerAdapter`](../backend/app/brokers/base.py)와 **별개** 계층으로 분리된 [`FuturesBrokerAdapter`](../backend/app/brokers/futures_base.py)의 인터페이스 contract를 정의한다. CLAUDE.md 절대 원칙 6 + [`futures_scope.md`](futures_scope.md)(#46) + [`futures_simulation_report.md`](futures_simulation_report.md)(#151) 위에서, 향후 LIVE 어댑터(KIS / Kiwoom / 해외선물)가 구현해야 할 *공식* 메서드/모델/제약을 명문화한다.

본 PR 시점에 LIVE 어댑터는 추가하지 않는다. 본 contract만 정리한다.

## 1. 주식 BrokerAdapter와 분리

| 항목 | 주식 | 선물 |
|---|---|---|
| ABC 위치 | `app.brokers.base.BrokerAdapter` | `app.brokers.futures_base.FuturesBrokerAdapter` (≡ `app.futures.base.FuturesBrokerAdapter`) |
| 주문 모델 | `OrderRequest` (symbol, quantity = 주식 수) | `FuturesOrder`/`FuturesOrderRequest` (contract, quantity = **계약 수**) |
| 잔고 모델 | `Balance` (cash, equity, buying_power) | `FuturesBalance`/`FuturesMarginSnapshot` (cash, margin_used, margin_available, equity, maintenance_margin_required) |
| 포지션 모델 | `Position` (symbol, quantity, avg_price) | `FuturesPosition` (contract, side LONG/SHORT, quantity, entry_price, market_price, margin_used, **liquidation_price**) |
| RiskManager | `app.risk.risk_manager.RiskManager` (#34) | `app.futures.risk.FuturesRiskManager` (#151) |

**MRO 분리**: `FuturesBrokerAdapter`는 주식 `BrokerAdapter`를 *상속하지 않는다*. 정적 테스트(`test_futures_broker_adapter_does_not_inherit_from_stock_broker`)가 invariant를 lock한다 — 어떤 한쪽이 다른 쪽 메서드를 우회 호출하는 경로를 영구 차단.

## 2. 왜 분리해야 하는가

선물 주문은 주식과 본질적으로 다른 차원을 갖는다 — 단일 ABC로 통합하면 모든 메서드 시그니처에 optional 필드가 늘어나 타입 안전성이 깨진다.

| 차원 | 주식 | 선물 |
|---|---|---|
| **계약 수** (quantity) | 주식 수 (1주 = 1주식 가격) | 계약 수 (1계약 = mark_price × multiplier) |
| **증거금** | 없음 (현금 결제) | initial / maintenance margin |
| **만기** | 없음 | 매월/분기 만기, SQ 처리 필요 |
| **롤오버** | 없음 | 근월물 → 차월물 자동/수동 전환 |
| **틱가치** | 호가 단위표 (가격 구간별) | 상품별 multiplier (KOSPI200: 0.05pt × 250,000원 = 12,500원) |
| **레버리지** | 1배 | 5–50배 (상품별) |
| **청산가격** | 없음 | broker 강제청산 가격 |
| **통화** | KRW | KRW / USD (해외선물) |
| **거래시간** | 09:00–15:30 KST | 정규 + 야간 / 24시간 |

본 contract는 이 차원들을 `FuturesContractSpec` / `FuturesOrder` / `FuturesMarginSnapshot` / `FuturesPosition`에 분산해 모델링한다.

## 3. FuturesBrokerAdapter 메서드

```python
class FuturesBrokerAdapter(ABC):
    async def get_quote(contract_code) -> FuturesQuote
    async def get_balance() -> FuturesBalance
    async def get_positions() -> list[FuturesPosition]
    async def place_order(order: FuturesOrderRequest) -> FuturesOrderResult
    async def cancel_order(order_id) -> FuturesOrderResult
    async def get_order_status(order_id) -> FuturesOrderResult
```

| 메서드 | 의미 | LIVE 어댑터 책임 |
|---|---|---|
| `get_quote(contract_code)` | 호가 / 마지막 체결가 조회 | broker REST 호가 endpoint 호출, KST timestamp parsing |
| `get_balance()` | 증거금 + 예수금 조회 | broker 잔고 endpoint, FuturesMarginSnapshot으로 변환 가능 |
| `get_positions()` | 보유 포지션 조회 | LONG/SHORT 분리, liquidation_price 채우기 |
| `place_order(order)` | **신규/청산 주문**. 본 메서드 호출 *전*에 `FuturesRiskManager`(#151) 통과 필수 | broker REST order endpoint, idempotency(client_order_id) 적용 |
| `cancel_order(order_id)` | 미체결 취소 | broker REST cancel endpoint |
| `get_order_status(order_id)` | 주문 상태 조회 | 체결/부분체결/취소/거부 분류 |

**공식 인터페이스 입구**: `app.brokers.futures_base` 모듈을 import하면 위 ABC + 모든 모델 + helper를 한 번에 받는다. LIVE 어댑터는 본 모듈만 의존하면 충분.

## 4. 모델 (`FuturesContractSpec` / `FuturesOrder` / `FuturesMarginSnapshot`)

### `FuturesContractSpec`

거래소 + broker가 공시하는 contract 메타데이터를 한 객체로:

| 필드 | 의미 |
|---|---|
| `code` | broker contract code (예: `"KOSPI200_2503"`) |
| `underlying` | 기초자산 코드 (예: `"KOSPI200"`) |
| `expiry` | 만기 datetime (KST 권장) |
| `multiplier` | 1계약 명목금액 = `mark_price × multiplier` (예: 250,000) |
| `tick_size_pt` / `tick_value_krw` | 호가 단위 / 호가당 가치 (예: 0.05pt / 12,500원) |
| `leverage_max` | 거래소/broker 한도 (`FuturesRiskPolicy.max_leverage`와 별개 — 작은 값이 효력) |
| `currency` | KRW / USD |
| `market_open_kst` / `market_close_kst` | 정규 거래시간 |

운영자가 정적 매핑(또는 LIVE 어댑터가 `get_contract_spec(code)`)으로 주입한다. 본 PR은 *모델만* 정의하며, 실제 값 주입은 별도 PR.

### `FuturesOrder` (legacy `FuturesOrderRequest` + audit 필드)

```
FuturesOrderRequest 기본 필드: contract, side, quantity (= 계약 수), order_type, limit_price
+ audit 필드: client_order_id, trade_reason, strategy, signal_strength,
              signal_confidence, ai_decision_meta
```

주식 `OrderRequest`(#138/139/140/152)와 일관된 audit 필드 구성으로, 향후 선물 audit log에 동일 metadata를 carry한다. **`quantity`는 계약 수** — 명목금액은 `quantity * mark_price * multiplier`로 별도 산출.

### `FuturesMarginSnapshot`

`FuturesBalance`의 모든 필드 + `maintenance_margin_required` + `margin_call`. broker API가 maintenance margin을 직접 제공하지 않을 수 있어 어댑터가 자체 산출 후 반환하는 형식으로 통일.

`FuturesMarginSnapshot.from_balance(balance)` helper로 legacy `FuturesBalance` → richer snapshot 변환.

## 5. 만기 / 롤오버 helper

```python
days_to_expiry(expiry, now=None) -> int
is_contract_expiring_soon(spec, now=None, threshold_days=5) -> bool
should_rollover(spec, now=None, threshold_days=5) -> bool
```

| helper | 의미 | 반환 |
|---|---|---|
| `days_to_expiry` | 만기까지 calendar day floor (영업일 캘린더는 별도 PR) | int (음수 = 이미 만료) |
| `is_contract_expiring_soon` | 만기 ≤ threshold_days면 True | bool — 신규 진입 차단 advisory |
| `should_rollover` | 동일 임계 — 롤오버 advisory | bool — **자동 주문 트리거 X** |

**모든 helper는 advisory** — broker 호출 / 자동 주문 실행을 *절대* 트리거하지 않는다. 운영자가 본 함수의 결과를 보고 close + open 페어를 *수동*으로 결정한다.

본 helper의 영업일 / 휴장일 / SQ 처리는 *근사*이며, 실제 영업일 캘린더는 후속 PR에서 별도 데이터 소스로 제공해야 한다 ([`futures_scope.md`](futures_scope.md) §6 / §12 후속 과제 #6).

## 6. MockFuturesBroker — 본 PR 시점 유일한 구현체

[`app.futures.mock.MockFuturesBroker`](../backend/app/futures/mock.py)가 본 ABC의 *유일한* 구현체다. in-memory 상태 머신으로 외부 네트워크 호출 0건 — 모든 prices / cash / positions가 ctor 또는 setter로 주입.

backwards compat: `app.futures.base.FuturesBrokerAdapter` ≡ `app.brokers.futures_base.FuturesBrokerAdapter` (동일 클래스 re-export). 기존 코드/테스트는 변경 0건.

LIVE 어댑터(KIS / Kiwoom / 해외선물)는 별도 PR — 본 PR에서 추가하지 *않는다*. [`live_activation_blockers.md`](live_activation_blockers.md) §3 + §3.1 9-step blocker 참조.

## 7. FuturesRiskManager와의 역할 분리

| 책임 | 모듈 |
|---|---|
| **margin / leverage / contracts / 청산위험 사전 검사** | [`FuturesRiskManager`](../backend/app/futures/risk.py)(`evaluate_virtual_order` / `evaluate_order`) |
| **broker REST 호출** | `FuturesBrokerAdapter`(LIVE 어댑터, 본 PR 미존재) / `MockFuturesBroker` |
| **주식 RiskManager 가드 적용** | ❌ 사용 안 함 — 선물은 `FuturesRiskManager`만 |
| **`PositionLimitRule` (#35) 적용** | ❌ 선물 미사용 (정적 grep 가드) |

`FuturesRiskManager`가 검사하는 가드 (`evaluate_virtual_order` 기준):

1. `leverage > 0` and `≤ policy.max_leverage`
2. 신규 contract 추가 후 총 보유 ≤ `max_contracts` (default 1)
3. `mark_price > 0`
4. `margin_available ≥ required initial_margin`
5. `margin_used + initial_margin ≤ max_margin_used` (default 1,000,000원)
6. `daily_realized_pnl > -max_daily_loss` (default 200,000원)

`FuturesRiskPolicy`는 주식 `RiskPolicy`와 별개 dataclass — 필드명/의미가 다르며 동일 instance로 취급할 수 없다 (`test_futures_risk_policy_is_separate_from_stock_policy` invariant lock).

호출 흐름 (현재 — 가상만):

```
caller(strategy / 운영자 / agent)
  → FuturesRiskManager.evaluate_virtual_order  (사전 검사)
  → MockFuturesBroker.place_order              (가상 체결)
  → futures_order_audit_log                    (영구화)
```

LIVE 활성화 후 (별도 PR):

```
caller
  → FuturesRiskManager.evaluate_order          (live 사전 검사 — 본 PR 시점 항상 REJECTED)
  → FuturesBrokerAdapter (LIVE).place_order    (실 broker, 본 PR 미존재)
  → futures_order_audit_log
```

## 8. 자동 롤오버 금지 정책

선물 contract 만기가 임박하면 (`should_rollover() == True`) 운영자에게 advisory 시그널을 surface하지만, **자동으로 close + open 주문을 실행하지 않는다**. 이유:

- 만기 직전 변동성이 큰 시간대(SQ 주)에 자동 실행은 슬리피지/체결품질 위험
- 롤오버 비용(2 × 수수료 + 슬리피지)이 자동 정책으로 누적되면 손실 제어 불가
- 운영자가 수동으로 timing을 결정해야 함

운영자가 결정하는 표준 흐름:

1. `is_contract_expiring_soon(spec)` → True인 contract 식별
2. **신규 진입 차단** (advisory) — 새 long/short 자제
3. 보유 포지션을 **수동으로 close**
4. 차월물 contract spec 확인 후 **수동으로 open**
5. 각 주문은 `FuturesRiskManager` + `PermissionGate`를 거치는 것이 원칙

본 정책은 [`futures_scope.md`](futures_scope.md) §4 *자동 강제청산 주문 금지*와 일관되며, 향후 strategy가 자동 롤오버를 시도해도 어댑터 단에서 차단되도록 LIVE 어댑터 PR에서 가드를 추가할 수 있다.

## 9. Live Futures 금지 — 본 PR 시점 invariant

| invariant | 보장 |
|---|---|
| `ENABLE_FUTURES_LIVE_TRADING=False` 기본값 유지 | `Settings` default + `FuturesRiskPolicy` default. `test_settings_default_keeps_futures_live_trading_disabled` |
| `FuturesRiskManager.evaluate_order` 항상 REJECTED | `app/futures/risk.py:66-75` — 본 PR에서 변경 없음 |
| LIVE 선물 어댑터 코드 0건 | `app/brokers/futures_base.py`는 contract만 정의, 구현 0건 |
| 본 모듈이 KIS / kis_client / mock_broker / executor / route_order import | 정적 grep 가드 (`test_module_does_not_import_live_broker_or_executor`) |
| 본 helper들이 broker 호출을 트리거 | 0건 — `test_helpers_return_only_advisory_no_broker_calls` |

LIVE 어댑터를 추가하려면 [`live_activation_blockers.md`](live_activation_blockers.md) §3.1 9-step 체크리스트를 모두 통과한 별도 옵트인 PR이 필요하다.

## 10. 테스트 매트릭스 (`tests/test_futures_broker_contract.py`)

29개 테스트 — 본 contract의 invariant를 영구 lock:

| 항목 | 테스트 |
|---|---|
| ABC 분리 | `test_futures_broker_adapter_does_not_inherit_from_stock_broker` 외 2개 |
| `FuturesOrder` 모델 | 4개 (base 필드 carry / audit 필드 / quantity 양수 / signal 범위) |
| `FuturesContractSpec` | 3개 (필드 / multiplier 양수 / leverage 양수) |
| `FuturesMarginSnapshot.from_balance` | 2개 |
| 만기 / 롤오버 helper | 7개 |
| 정적 import 가드 | 1개 |
| Backwards compat (MockFuturesBroker) | 2개 |
| RiskManager 분리 | 3개 (FuturesRiskPolicy 별개 / position_limits import 없음 / signature 차이) |
| ENABLE_FUTURES_LIVE_TRADING=False | 2개 (policy + Settings) |
| advisory invariant | 1개 (helper 반환값 plain bool/int) |
| ABC abstract methods | 1개 |

## 11. 변경 시 동기화

다음 변경은 본 문서 + 관련 문서를 함께 업데이트해야 한다:

- `FuturesBrokerAdapter` 메서드 시그니처 변경
- `FuturesContractSpec` / `FuturesOrder` / `FuturesMarginSnapshot` 필드 추가/제거
- 만기 / 롤오버 helper 임계 default 변경
- LIVE 어댑터 추가 — 별도 옵트인 PR + 본 문서 §6 / §9 갱신
- `FuturesRiskPolicy` 필드 변경

## 관련 문서

- [`futures_scope.md`](futures_scope.md) — 선물 1차 범위 + 국내/해외선물 비교 (#46)
- [`futures_margin_risk.md`](futures_margin_risk.md) — margin/leverage/liquidation rules + `/api/futures/margin/preview` (#48)
- [`futures_strategy_contract.md`](futures_strategy_contract.md) — `FuturesStrategyBase` + mock 전략 3종 (#49)
- [`futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 + invariant (#151)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 시 변경 매트릭스 (§3.1 9-step)
- [`risk_manager_contract.md`](risk_manager_contract.md) — 주식 RiskManager 표준 진입점 (#34)
- [`order_executor_contract.md`](order_executor_contract.md) — 주식 broker 단일 호출 진입점 (#40)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 (특히 §6)
