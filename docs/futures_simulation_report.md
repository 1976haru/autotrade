# Futures Simulation Report (151, MUST)

CLAUDE.md 절대 원칙: **선물 실거래는 영구 비활성**. 본 문서는 가상 환경
(`FuturesMockBroker` + `FuturesSimulationEngine`)의 산식과 invariant를 정리한다.

## 모듈 구성

| 파일 | 역할 |
|---|---|
| [`backend/app/futures/types.py`](../backend/app/futures/types.py) | enum / Pydantic 모델 (Quote / Position / Balance / OrderRequest / OrderResult). 151에서 `FuturesPosition.liquidation_price` 추가. |
| [`backend/app/futures/simulation.py`](../backend/app/futures/simulation.py) | 순수 산식 — initial margin, liquidation price, slippage, fee, realized PnL, 강제청산 조건. |
| [`backend/app/futures/mock.py`](../backend/app/futures/mock.py) | `MockFuturesBroker` — in-memory 상태 머신. cash / positions / orders / prices를 자체 관리. |
| [`backend/app/futures/risk.py`](../backend/app/futures/risk.py) | `FuturesRiskManager`. live 경로(`evaluate_order`)는 항상 REJECTED. 가상 경로(`evaluate_virtual_order`)만 평가 후 APPROVE. |

## 산식

### Initial margin
```
initial_margin = ceil(notional / leverage)
```
- `notional = mark_price * quantity`
- `leverage`는 운영자가 `MockFuturesBroker.set_leverage()`로 결정. `policy.max_leverage`(기본 10x) 이하여야 한다.
- 정수 원으로 ceil — 0.5원이라도 부족하면 거부 권장.

### Liquidation price
```
loss_buffer_ratio = max(0, 1/leverage - maintenance_margin_pct/100)
LONG  liquidation = entry * (1 - loss_buffer_ratio)
SHORT liquidation = entry * (1 + loss_buffer_ratio)
```
예: `leverage=5, mm=10%` → `loss_buffer = 0.20 - 0.10 = 0.10`. LONG 진입가 1000 → liquidation 900.

### Force liquidation
- LONG: `mark_price ≤ liquidation_price` → 즉시 청산.
- SHORT: `mark_price ≥ liquidation_price` → 즉시 청산.
- `MockFuturesBroker.force_liquidate_if_needed(contract)`가 운영자/테스트가 호출하는 진입점.

### Slippage / fee / PnL
- Slippage: `delta = max(1, price * slippage_bps / 10000)`. LONG/BUY 위로, SHORT/SELL 아래로.
- Fee: `max(1, notional * fee_bps / 10000)`. 진입과 청산 양쪽에 부과 (왕복 2 × fee_bps).
- Realized PnL: LONG `(exit - entry) * qty`, SHORT 부호 반대.

## 파라미터 표 (FuturesSimulationParams)

| 항목 | 기본값 | 비고 |
|---|---:|---|
| `default_leverage` | 5.0 | KOSPI200 평균 |
| `max_leverage` | 10.0 | 운영자가 set_leverage로 이 값 초과 시 ValueError |
| `maintenance_margin_pct` | 10.0 | notional 대비 % |
| `fee_bps` | 2 | 0.02% (왕복 4bps) |
| `slippage_bps` | 5 | 0.05% |

## RiskPolicy 표 (FuturesRiskPolicy)

| 항목 | 기본값 |
|---|---:|
| `max_contracts` | 1 |
| `max_margin_used` | 1,000,000원 |
| `max_daily_loss` | 200,000원 |
| `max_leverage` | 10.0 |
| `enable_futures_live_trading` | **false (영구)** |

## 가드 매트릭스 — virtual evaluate

`FuturesRiskManager.evaluate_virtual_order`가 검사하는 invariant:

| 검사 | 거부 사유 |
|---|---|
| `leverage ≤ 0` 또는 > max_leverage | "leverage exceeds max_leverage" |
| 신규 contracts > max_contracts | "contracts exceeds max_contracts" |
| `mark_price ≤ 0` | "mark_price must be positive" |
| `margin_available < required initial_margin` | "margin_available < required" |
| `margin_used + initial_margin > max_margin_used` | "max_margin_used exceeded" |
| `daily_realized_pnl ≤ -max_daily_loss` | "daily futures loss limit reached" |

모두 통과해야 APPROVED. **단 한 검사라도 실패하면 REJECTED + reason 누적.**

## Live 가드 (변경 없음)

`FuturesRiskManager.evaluate_order`(live 경로):
- `enable_futures_live_trading=False` → 즉시 `REJECTED` ("ENABLE_FUTURES_LIVE_TRADING is disabled").
- `True`(운영자가 의도적으로 켰을 때조차) → 즉시 `REJECTED` ("live futures evaluation not implemented yet"). **151 PR은 live 평가 로직을 만들지 않는다.** 실제 KIS 선물 endpoint 연결은 별도 옵트인 PR.

## 테스트 (`backend/tests/test_futures_simulation.py`)

총 31 테스트:
- 산식 단위 테스트 12개 (margin / liquidation / slippage / fee / PnL).
- MockFuturesBroker lifecycle 11개 (LONG/SHORT 진입, 부분 청산, 강제청산, 잔고 부족, 레버리지 캡, 잔고 reflection, cancel 동작).
- FuturesRiskManager 가드 8개 (live 양 케이스 + virtual 6개 invariant).

`tests/test_futures_skeleton.py`도 갱신: 기존 stub 테스트(NotImplementedError) → virtual broker 동작 / live still REJECTED로 의도 변경.

## 169: 선물 audit 로그

`MockFuturesBroker(initial_cash, params, *, db=None, audit_mode="VIRTUAL_FUTURES")` —
optional `db` Session 주입 시 매 broker 호출 후 `FuturesOrderAuditLog` 행 추가.
미주입(default None)이면 in-memory `orders` dict만 — 기존 호출 패턴 유지.

기록되는 시나리오:
- 신규 진입 (long/short open)
- 동일 방향 추가 (add)
- 부분/전량 청산 (close)
- 강제청산 — `forced_liquidation=True` flag로 구분
- 잔고 부족 거부 — `executed=False, broker_status="REJECTED", reasons=["insufficient_cash"]`
- LIMIT not crossed — `executed=False, broker_status="RECEIVED", reasons=["limit_not_crossed"]`

스키마(`backend/app/db/models.py::FuturesOrderAuditLog`):
- 주식 OrderAuditLog와 분리 — `contract`(vs symbol), `leverage`, `liquidation_price`, `forced_liquidation` 등 선물 고유 필드.
- alembic 0013 마이그레이션. 인덱스: created_at / mode / contract / decision / forced_liquidation.

`audit_mode` 기본 `"VIRTUAL_FUTURES"`. 운영자가 LIVE 단계 도달 후 다른 모드 (예: `LIVE_FUTURES_SHADOW`) 명시 가능 — 별도 옵트인.

## 운영 invariant (단정문)

1. **선물 실거래 endpoint는 본 PR에서 호출되지 않는다.** `FuturesRiskManager.evaluate_order`는 어떤 경로로도 APPROVED를 반환하지 않는다.
2. **MockFuturesBroker는 외부 네트워크 호출 없음.** in-memory 상태 머신 — 모든 prices / cash / positions가 ctor 또는 setter로 주입.
3. **가상 환경에서도 가드 체인은 그대로.** `evaluate_virtual_order`가 실패하면 broker `place_order`를 호출해선 안 된다(caller 책임). 152의 `VirtualAiAgent`도 본 가드를 우회하지 않는다.
4. **`enable_futures_live_trading=True` 플래그는 본 PR에서 무력화** — flag만 켜도 live evaluate는 REJECTED. 실제 활성화는 별도 PR + 명시적 옵트인.

## 미구현 / 향후 follow-up

- 실거래 KIS 선물 adapter (별도 옵트인 PR — `docs/live_activation_blockers.md` 참조).
- 만기 자동 처리 — 본 PR은 expiry 검증 없이 contract 코드만 사용.
- Funding fee 시뮬레이션 — 현재는 stub (no-op).
- 선물 `Strategy` ABC — 주식 strategy와 분리된 별도 인터페이스 필요 시 별도 PR.

## 관련 문서

- [`CLAUDE.md`](../CLAUDE.md) 절대 원칙 6 — 선물 별도 모듈 분리.
- [`docs/risk_policy.md`](risk_policy.md) "선물 RiskPolicy" 섹션.
- [`docs/promotion_policy.md`](promotion_policy.md) — 선물 단계 (현 단계는 "Virtual"; 다음 단계는 별도 옵트인).
