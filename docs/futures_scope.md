# Futures Scope (#46)

본 문서는 본 프로젝트의 **선물 기능 범위와 금지 범위**를 정의한다. CLAUDE.md 절대 원칙 6 + [`futures_simulation_report.md`](futures_simulation_report.md) + [`live_activation_blockers.md`](live_activation_blockers.md) + [`promotion_policy.md`](promotion_policy.md) 위에서, **국내선물/옵션 vs 해외선물** 중 1차 도입 후보를 비교하고 1차 범위를 *Simulation Only*로 고정한다.

## 1. 결론

- 현재 프로젝트의 선물 기능은 **실거래가 아니다**.
- 1차 범위는 [`MockFuturesBroker`](../backend/app/futures/mock.py) + [`FuturesSimulationEngine`](../backend/app/futures/simulation.py) 기반 **가상 시뮬레이션**이다.
- 실제 선물 실거래는 주식 MVP, Shadow(#43), Paper, Manual Approval(#41) 검증 이후 **별도 Phase**로 분리한다.
- `ENABLE_FUTURES_LIVE_TRADING`은 **기본 비활성화** 유지 — 본 PR에서 변경 0건.
- AI 자동매매는 선물에서 **더 강한 권한 게이트**를 필요로 한다 (#39 + #45 위에 추가 futures-specific gate).

## 2. 왜 선물은 별도 Phase인가

선물·옵션은 주식과 본질적으로 다른 위험 프로파일을 갖는다:

| 위험 요소 | 주식 | 선물·옵션 |
|---|---|---|
| 레버리지 | 1배 (현금 매수) | KOSPI200 선물 ~5–10배, 해외선물 더 큼 |
| 증거금 | 없음 (현금 결제) | initial / maintenance margin, margin call |
| 강제청산 | 없음 | 가능 — 손실이 증거금을 초과하면 broker가 강제 청산 |
| 손실 한계 | 매수가까지 (-100%) | **예수금 초과 손실 가능** (특히 short 무한 손실) |
| 거래시간 | 09:00–15:30 KST | 야간 / 글로벌 시간대 (해외선물은 24시간 근접) |
| 호가단위 / 틱가치 | 주식 호가 단위표 | 상품별 상이 (KOSPI200: 0.05pt = 12,500원) |
| 변동성 | 일반 종목 ±15% 사이드카 | 급격한 변동성 + 롤오버 / 만기 영향 |
| AI 판단 오류 영향 | 부분 손실 | **계좌 전체 손실 + 추가 채무** 가능 |

이 차이로 인해, 주식 MVP에서 검증된 RiskManager / PermissionGate / OrderExecutor / OrderGuard / AIPermissionGate / AIExecutionGate 위에 **futures-specific 추가 게이트**가 필요하며, 그 게이트가 충분히 검증되기 전까지 실거래는 금지한다.

## 3. 국내 선물/옵션 vs 해외선물 비교표

| 항목 | 국내 선물/옵션 | 해외선물 |
|---|---|---|
| **대상 시장** | KOSPI200 선물·옵션, 미니/마이크로 KOSPI200, 개별주식선물 | CME (지수 선물 ES/NQ/MES/MNQ, 원자재 CL/GC, FX), Eurex, ICE 등 |
| **거래시간** | KOSPI200 선물 정규 09:00–15:45 KST + 야간 18:00–익일 05:00 KST | 거의 24시간 (CME Globex: 일요일 23:00–금요일 22:00 KST 근접) |
| **증거금 구조** | KRX 증거금 체계 (initial / maintenance), 일중 변동 시 추가증거금 | CME SPAN 또는 broker별 day/overnight margin (overnight이 더 큼) |
| **레버리지** | KOSPI200 선물 ~5–10배 | 상품별 상이, 마이크로 ES ~25–50배 가능 |
| **호가단위 / tick value** | KOSPI200: 0.05pt × 250,000원 = **12,500원** | ES: 0.25pt × $50 = **$12.5** (≈ 16,000원) |
| **강제청산 위험** | 높음 (margin call 후 broker 청산) | 매우 높음 (24시간 갭 + 환율 갭) |
| **야간 리스크** | 야간 선물 + KOSPI200 차익 거래 | 미국 지표/실적 발표 시간 변동 |
| **환율 리스크** | 없음 (KRW 기준) | **있음** (USD 표시 + 환전 비용) |
| **API 접근성** | KIS 국내선물옵션 API 카테고리 존재, Kiwoom OpenAPI+ / REST 일부 지원 — 상세 조사 필요 | KIS 해외선물옵션 API 카테고리 존재 — 별도 계약 + 수수료 |
| **모의투자 가능성** | KIS 모의투자: 국내선물 지원 (별도 신청) | KIS 모의투자: 해외선물 지원 여부 별도 확인 필요 |
| **자동매매 난이도** | 중–높음 (만기/롤오버/SQ 처리) | 높음 (24시간 + 환율 + 다수 거래소) |
| **현재 프로젝트와의 적합성** | 주식 MVP 인프라(KIS adapter, KST 시장 시간 가드, KRW 단가)와 **상대적으로 호환** | 별도 환율 처리, USD 단가, 24시간 시간대 처리 — 주식 인프라 큰 폭 확장 필요 |
| **1차 도입 추천** | **문서 조사 + 모의환경만** (실거래 X) | **후순위** (주식 MVP + 국내 선물 모의환경 검증 후 재평가) |

> **note**: 위 표는 일반적인 시장 정보를 기반으로 한 *비교 가이드*이며, 실거래 진입 전에는 KIS / 키움 공식 문서 + 거래소 공시로 모든 수치를 재검증해야 한다.

## 4. 1차 선물 범위

### 포함 (In-Scope)

| 항목 | 위치 |
|---|---|
| `FuturesMockBroker` (in-memory 가상 broker) | [`app/futures/mock.py`](../backend/app/futures/mock.py) |
| `FuturesSimulationEngine` 산식 (margin / liquidation / slippage / fee / PnL) | [`app/futures/simulation.py`](../backend/app/futures/simulation.py) |
| `FuturesRiskManager.evaluate_virtual_order` (가상 경로) | [`app/futures/risk.py`](../backend/app/futures/risk.py) |
| `futures_order_audit_log` (가상 주문 audit) | `app/db/models.py::FuturesOrderAuditLog` |
| 가상 long / short 포지션 | `FuturesPosition` |
| 가상 증거금 (initial / maintenance) | `compute_initial_margin` |
| 가상 레버리지 | `MockFuturesBroker.set_leverage` |
| 가상 청산가격 (`liquidation_price`) | `compute_liquidation_price` |
| 가상 강제청산 시뮬레이션 | `should_force_liquidate` + `force_liquidate_if_needed` |
| Futures 탭 UI (가상 주문 표시 / 강제청산 토글) | `frontend/src/components/tabs/Futures.jsx`, `FuturesOrderAuditCard.jsx` |
| Futures risk report (가상) | `futures_simulation_report.md` |

### 제외 (Out-of-Scope) — 본 단계 영구 금지

| 항목 | 사유 |
|---|---|
| 실제 선물 주문 (`KisBrokerAdapter` 또는 별도 live adapter의 `place_order`) | `enable_futures_live_trading=False` + adapter 미구현 |
| 실제 선물 주문 취소 (`cancel_order`) | 동일 |
| 실제 선물 계좌 연결 (KIS 선물옵션 API 실키) | API key 변경 절대 금지 (절대 원칙 5) |
| 실제 해외선물 주문 | 후순위 — 주식 MVP + 국내 선물 모의 검증 후 재평가 |
| 실제 옵션 매매 | 본 PR 범위 외 |
| AI 선물 자동매매 | `ENABLE_AI_EXECUTION=False` + 선물은 추가 게이트 |
| 자동 강제청산 *주문* | broker로 청산 주문이 나가는 경로 0건 |
| 실계좌 증거금 기반 주문 | 가상 broker 자체 증거금 트래킹만 |

## 5. 대상 시장 선택 정책

- **현재**: 특정 실제 시장을 선택하지 않고 **Simulation Only**.
- **실제 도입 시**: 국내선물/옵션과 해외선물 중 **하나만** 선택. 동시 도입 금지.
- **1차 실제 연동 후보 선정 기준**:
  1. KIS / Kiwoom API 모의투자 접근성
  2. 공식 문서 품질 (request/response schema, error code)
  3. 증거금 계산이 broker API + 자체 산식으로 reconcilable
  4. 만기 / 롤오버 / SQ 처리 코드 부담
  5. 24시간 거래 vs KST 영업일 정렬 부담 (해외선물이 큼)
- **잠정 권고 순위**: 국내선물(KOSPI200) 모의환경 우선 → 해외선물은 후순위

## 6. 거래시간 정책

- 선물은 거래시간이 주식과 다르다. 현재 [`risk_manager.py`](../backend/app/risk/risk_manager.py)의 `_MARKET_OPEN_KST` / `_MARKET_CLOSE_KST` (09:00–15:30)는 **주식 전용**이다.
- 실제 선물 도입 시 별도 처리 필요:
  - **장중 / 야간 / 휴장일** — 선물 영업일 캘린더
  - **만기일** — 결제일, SQ(Special Quotation) 처리
  - **롤오버** — 근월물 → 차월물 자동 전환 정책
- 현재 simulation은 단순 시간 모델만 사용 — 만기 / 롤오버 미처리.
- 실제 연동 전 **futures trading calendar** 데이터 소스 확정 필요.

## 7. 증거금 / 레버리지 / 청산 (가상 모델)

현재 [`app/futures/simulation.py`](../backend/app/futures/simulation.py)에 다음 산식이 구현되어 있다 — 모두 **가상**:

| 항목 | 산식 / 정의 |
|---|---|
| `notional` | `mark_price × quantity` |
| `initial_margin` | `ceil(notional / leverage)` |
| `maintenance_margin` | `notional × maintenance_margin_pct / 100` |
| `leverage` | 운영자가 `set_leverage()`로 설정, `policy.max_leverage` (default 10x) 이하 |
| `liquidation_price` | LONG: `entry × (1 − loss_buffer)`, SHORT: `entry × (1 + loss_buffer)` where `loss_buffer = max(0, 1/leverage − maintenance_margin_pct/100)` |
| `margin_call` | (가상) `mark_price`가 liquidation 근접 — UI 경고만 |
| `forced_liquidation_risk` | LONG: `mark ≤ liquidation`, SHORT: `mark ≥ liquidation` |
| `daily_loss_limit` | `FuturesRiskPolicy.max_daily_loss` (default 200,000원) |
| `max_contracts` | `FuturesRiskPolicy.max_contracts` (default 1) |
| `max_margin_used` | `FuturesRiskPolicy.max_margin_used` (default 1,000,000원) |

실거래 도입 시: broker API에서 받는 증거금/청산가와 본 가상 산식의 차이를 reconciliation으로 측정해야 한다.

## 8. AI 권한 정책 (선물)

선물에서 AI 자동매매는 주식보다 더 엄격해야 한다:

- **기본**: AI 추천만 가능 (주식의 `LIVE_AI_ASSIST`(#44) 패턴과 동일).
- **`VIRTUAL_AI_EXECUTION`** (가상 환경): 현재 모드는 *주식* 가상 자동 실행이 정의됨 — 선물 가상 자동 실행을 별도로 추가하려면 **별도 risk gate** + futures audit 계약 필요.
- **`FUTURES_LIVE` + `LIVE_AI_EXECUTION` 조합**: 최종 단계에서도 **별도 opt-in PR**이 필요하며, 다음을 모두 통과해야 한다:
  - `ENABLE_FUTURES_LIVE_TRADING=true` (env)
  - `ENABLE_AI_EXECUTION=true` (env)
  - `ENABLE_LIVE_TRADING=true` (env)
  - 주식 LIVE_AI_EXECUTION 무사고 운영 1개월 이상
  - 선물 모의환경 무사고 운영 (수치는 별도 PR 결정)
  - `AIExecutionGate`(#45)에 **futures-specific 보수적 한도** 추가 (max_notional 더 작게, max_contracts=1, leverage 제한, 야간 차단 등)
- **AI는 선물 주문 권한을 직접 갖지 않는다**: `route_order` / `OrderExecutor` 단일 진입점 invariant가 선물에도 동일 적용 — 미래 `FuturesOrderRouter`가 추가되더라도 broker 호출은 단일 함수에서만.

## 9. 안전 invariant

본 PR 시점에 *현재 코드에서 보장되는* invariant — 본 작업이 어느 하나라도 약화하지 않았다:

| invariant | 보장 |
|---|---|
| `ENABLE_FUTURES_LIVE_TRADING=False` 기본값 | `app/core/config.py::Settings` default |
| `MockFuturesBroker`만 사용 | `FuturesBrokerAdapter`의 *유일한* 구현체 |
| `FuturesRiskManager.evaluate_order` 항상 REJECTED | `app/futures/risk.py:66-75` ("ENABLE_FUTURES_LIVE_TRADING is disabled" + "live futures evaluation not implemented yet") |
| 실제 futures adapter 없음 | `app/futures/`에 KIS / Kiwoom adapter 0개 |
| broker live endpoint 호출 0건 | `MockFuturesBroker`가 외부 API 호출 0건 (in-memory only) |
| real futures order / cancel 0건 | 모든 선물 주문은 `MockFuturesBroker`에서 종결 |

## 10. 실전 전 필수 조건 (Live Activation Checklist)

선물 LIVE 활성화 시 운영자가 통과해야 할 단계 — **AND** (모두 충족):

1. **주식 MVP 완료** — `LIVE_MANUAL_APPROVAL` + `LIVE_AI_ASSIST` 기준 무사고 운영 (`promotion_policy.md` 단계별)
2. **KIS / Kiwoom Paper 검증** — 주식 모의투자 4주+ 무중단
3. **Shadow 최소 2~4주** — `LIVE_SHADOW`(#43) 종합 통계 + would-have 분석
4. **Manual Approval 최소 1개월** — `LIVE_MANUAL_APPROVAL`(#41) 무사고
5. **Futures simulation stress 통과** — 강제청산 / 만기 / 롤오버 시나리오 stress test
6. **증거금 계산 검증** — 가상 산식 vs broker reconciliation
7. **강제청산 시나리오 검증** — 가상 forced liquidation flow 운영자 학습
8. **최소 계약 수 수동승인 테스트** — `max_contracts=1`로 시작, 모든 주문 사람 승인
9. **사용자 별도 opt-in** — env flag + 운영자 명시 승인 + PR review

위 조건이 하나라도 미충족이면 LIVE 활성화 금지.

## 11. 공식 링크

- KIS Developers Portal: <https://apiportal.koreainvestment.com/>
  - 국내선물옵션 API 카테고리 (모의투자 신청 별도)
  - 해외선물옵션 API 카테고리 (별도 계약 + 데이터 fee)
- Kiwoom REST API: <https://openapi.kiwoom.com/>
- Kiwoom OpenAPI+ / 모의투자 안내: <https://www.kiwoom.com/h/common/bbs/VBbsBoardBWFOZView>
- KRX 파생상품 시장 (영업일 / 호가단위 / 증거금 공시): <https://www.krx.co.kr/>
- CME Group (해외선물 명세 / 거래시간): <https://www.cmegroup.com/>

## 12. 후속 과제 (별도 PR)

본 PR의 *문서 정의*가 완료된 후, 단계적으로 진행할 수 있는 작업들:

1. **KIS 국내선물옵션 API 상세 조사** — endpoint / tr_id / margin schema / 만기 처리
2. **KIS 해외선물옵션 API 상세 조사** — 24시간 거래 / 환율 / 데이터 fee
3. **Kiwoom REST 선물/옵션 지원 범위 확인** — 국내 선물 가능 시 KIS와 비교
4. **Kiwoom OpenAPI+ COM/OCX 호환** — Windows-only 의존성 부담 평가 (현재 KIS는 REST만)
5. **`FuturesAIExecutionGate`** — `AIExecutionGate`(#45)의 futures-specific 추가 한도 (max_notional 더 작게, leverage cap, 야간 차단)
6. **Futures trading calendar** — 영업일 / 만기일 / SQ 데이터 소스
7. **롤오버 정책** — 근월물 → 차월물 자동 전환 시뮬 (가상 환경 먼저)
8. **Futures Shadow Mode** — 실 시세 read-only 검증 단계 (LIVE_FUTURES_SHADOW 정의)
9. **선물 audit reconciliation report** — 가상 broker 인식 vs DB audit 산출 drift

본 PR은 위 9개 후속 과제의 **기준 문서**를 제공한다.

## 관련 문서

- [`futures_broker_contract.md`](futures_broker_contract.md) — `FuturesBrokerAdapter` 공식 contract + `FuturesContractSpec` / `FuturesOrder` / 만기·롤오버 helper (#47)
- [`futures_margin_risk.md`](futures_margin_risk.md) — `FuturesMarginRule` / `LeverageLimitRule` / `LiquidationRiskRule` (3%/7% threshold) + `/api/futures/margin/preview` (#48)
- [`futures_strategy_contract.md`](futures_strategy_contract.md) — `FuturesStrategyBase` + 양방향 신호 + 계약 sizing + 롤오버 advisory + mock 전략 3종 (#49)
- [`futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 + invariant (#151, #169)
- [`live_activation_blockers.md`](live_activation_blockers.md) — LIVE 활성화 시 변경 매트릭스 (선물 §3 포함)
- [`promotion_policy.md`](promotion_policy.md) — 단계별 승격 기준
- [`risk_policy.md`](risk_policy.md) — RiskManager 평가 매트릭스 (주식)
- [`ai_permission_gate.md`](ai_permission_gate.md) — AI 권한 단계 (#39)
- [`ai_execution_policy.md`](ai_execution_policy.md) — AI 자동 실행 게이트 (#45)
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙 (특히 §6)
