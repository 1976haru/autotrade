# LIVE Activation Blockers

**현재 시점에서 본 시스템은 *가상 환경 전용*이다.** 본 문서는 라이브 실거래
활성화 시 변경해야 할 정확한 코드 / 설정 / 절차를 매핑한다. 모든 항목에
**확인 필요** 태그가 붙어 있으며, 사용자가 명시적으로 옵트인하기 전까지는
이 변경들이 자동으로 일어나지 않는다.

## 0. 상위 절대 원칙

CLAUDE.md 절대 원칙 1–6은 라이브 활성화 시에도 그대로 유효해야 한다.
특히:
- 원칙 1: AI는 broker API 직접 호출 X.
- 원칙 2: 모든 주문이 RiskManager → PermissionGate → OrderExecutor.
- 원칙 5: API Key / 계좌번호는 frontend 미저장.

라이브 활성화가 위 원칙을 위반하는 형태라면 진행해선 안 된다.

## 1. 환경 플래그 (확인 필요)

| 변수 | 현재 기본 | LIVE 활성화 시 | 영향 |
|---|---|---|---|
| `ENABLE_LIVE_TRADING` | `false` | `true` | RiskPolicy live 가드 통과. KIS adapter routes_broker 분기 |
| `KIS_IS_PAPER` | `true` | `false` | KisClient host + tr_id가 prod로 전환 |
| `ENABLE_AI_EXECUTION` | `false` | `true`(선택) | LIVE_AI_EXECUTION 모드의 ai_can_execute 가드 통과 |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | `true`(선택) | 선물 RiskManager evaluate_order의 첫 가드 |
| `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` | 빈 문자열 | 실 KIS 키 입력 | KisClient.has_credentials() True |
| `ANTHROPIC_API_KEY` | 빈 문자열 | (LIVE_AI에 한해) 실 키 | 실 LLM 호출 가능 |

**확인 필요**: `.env` 변경, API key 입력은 사용자 직접 수행. 본 세션이나 PR이
이 값을 git에 커밋해서는 안 된다 (CLAUDE.md 절대 원칙 4).

## 2. 코드 단 차단 — 라이브 주식 주문

| 파일 | 위치 | 현재 동작 | LIVE 활성화 시 변경 |
|---|---|---|---|
| `backend/app/brokers/kis.py` | `KisBrokerAdapter.place_order` | `is_paper=False` 분기에서 `NotImplementedError` (또는 `cancel_order`도) | 실 KIS `/uapi/domestic-stock/v1/trading/order-cash` 호출 추가. tr_id `TTTC0802U` (BUY) / `TTTC0801U` (SELL) |
| `backend/app/brokers/kis.py` | `KisBrokerAdapter.cancel_order` | stub | 실 KIS `/uapi/domestic-stock/v1/trading/order-rvsecncl` |
| `backend/app/api/deps.py::get_broker` | factory | `MARKET_DATA_PROVIDER`/`DEFAULT_MODE` 조합으로 분기 | LIVE_MANUAL_APPROVAL + ENABLE_LIVE_TRADING + KIS_IS_PAPER=false 시 실 KIS adapter 라우팅 추가 |

**확인 필요**: 위 변경 PR은 별도 옵트인. 머지 전 사용자 확인 절차 필수.

## 3. 코드 단 차단 — 라이브 선물 주문

| 파일 | 위치 | 현재 동작 | LIVE 활성화 시 변경 |
|---|---|---|---|
| `backend/app/futures/risk.py` | `FuturesRiskManager.evaluate_order` | `enable_futures_live_trading=True`여도 항상 REJECTED ("live futures evaluation not implemented yet") | 실제 평가 로직 구현. margin / max_contracts / daily_loss / 만기 / 등락률 |
| `backend/app/futures/mock.py` | (해당 없음) | 가상 broker만 존재 | 새 파일 `backend/app/futures/kis_futures.py` 생성 + 별도 `FuturesBrokerAdapter` 구현체 |
| `backend/app/api/deps.py` | (해당 없음) | 선물 broker factory 미존재 | `get_futures_broker()` 추가. live 분기에서 KIS futures 라우팅 |

**확인 필요**: 선물 라이브는 단계적 활성화 (먼저 LIVE_FUTURES_SHADOW 같은 read-only 모드 거친 뒤 manual approval).

### 3.1 선물 LIVE 활성화 blocker 체크리스트 (#46)

선물 범위 정책은 [`futures_scope.md`](futures_scope.md)에 정의되어 있다. 본 절은 그 문서의 §10 *실전 전 필수 조건*을 LIVE 활성화 차단 항목으로 명시한다 — 어느 하나라도 미충족이면 선물 LIVE PR을 머지하지 않는다:

- [ ] **주식 MVP 완료** — `LIVE_MANUAL_APPROVAL` + `LIVE_AI_ASSIST`(#44) 무사고 운영 (`promotion_policy.md` 단계별)
- [ ] **국내/해외선물 1차 시장 선택** — `futures_scope.md` §3 비교표 기준 *하나만* 선택 (동시 도입 금지)
- [ ] **모의투자 환경 검증** — KIS / Kiwoom 모의투자에서 선물 호가 / 잔고 / 증거금 폴링 4주 이상 무중단
- [ ] **`FuturesAIExecutionGate` 추가** — `AIExecutionGate`(#45) 위에 futures-specific 보수적 한도 (max_notional, max_contracts=1, leverage cap, 야간 차단)
- [ ] **Futures trading calendar** — 영업일 / 만기일 / SQ 데이터 소스 확정
- [ ] **롤오버 정책** — 근월물 → 차월물 시뮬 검증 (가상 환경 먼저)
- [ ] **증거금 reconciliation** — 가상 산식 vs broker API 차이 측정 (PASS 기준 별도 PR)
- [ ] **강제청산 시나리오 학습** — 운영자가 forced liquidation flow 수동 검증
- [ ] **사용자 별도 opt-in PR** — `ENABLE_FUTURES_LIVE_TRADING=true` + 운영자 명시 승인

선물 LIVE는 **주식 MVP보다 위험도가 한 등급 높다** (레버리지 / 강제청산 / 환율 / 24시간 거래) — 본 체크리스트의 모든 항목이 PR review에서 명시적으로 추적되어야 한다.

## 4. 코드 단 차단 — AI 자동 실거래

| 파일 | 위치 | 현재 동작 | LIVE 활성화 시 변경 |
|---|---|---|---|
| `backend/app/core/modes.py` | `can_ai_execute` | `LIVE_AI_EXECUTION + enable_ai_execution=True`이면 True; `VIRTUAL_AI_EXECUTION`은 flag 무관 True | 변경 불필요 — capability 자체는 이미 정확 |
| `backend/app/ai/virtual_agent.py` | `VirtualAiAgent.propose_stub` | 결정적 종가 비교 | 실제 LLM 호출하는 `propose_via_llm` 추가 (별도 클래스 권장 — `LiveAiAgent`) |
| (신규) | `backend/app/ai/live_agent.py` | (해당 없음) | Anthropic API 호출 + JSON 신호 파싱 + route_order(requested_by_ai=True, mode=LIVE_AI_EXECUTION) |

**확인 필요**: 실 LLM 호출은 비용 발생 — usage limit + retry policy 별도 설계.

## 5. 데이터베이스 / 마이그레이션

현재 0011까지 적용. LIVE 활성화로 추가 마이그레이션이 즉시 필요한 항목 없음.
다만 운영 단계에서 다음 항목은 별도 마이그레이션 필요:

- `OrderAuditLog` 무한 누적 — archival/partition 정책 (backlog).
- 선물 주문용 audit 테이블 (`futures_order_audit_log`) — 현재 `OrderAuditLog`는 주식 전용 스키마. 선물 도입 시 별도 테이블 또는 polymorphic 확장.

## 6. RiskPolicy 한도 재검토

라이브 자금 규모에 맞춰 RiskPolicy 한도 조정 필요. 현재 default는 검증용:

| 한도 | 현재 | 라이브 권장 (소액 운영) |
|---|---:|---:|
| `max_order_notional` | 1,000,000 | 운영자 결정 (운용 자본의 10% 이하 권장) |
| `max_daily_loss` | 200,000 | 운영자 결정 (운용 자본의 2% 이하 권장) |
| `max_positions` | 5 | 운영자 결정 |
| `max_symbol_exposure` | 1,500,000 | 운영자 결정 |
| `stale_price_max_age_seconds` | 60 | 30 (라이브에서는 더 엄격) |
| `enable_live_trading` | False | True |
| `enable_ai_execution` | False | (LIVE_AI_EXECUTION 단계 도달 후) True |

`docs/promotion_policy.md`에 단계별 한도 권장값 매트릭스 정리되어 있음.

## 7. UI 가드

라이브 활성화 시 frontend가 분명히 **현재 모드와 ENABLE 플래그 상태**를
운영자에게 보여줘야 한다. 현재 구현:

- ✅ `frontend/src/components/tabs/Settings.jsx`: 모드 + flag 상태 + 위험
  조합(LIVE_MANUAL + ENABLE_LIVE_TRADING=false) 경고 배너.
- ✅ `BackendPolicyCard`: OVERRIDDEN 배지로 default와 다른 한도 식별.
- ✅ Dashboard: emergency_stop ON 시 빨간 배너.

**확인 필요**: 라이브 활성화 시 BotControl 탭에 "현재 LIVE / 가상" 큰 라벨
추가 권장 (운영자 실수 방지).

## 8. 운영자 절차 체크리스트

LIVE 활성화 전 운영자가 확인해야 할 것:

- [ ] PAPER 모드에서 4주 이상 무중단 운영 (`docs/promotion_policy.md` 3단계).
- [ ] LIVE_SHADOW 모드에서 4주 이상 시세 / 잔고 폴링 정상.
- [ ] backtest 단계에서 PF ≥ 1.2, 거래 100건 이상.
- [ ] RiskPolicy 한도가 운용 자본 대비 합리적.
- [ ] 운영자명 / decided_by 프로토콜 확정.
- [ ] emergency_stop 토글 테스트 — KST 시장 시간 외에서 ON / OFF 한 번씩.
- [ ] `/api/reconciliation/status` (212) IN SYNC 확인 — broker 인식 포지션 vs audit 산출 포지션 drift 0건. SHADOW / PAPER 단계에서 며칠 단위 모니터링.
- [ ] AI 자동매매 활성화는 LIVE_MANUAL_APPROVAL 단계 1개월 이상 무사고 후만.

## 9. 본 세션이 "절대 안 한 것" 단정문

본 세션의 어떤 PR도 다음을 수행하지 않았다:

1. ❌ `.env` / 시크릿 / 계좌번호 / API key 변경.
2. ❌ `KisBrokerAdapter.place_order(is_paper=False)` 활성화 (여전히 NotImplementedError).
3. ❌ `KisBrokerAdapter.cancel_order` 구현 (여전히 stub).
4. ❌ `FuturesRiskManager.evaluate_order` 라이브 평가 로직 활성화 (여전히 REJECTED).
5. ❌ `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` 기본값 변경.
6. ❌ 실 KIS / Anthropic API 호출 코드 추가 (read-only 분석은 기존부터 존재 — 본 세션에서 추가 안 함).
7. ❌ 라이브 broker endpoint 호출 — 모든 테스트는 Mock 사용.
8. ❌ `git reset --hard` / force push / 파일 대량 삭제.

## 관련 문서

- [`docs/promotion_policy.md`](promotion_policy.md) — 단계별 승격 기준
- [`docs/risk_policy.md`](risk_policy.md) — RiskManager 평가 매트릭스
- [`docs/virtual_trading_architecture.md`](virtual_trading_architecture.md) — 가상 환경 아키텍처
- [`docs/futures_scope.md`](futures_scope.md) — 선물 1차 범위 + 국내/해외선물 비교 (#46)
- [`docs/futures_simulation_report.md`](futures_simulation_report.md) — 가상 산식 + invariant
- [`CLAUDE.md`](../CLAUDE.md) — 절대 원칙
