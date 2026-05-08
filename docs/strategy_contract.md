# Strategy Contract (체크리스트 #28)

## 1. 목적

Strategy는 **주문을 실행하지 않는다** — 신호와 설명만 생성한다. 실제 주문은 `route_order` 단일 진입점이 RiskManager → PermissionGate → OrderExecutor 순서로 처리한다 (CLAUDE.md 절대 원칙 2).

본 PR은 `app/strategies/base.py`의 `Strategy(ABC)`를 깨지 않으면서, 운영자/Agent/audit이 **구조화된 신호 + 사이즈 힌트 + 청산 계획 + 사람이 읽는 설명**을 한 번에 받을 수 있도록 4개 인터페이스를 추가한다. 기존 `on_bar` 인터페이스는 그대로 — 신규 인터페이스는 모두 default impl이 있어 기존 concrete 전략 (sma_crossover / rsi_reversion / orb_vwap)은 수정 없이 새 contract를 만족한다.

## 2. 4개 인터페이스

| 메서드 | 입력 | 출력 | 의미 |
|---|---|---|---|
| `on_bar(bars)` | `list[Bar]` | `Signal` (BUY/SELL/HOLD) | **Legacy** — 기존 BacktestEngine / LiveStrategyEngine이 의존. 그대로 유지 |
| `generate_signal(context)` | `StrategyContext` | `StrategySignal` | 신규 — 구조화된 신호. default는 `on_bar` 호출 결과를 변환 |
| `calculate_size(signal, ...)` | `StrategySignal` + 옵션 | `SizingHint` | 권장 사이즈 힌트. **최종 수량은 RiskManager / PositionSizingAgent가 결정** |
| `exit_rule(signal, ...)` | `StrategySignal` + 옵션 | `ExitPlan` | 청산 계획 (TP/SL/시간 청산/무효화). 운영자/Agent가 본다 |
| `explain_signal(signal, ...)` | `StrategySignal` + 옵션 | `SignalExplanation` | 사람이 읽을 한 줄 + 사유 + indicator. audit `structured_reason` carry |
| `validate_context(context)` | `StrategyContext` | `ValidationResult` | 사전 점검 — 봉 부족 / regime 미일치 등 |

## 3. DTO

### `StrategySignal`
- `action`: `SignalAction` enum — `BUY / SELL / EXIT / WATCH / NO_SIGNAL`.
- `symbol`, `sizing_hint`, `exit_plan`, `explanation`.
- **`is_order_intent: bool = False` invariant** — Strategy가 반환하는 신호는 **주문 의도가 아니다**. 본 필드는 항상 False, 코드/테스트로 강제. 호출자가 True로 바꾸려면 별도 옵트인 PR 필요.

### `SizingHint`
- `quantity`, `position_size_pct`, `risk_pct`, `reduce_only`, `note`.

### `ExitPlan`
- `take_profit_pct`, `stop_loss_pct`, `time_exit_bars`, `invalidation`, `rule_summary`.

### `SignalExplanation`
- `summary` (한 줄), `reasons` (배열), `confidence` (0~100), `indicators` (자유 dict), `required_regime`.

### `StrategyContext`
- `bars`, `symbol`, `regime`, `watchlist`, `account_equity`, `extra`.

### `ValidationResult`
- `ok: bool`, `reasons: list[str]`.

## 4. 직접 주문 금지 invariant (코드/테스트)

다음을 코드/테스트로 강제한다:

| 검증 | 위치 |
|---|---|
| `app/strategies/base.py`에 `from app.brokers / risk / permission / execution / governance` import 0건 | 테스트 `test_strategy_module_does_not_import_broker_or_risk` |
| Strategy class 표면에 `buy / sell / place_order / submit_order / decide_order / execute` 류 메서드 없음 | 테스트 `test_strategy_class_has_no_order_decision_methods` |
| `StrategySignal.is_order_intent`는 모든 SignalAction에서 항상 False | 테스트 `test_strategy_signal_is_order_intent_invariant` |
| `StrategySignal.to_dict()`에 `side / order_type / limit_price / decision / broker_order_id / client_order_id` 필드 없음 | 테스트 `test_strategy_signal_dict_has_no_order_fields` |

## 5. 전략 추가 절차

1. `Strategy(ABC)` (또는 alias `StrategyBase`)를 상속.
2. metadata 작성: `entry / exit / invalidation / required_regime / risk_profile`.
3. `on_bar(bars)`를 구현 (legacy 호환).
4. 필요하면 `generate_signal / calculate_size / exit_rule / explain_signal` override (구조화된 신호 생성).
5. `app/strategies/concrete/__init__.py`의 registry에 등록 + contract metadata 강제 (#170 — `enforce_contract=True`).
6. 테스트 추가 (단일 strategy 테스트 + registry 테스트).
7. **broker / RiskManager / PermissionGate / OrderExecutor / `route_order` 직접 호출 금지** — 본 contract 의 핵심.

## 6. Agent와의 관계

- AI Agent (`app/ai/agents/*`)는 Strategy의 `StrategySignal` / `SignalExplanation`을 *참고*할 수 있다.
- Agent도 직접 주문하지 않는다 — Quality / Risk / Permission / OrderExecutor를 거쳐야 함.
- Agent가 만든 결정은 `agent_decision_log`에 영구화되며 `route_order(requested_by_ai=True)`를 통해야 실제 주문이 만들어진다.

흐름:
```
StrategySignal  +  AgentDecision
       \              /
        \            /
         v          v
        Quality / RiskManager / PermissionGate
                    |
                    v
              OrderExecutor
```

Strategy는 *왼쪽 윗단*에만 영향. 주문 *오른쪽 아래단*은 그대로 단일 경로.

## 7. Audit와의 관계

- `explain_signal(signal)` 결과는 `OrderAuditLog.structured_reason`(또는 그 후속 컬럼)으로 carry 가능 — 운영자/감사가 "왜 이 주문이 만들어졌는지" 추적.
- `confidence` / `indicators` 같은 메타는 `ai_decision_meta` (#152) 또는 `signal_strength / signal_confidence` (#139) 컬럼과 매핑.

## 8. 호환성 정책

- `Strategy` 이름 유지 — 기존 import 호환.
- `StrategyBase = Strategy` alias 추가 — 신규 호출자가 새 이름을 쓰고 싶을 때.
- `on_bar`는 `@abstractmethod` 그대로 — 모든 구현체가 반드시 정의.
- 신규 인터페이스는 default impl이 있어 기존 concrete 전략 수정 0건.
- `app/backtest/engine.py` / `app/strategies/live_engine.py` 변경 0건 — `on_bar`만 사용.
- `to_legacy_signal(StrategySignal) -> Signal` / `from_legacy_signal(Signal) -> StrategySignal` adapter — 두 인터페이스가 어디서든 변환 가능.

## 9. 안전 invariant (본 PR이 지키는 것)

- `app/strategies/base.py`는 broker / RiskManager / PermissionGate / OrderExecutor / governance import 0건.
- 기존 `Strategy.on_bar` 시그니처 그대로 — 모든 concrete 전략 / BacktestEngine / LiveStrategyEngine 회귀 0건.
- 신규 메서드는 default impl이 있어 추상 메서드 추가로 인한 기존 코드 깨짐 없음.
- `StrategySignal.is_order_intent`는 항상 False — invariant 테스트.
- `StrategySignal.to_dict()`에 주문 필드(side/quantity/order_type/decision) 없음 — 테스트.
- 외부 네트워크 호출 0건.
- `ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING` 변경 0건.
- API Key / Secret / 계좌번호 변경 0건. frontend 시크릿 노출 0건.

## 10. 후속 작업 (Backlog)

| 항목 | 트리거 |
|---|---|
| 기존 concrete 전략을 native `generate_signal` 구현으로 점진 전환 | 29번 전략 구현 PR (별도) |
| LiveStrategyEngine이 `generate_signal` 사용하도록 확장 | 별도 옵트인 PR |
| Agent → Strategy → RiskManager 흐름에서 `SignalExplanation`을 `OrderAuditLog`에 carry | LIVE 활성화 PR |
| `StrategySignal.confidence`를 RiskPolicy `min_ai_confidence` (#158)에 연결 | 별도 옵트인 PR |
| frontend StrategyRisk 탭에 `SignalExplanation` 표시 | UI 요청 시 |
| `validate_context`를 BacktestEngine pre-check에 wire | 별도 옵트인 PR |

## 관련 문서

- [`promotion_policy.md`](promotion_policy.md) — Strategy contract metadata (#131) 요건
- [`strategy_promotion_gate.md`](strategy_promotion_gate.md) — 코드 게이트 (#27)
- [`strategies.md`](strategies.md) — concrete 전략 목록
- [`risk_policy.md`](risk_policy.md), [`risk_guards_matrix.md`](risk_guards_matrix.md) — RiskManager (Strategy와 분리)
- [`agent_decision_schema.md`](agent_decision_schema.md) — AgentDecisionLog (Strategy와 분리)
