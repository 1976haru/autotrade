# Futures Promotion Policy — 체크리스트 #76

> **선물 실거래는 자동매매 전체에서 가장 마지막 단계다.**
> 본 문서는 선물 기능의 *단계별 승격 기준*만 정의한다. 본 PR로 선물 LIVE를
> 활성화하지 *않는다* — `ENABLE_FUTURES_LIVE_TRADING` 기본값 `false` 유지.

---

## 1. 결론

- 선물 실거래는 자동매매 전체에서 **가장 마지막** 단계다.
- 주식 MVP, Paper, Shadow, Live Manual, AI Assist가 안정화되기 *전*에는 선물
  실거래를 **금지**한다.
- 현재 선물 기능의 기본 범위는 **Simulation / Mock / Paper 준비** 단계
  ([`futures_scope.md`](futures_scope.md)).
- **`FUTURES_AI_EXECUTION`은 기본 BLOCKED** — 본 프로젝트는 선물 AI 자동
  실행을 허용하지 *않는다*. 주식 `LIVE_AI_EXECUTION`(#75)보다 *더 엄격*하다.

핵심 invariant (코드 단에 이미 강제):
- `ENABLE_FUTURES_LIVE_TRADING` default `false`.
- `FuturesRiskManager.evaluate_order` 가 LIVE 분기에서 항상 REJECTED
  (`"live futures evaluation not implemented yet"`).
- `KisBrokerAdapter` 가 futures live order endpoint를 구현하지 *않음*.
- `MODE_CAPABILITIES` 에 선물 LIVE 모드 자체가 없음.
- AI Execution Activation Gate(#75)의 `futures_allowed=False` 불변 — 본
  게이트로 선물 AI Execution을 *영구* 허용하지 않는다.

---

## 2. 왜 선물은 별도 Gate가 필요한가

선물은 주식보다 위험 한 등급 위:

| 위험 요인 | 주식 | 선물 |
|---|---|---|
| 레버리지 | 1배 (현금) | 5~20배 이상 |
| 증거금 / 유지증거금 | — | 위반 시 마진콜 / 강제청산 |
| 강제청산 (Forced liquidation) | — | broker가 자동 청산 가능 |
| 야간 변동성 | 시간 외 거래 제한 | 24시간 또는 야간 세션 |
| 만기 / 롤오버 | — | 근월 → 차월 전환 필요 |
| 호가단위 / 틱가치 | 균일 | 종목별 차이 |
| 작은 가격 변동 → 큰 손익 | 비교적 작음 | 레버리지 곱해서 증폭 |
| AI 판단 오류 시 손실 확대 | 한정적 | 마진 전체 손실 + 추가 추징 |

→ 주식과 *별개 게이트*가 필요하며, 본 게이트는 **주식 AI Assist까지 안정화된
이후**에만 시작할 수 있다.

---

## 3. 선물 승격 단계 (7단계)

```text
1. FUTURES_DISABLED              (default, 본 PR 시점)
2. FUTURES_SIMULATION            (MockFuturesBroker only)
3. FUTURES_SHADOW                (실 시세 read-only, would-have 기록)
4. FUTURES_PAPER                 (모의투자 or mock paper)
5. FUTURES_MANUAL_APPROVAL       (초소액 실거래 + 사람 승인)
6. FUTURES_AI_ASSIST             (AI 후보 + 사람 승인)
7. FUTURES_AI_EXECUTION_BLOCKED  (영구 BLOCKED — 본 프로젝트에서 미허용)
```

### 단계별 매트릭스

| 단계 | 실 주문 | broker | 최소 기간 | 신호 / 주문 수 | 손실한도 | 증거금 / 레버리지 | 사람 승인 | AI 권한 | 다음 단계 조건 |
|---|---|---|---|---|---|---|---|---|---|
| 1. DISABLED | ❌ | — | — | — | — | — | — | — | 운영자 explicit opt-in + futures_scope 확정 |
| 2. SIMULATION | ❌ | `MockFuturesBroker` | 무관 | ≥500 case | 시뮬레이션 손실 추적 | leverage_max enforce | — | 분석/추천 | 4 단계 PASS 조건 |
| 3. SHADOW | ❌ | mock + 실 시세 read-only | ≥**4주** | ≥**100 signal** | would-have 손실 추적 | leverage 시뮬 | — | 분석/추천 | 5 단계 PASS 조건 |
| 4. PAPER | ❌ (모의) | mock paper or KIS 선물 모의 | ≥**4~8주** | ≥**100~200건** | `max_daily_futures_loss` 위반 0회 | `max_contracts` ≤2, `max_margin_used` 보수적 | — | 분석/추천 | 6 단계 PASS 조건 |
| 5. MANUAL | ✅ 초소액 | 실 선물 broker | ≥**1~2개월** | 모든 주문 사람 승인 | 일일 손실 극소액 | `max_contracts=1`, leverage 최소 | **필수** | 분석/추천 | 7 단계 PASS 조건 |
| 6. AI_ASSIST | ✅ 초소액 | 실 선물 broker | ≥**1~2개월** | ≥100 AI proposal | 보수적 한도 | `max_contracts=1` | **필수** (모든 AI 제안) | 후보 제안만 | 7 단계는 **BLOCKED** |
| 7. AI_EXEC | 🛑 **BLOCKED** | — | — | — | — | — | — | — | **영구 BLOCKED** |

---

## 4. FUTURES_SIMULATION 기준 (단계 2)

**현재 위치 = 1차 범위**.

요구사항:
- `MockFuturesBroker` 만 사용 — 실 broker 호출 0건.
- `FuturesSimulationEngine` 가상 산식만 사용.
- 시뮬레이션 case ≥ **500개**.
- forced liquidation scenario 테스트 (자동 청산 *주문* 트리거 0건).
- margin 부족 주문 rejection 테스트.
- leverage 초과 rejection 테스트.

**PASS 조건**:
- ✅ 시뮬레이션 stress 테스트 통과.
- ✅ liquidation risk 계산 정상 (`LiquidationRiskRule` #48).
- ✅ `FuturesOrderAuditLog` 누락 0건.
- ✅ margin rule 위반 시나리오 모두 BLOCK 확인.

자세한 산식: [`futures_simulation_report.md`](futures_simulation_report.md),
[`futures_margin_risk.md`](futures_margin_risk.md).

---

## 5. FUTURES_SHADOW 기준 (단계 3)

요구사항:
- 실제 선물 *주문 발신 0건*.
- 실 시세(또는 mock) 기반 신호만 기록.
- 운영 기간 ≥ **4주**.
- 누적 futures signal ≥ **100건**.
- `actual_broker_order_sent=False` 불변 (주식 #43 패턴 차용).
- margin risk report 생성 — 만약 실제 주문 보냈다면 강제청산 위험이 얼마였을지
  계산만.
- shadow 결과와 실제 체결 가능성을 *명확히* 구분 (would-have 기록).

**PASS 조건**:
- ✅ signal_count ≥ 100
- ✅ margin risk warning 분석 완료
- ✅ forced liquidation risk warning 정상 (자동 청산 *주문* 발신 0건 확인)
- ✅ audit 누락 0건
- ✅ stale data 기반 신호 후보 0건 (freshness 가드 통과)

---

## 6. FUTURES_PAPER 기준 (단계 4)

요구사항:
- 실 선물 실거래 *아님* — 모의투자 환경 또는 mock paper.
- 운영 기간 ≥ **4~8주**.
- 누적 paper 주문 ≥ **100~200건**.
- `max_contracts` 제한 적용 (≤ 2계약).
- `max_margin_used` 제한 적용 (보수적).
- `max_leverage` 제한 적용 (계약별 leverage_max의 50% 이하 권장).
- `max_daily_futures_loss` 위반 0회.
- margin call risk 0회 (또는 통제 가능 수준).
- forced liquidation 0회.
- 주문 거부 사유 audit 기록.
- FillPolling 정합성 확인.

**PASS 조건**:
- ✅ expectancy > 0 (선물 단위 손익으로 계산)
- ✅ PF ≥ 1.2
- ✅ MDD 한도 (운영 자본의 15% 이내)
- ✅ Monte Carlo worst 5% MDD ≤ 30%
- ✅ risk_of_ruin ≤ 5%
- ✅ MarginRule / LeverageLimitRule / LiquidationRiskRule (#48) 위반 0회
- ✅ FuturesOrderAuditLog 누락 0건

---

## 7. FUTURES_MANUAL_APPROVAL 기준 (단계 5)

요구사항:
- **초소액 / 최소 계약**:
  - `max_contracts = 1`
  - `max_leverage` 최소화 (계약별 leverage_max의 30% 이하)
  - 일일 손실한도 극소액 (운용 자본의 0.5% 이하 권장)
- **모든 주문 사람 승인** — `PermissionGate` 큐 경유 (주식 #41 패턴).
- 자동 주문 절대 금지.
- AI는 *분석/추천만* 가능.
- **Overnight position 금지** 또는 별도 승인.
- **만기 / 롤오버 자동 처리 금지** — 운영자가 수동으로 청산/롤오버.

**PASS 조건**:
- ✅ 최소 운영 기간 1~2개월
- ✅ 시스템 오류 0건
- ✅ 손실한도 위반 0건
- ✅ Manual approval audit 누락 0건
- ✅ Emergency stop drill 통과 (3-Level Kill Switch #37)
- ✅ 운영자 explicit opt-in (별도 PR / 운영 노트)

---

## 8. FUTURES_AI_ASSIST 기준 (단계 6)

요구사항:
- AI는 **후보만 제안** — 실행 X.
- 사람 승인 필수.
- AI suggestion 성과 추적 (주식 AI Assist Gate #74 패턴 차용).
- **RiskAuditor**가 최우선 veto 권한.
- `FuturesRiskManager` 통과 필수.
- `MarginRule` / `LeverageLimitRule` / `LiquidationRiskRule` (#48) 모두 통과.

**PASS 조건**:
- ✅ AI proposal count ≥ 100
- ✅ approved expectancy > 0
- ✅ risk rejection 사유 분석 완료
- ✅ AI overconfidence 낮음 (confidence calibration 적정)
- ✅ forced liquidation risk 0건
- ✅ 사람 승인 후에도 broker 호출 전 RiskManager **재검증** 필수 (#070)

---

## 9. FUTURES_AI_EXECUTION 정책 (단계 7) — 영구 BLOCKED

**기본 BLOCKED.** 현재 프로젝트에서는 선물 AI 자동 실행을 허용하지 *않는다*.

이유:
- 주식 AI Execution(#75)보다 위험 한 등급 더 높음 (레버리지 + 강제청산 + 24h + 만기).
- AI 판단 오류 → 자동 마진콜 → 자동 강제청산 → 손실 확대.
- 본 프로젝트의 1차 범위는 안전 우선 — 선물 AI 자동매매는 *영구* 미허용.

코드 단 강제:
- `AIExecutionActivationGateResult.futures_allowed=False` 불변 (#75) — True
  생성 시 ValueError.
- `GET /api/governance/ai-execution-gate/policy` 가 항상 `"futures_allowed": false` 반환.
- 입력 `futures_target=True` 또는 `enable_futures_live_trading=True` → 즉시 BLOCKED.

만일 미래에 선물 AI 자동매매가 *어떤 형태로든* 검토된다면:
1. 본 문서를 폐기하고 별도 정책 문서 작성.
2. 별도 게이트 (`FuturesAIExecutionGate`) 새로 구현.
3. 별도 9단계 blocker 통과 ([`live_activation_blockers.md`](live_activation_blockers.md) §3.1).
4. 사용자 명시 승인 별도 PR.

본 PR은 위 조건을 *허용하지 않으며*, 현재 시점에서 항상 `BLOCKED`.

---

## 10. 선물 손실한도 / 증거금 / 청산 정책

`FuturesRiskPolicy` ([`backend/app/futures/risk.py`](../backend/app/futures/risk.py))
+ `MarginRule` / `LeverageLimitRule` / `LiquidationRiskRule`
([`futures_margin_risk.md`](futures_margin_risk.md)) 정책:

| 항목 | SIMULATION | SHADOW | PAPER | MANUAL | AI_ASSIST |
|---|---|---|---|---|---|
| `max_contracts` | 5 (test) | 시뮬만 | 2 | **1** | **1** |
| `max_leverage` | contract.leverage_max | 시뮬만 | 50% of leverage_max | 30% | 30% |
| `max_margin_used` | 보수적 | 시뮬만 | 운용 자본의 30% | 운용 자본의 10% | 운용 자본의 10% |
| `max_daily_futures_loss` | 시뮬 | 시뮬 | 운용 자본의 2% | 운용 자본의 0.5% | 운용 자본의 0.5% |
| 청산 거리 임계 (LiquidationRiskRule) | ≤3% BLOCK, 3~7% WARN | 동일 | 동일 | 동일 | 동일 + WARN 시 추가 사람 검토 |

기본값은 `FuturesRiskPolicy()` dataclass default — 본 PR 시점에서 모든 값은
*가상 시뮬레이션*용으로 설정되어 있으며, 실제 paper / manual 단계 진입 시
운영자가 *더 보수적*으로 override.

---

## 11. 만기 / 롤오버 정책

| 단계 | 자동 롤오버 | 만기 5일 이내 신규 진입 | 만기일 AI 자동매매 |
|---|---|---|---|
| SIMULATION | advisory plan만 (#49) | watch only 강등 | (해당 없음) |
| SHADOW | advisory plan만 | watch only 강등 | (해당 없음) |
| PAPER | advisory plan만 | 차월물 우선 권장 | (해당 없음) |
| MANUAL | **금지** — 운영자 수동 청산/롤오버 | 사람 승인 필수 | (해당 없음) |
| AI_ASSIST | **금지** — 운영자 수동 | 사람 승인 필수 | (해당 없음) |
| AI_EXEC | (영구 BLOCKED) | (영구 BLOCKED) | **영구 금지** |

만기일 근처 AI 자동매매는 *어떤 단계에서도 금지*. `FuturesRolloverPlan` 은
advisory 객체일 뿐 broker 호출 트리거가 *아니다* (#49 정적 grep 가드).

자세한 contract: [`futures_strategy_contract.md`](futures_strategy_contract.md).

---

## 12. UI / 운영자 가이드

- Futures 탭은 `frontend/src/config/features.js` 의 `VITE_ENABLE_FUTURES_TAB`
  (default **false**) 로만 노출 (#50). PC TopNav 에서만, 모바일 BottomNav는
  flag=true여도 직접 노출 X (사용자 혼동 방지).
- Futures UI에는 "활성화" / "주문 실행 시작" 라벨 활성 버튼 0개 — 안전 banner
  / 6 row safety matrix / disabled order area 만.
- 자세한 UI 정책: [`futures_ui.md`](futures_ui.md).

---

## 13. 금지 사항

- 🚫 주식 MVP 완료 전 선물 실거래 금지.
- 🚫 선물 live order 임의 활성화 금지 (`ENABLE_FUTURES_LIVE_TRADING=true`
  임의 전환 금지).
- 🚫 AI 선물 자동매매 금지 — 영구 BLOCKED.
- 🚫 자동 강제청산 *주문* 생성 금지 — Rule들은 위험 *계산* 전용 (#48).
- 🚫 실제 broker live futures endpoint 호출 금지 (`FuturesRiskManager.evaluate_order`
  LIVE 분기 항상 REJECTED).
- 🚫 API Key / Secret 하드코딩 금지 (CLAUDE.md 절대 원칙 4).
- 🚫 만기일 근처 AI 자동매매 금지.

---

## 14. 다음 단계 체크리스트

본 PR 이후 단계별로 진행:

- [x] **#46 futures scope 확정** ([`futures_scope.md`](futures_scope.md))
- [x] **#47 futures broker adapter contract** ([`futures_broker_contract.md`](futures_broker_contract.md))
- [x] **#48 futures margin risk** ([`futures_margin_risk.md`](futures_margin_risk.md))
- [x] **#49 futures strategy base** ([`futures_strategy_contract.md`](futures_strategy_contract.md))
- [x] **#50 futures UI gated** ([`futures_ui.md`](futures_ui.md))
- [x] **#76 futures promotion policy** (본 문서)
- [ ] **futures simulation stress** — 500+ case stress test 별도 PR
- [ ] **futures paper gate** — `FuturesPaperGate` 평가기 별도 PR
- [ ] **futures manual gate** — `FuturesManualGate` 평가기 별도 PR
- [ ] **(영구 BLOCKED) futures AI execution** — 본 프로젝트 미허용

각 후속 PR은 운영자 explicit opt-in + 사용자 명시 승인 필요.

---

## 15. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙 (특히 6번: 선물은 별도 어댑터)
- [`docs/futures_scope.md`](futures_scope.md) — #46 1차 범위 + 국내/해외 비교
- [`docs/futures_broker_contract.md`](futures_broker_contract.md) — #47 broker adapter
- [`docs/futures_margin_risk.md`](futures_margin_risk.md) — #48 Margin / Leverage / Liquidation Rule
- [`docs/futures_strategy_contract.md`](futures_strategy_contract.md) — #49 strategy base
- [`docs/futures_ui.md`](futures_ui.md) — #50 UI gated + disabled order area
- [`docs/futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식
- [`docs/live_activation_blockers.md`](live_activation_blockers.md) — §3.1 선물 9단계 blocker
- [`docs/promotion_policy.md`](promotion_policy.md) — 주식 단계별 승격
- [`docs/ai_execution_gate.md`](ai_execution_gate.md) — #75 (`futures_allowed=False` 불변)
- [`docs/ai_assist_gate.md`](ai_assist_gate.md) — #74 (주식 AI Assist)
- [`docs/live_manual_gate.md`](live_manual_gate.md) — #73 (주식 Live Manual)
- [`docs/paper_gate_policy.md`](paper_gate_policy.md) — #72 (주식 Paper)
- [`docs/risk_manager_contract.md`](risk_manager_contract.md) — #34 단일 진입점
- [`docs/order_executor_contract.md`](order_executor_contract.md) — #40 broker 호출 유일 지점
