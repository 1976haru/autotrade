# Live Readiness Policy — AI Paper 와 AI Live 실전 단계 분리 (#0-01)

> **본 문서는 자동매매 시스템의 *최상위 경계 정의* 입니다.**
> 본 문서의 체크리스트 충족만으로 *실전 자동매매가 가능한 것은 아닙니다*.
> 실전(Live) 진입은 본 문서 외에 **별도 Live Gate / Canary 통과 + 운영자 명시
> 옵트인 PR** 이 모두 필요합니다.

---

## 0. 최우선 안전 기본값 (영구)

본 프로젝트의 default `.env` 는 **모두 비활성** 상태로 출하되며, 본 문서의
모든 단계는 이 기본값을 *침해하지 않는 한*에서만 유효합니다.

| 환경 변수 | Default | 의미 |
|---|---|---|
| `KIS_IS_PAPER` | `true` | KIS API 모의투자 모드 강제. `false` 전환은 별도 옵트인 PR. |
| `ENABLE_LIVE_TRADING` | `false` | LIVE_* 모드에서도 실거래 차단. |
| `ENABLE_AI_EXECUTION` | `false` | LIVE_AI_EXECUTION 모드에서 AI 자동 실행 차단. |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | 선물 실거래 차단 (선물 AI Execution 은 *영구* BLOCKED — #76). |

위 4개 flag default 변경은 **CI 정적 가드 + 다층 안전 가드** (#88 hygiene /
#93 security_scan / 본 문서) 로 차단됩니다. 임의 변경 시 머지 거부.

---

## 1. 최종 목표 정의

본 프로젝트의 *최종 목표* 는 다음 흐름의 *연구·검증 플랫폼* 입니다:

1. 사용자가 데스크톱 EXE 의 **"시작" 버튼**을 누른다.
2. **AI Agent** 가 **Paper 단계**에서 매수 / 매도 / 보류 / 청산을 *자동 판단* 한다.
3. **단, Paper 단계는 *실제 주문이 아니다*.** — MockBroker 또는 KIS 모의투자
   (`KIS_IS_PAPER=true`) 에서만 가상 체결.
4. **실전(Live) 자동매매는 별도 Live Gate / Canary 통과 후에만** 가능하다.
   본 문서의 Paper 체크리스트 완료만으로는 *불가능*.

> 이 문서는 "AI 가 *시작 버튼 한 번* 만으로 실거래를 자동 수행한다" 를 의미
> *하지 않습니다*. AI Paper 자동매매와 AI Live 실전 자동매매는 **전혀 별개의
> 단계** 이며, 각각 다른 안전 게이트로 차단됩니다.

---

## 2. 단계 분리 (4 stages — *각각 별개의 게이트*)

| # | 단계 | 의미 | 진입 조건 (요약) | 실제 broker 호출 |
|---|---|---|---|---|
| 1 | **AI Paper Auto Trading** | AI Agent 가 모의투자에서 자동 매매 (현재 진행중) | §3 AI Paper 체크리스트 PASS | **❌** MockBroker / KIS Paper 만 (`KIS_IS_PAPER=true`) |
| 2 | **AI Live Manual Approval** | AI 가 후보 *제안* 만, 운영자가 *수동 승인* 후 실주문 | §4 AI Live + 본 단계 추가 게이트 PASS + 운영자 명시 옵트인 | ✅ 운영자 *수동 승인* 후만 |
| 3 | **AI Live Canary** | 초소액 자동 실주문 (예: 1회 ≤ 3만원, 일일 ≤ 5천원 손실) | §4 AI Live + 운영자 옵트인 + 즉시 kill switch 가능 | ✅ **초소액 한정** |
| 4 | **AI Live Auto Execution** | 정상 한도 내 AI 자동 실주문 | Canary 검증 통과 + 별도 옵트인 PR + #75 AIExecutionGate PASS | ✅ 한도 내 |

각 단계는 **별개의 ENV flag / 운영자 옵트인 / 게이트** 로 분리됩니다. 이전
단계의 PASS 가 다음 단계를 *자동* 허가하지 않습니다 — 매번 운영자가 명시
승인해야 다음 단계로 진입 가능합니다.

관련 게이트 모듈:
- 단계 2 진입: [`docs/live_manual_gate.md`](live_manual_gate.md) (#73)
- 단계 4 *활성화 검토* : [`docs/ai_execution_gate.md`](ai_execution_gate.md) (#75)
- 단계 4 *주문 시점* 가드: [`docs/ai_execution_policy.md`](ai_execution_policy.md) (#45)

---

## 3. AI Paper 가능 조건 (단계 1 진입)

본 단계는 *현재 활성 단계* 이며, 다음 모든 조건이 PASS 일 때만 운영자가
"시작" 을 누를 수 있어야 합니다.

| # | 조건 | 검증 위치 |
|---|---|---|
| 3-1 | **EXE 실행** (또는 backend + frontend 직접 실행) | `docs/desktop_packaging.md` (#86) |
| 3-2 | **Backend 자동 연결** (EXE 가 backend sidecar 기동 + frontend 가 status 엔드포인트 OK) | `docs/desktop_exe_status.md` |
| 3-3 | **Paper Auto Loop 정상 작동** (#2-01~#2-08) | `docs/paper_mode.md` |
| 3-4 | **AI 전략 조합 추천 완료** (3-02~3-08, 4-01~4-04) | `docs/strategy_optimization_report.md` / `docs/strategy_combination_recommendation.md` |
| 3-5 | **Pre-market PASS 또는 WARN** (`start_allowed=True`) | [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) (#80, #91) |
| 3-6 | **PaperBroker 또는 VirtualExecutor 만 사용** (`PaperTrader.assert_paper_broker`) | [`docs/paper_trading_policy.md`](paper_trading_policy.md) (#42) |
| 3-7 | **실거래 broker 호출 0건** (`KisBrokerAdapter.place_order(is_paper=False)` `NotImplementedError`) | `app/brokers/kis.py` |
| 3-8 | `KIS_IS_PAPER=true` 유지 | `.env` (default) |
| 3-9 | `ENABLE_LIVE_TRADING=false` 유지 | `.env` (default) |
| 3-10 | `ENABLE_AI_EXECUTION=false` 유지 | `.env` (default) |

위 모든 조건 PASS → AI Paper 자동매매 *시작 가능*. 단계 1 동안 AI 의 매수/매도
판단은 **실 주문을 발생시키지 않습니다** — MockBroker / KIS 모의투자만 사용.

---

## 4. AI Live 가능 조건 (단계 2~4 진입)

**AI Live 진입은 본 문서 단독으로 결정되지 않습니다.** 다음 4개 게이트 + 운영자
명시 옵트인 *모두* 통과해야 합니다.

### 4.1. 운영 데이터 조건 (Paper 누적 결과)

| # | 조건 | 검증 위치 |
|---|---|---|
| 4-1 | **Paper 운용 ≥ 28일** | [`docs/paper_gate_policy.md`](paper_gate_policy.md) (#72) |
| 4-2 | **모의 신호/체결 ≥ 100건** | 위 |
| 4-3 | **기대값 (expectancy) > 0** | 위 |
| 4-4 | **Profit Factor ≥ 1.2** | 위 |
| 4-5 | **MDD ≤ 15%** | 위 |
| 4-6 | **손실 한도 위반 0건** | 위 |
| 4-7 | **stale data 위반 0건** | RiskManager step 1.5 (#143) |
| 4-8 | **중복 주문 위반 0건** | OrderGuard (#38) |
| 4-9 | **audit 누락 0건** | OrderAuditLog |
| 4-10 | **AI Agent 위험 차단 성능 확인** | [`docs/ai_assist_gate.md`](ai_assist_gate.md) (#74) — 운영자 거절율 / Risk 거절율 / confidence calibration 등 |

### 4.2. 코드 단 게이트 (모두 PASS 필수)

| 게이트 | 의미 |
|---|---|
| **Paper Gate** (#72) | §4.1 의 28일 / 100건 / PF / MDD 등을 *코드 단으로 평가* |
| **Live Manual Gate** (#73) | 단계 2 진입 readiness — 운영자 explicit opt-in + 1회 ≤ 5만원 / 일일 손실 ≤ 1만원 / 보유 ≤ 3개 |
| **AI Assist Gate** (#74) | 단계 2 운용 결과를 read-only 검증 — 신호 품질 / 거절율 / confidence calibration |
| **AI Execution Activation Gate** (#75) | 단계 4 *활성화 readiness* — `READY_FOR_REVIEW` 라벨 (자동 활성화 *아님*) |

### 4.3. 운영자 명시 행동 (자동화 *불가*)

| # | 조건 |
|---|---|
| 4-11 | **운영자 명시 승인** — 각 단계 진입 시 운영자가 본인 의도를 서면 또는 PR 코멘트로 기록 |
| 4-12 | **`ENABLE_LIVE_TRADING=true` 별도 PR** — `.env` 수정 PR 을 운영자가 직접 작성 / 머지 |
| 4-13 | **초소액 Canary 통과** (단계 4 진입 시) — 단계 3 운용 결과 검토 + 정상 한도 진입 결정 |
| 4-14 | **즉시 kill switch 가능** — 3-Level Kill Switch (#37) 가 운영자 PC 에서 즉시 동작 확인 |

### 4.4. 영구 BLOCKED 항목

| 항목 | 사유 |
|---|---|
| `FUTURES_AI_EXECUTION` | 본 프로젝트는 선물 AI 자동 실행을 **영구 BLOCKED** — `AIExecutionActivationGateResult.futures_allowed=False` 불변 (#75, #76) |
| 만기일 근처 AI 자동매매 (선물) | 어떤 단계에서도 금지 |
| AI 가 broker 주문 API 를 *직접* 호출 | CLAUDE.md 절대 원칙 1 — 영구 |

---

## 5. 명확한 금지 문구

> 본 문서를 읽는 모든 사람이 *반드시* 이해해야 할 4가지:

1. **본 체크리스트 완료만으로 실전 자동매매가 가능한 것은 아닙니다.**
   §3 (AI Paper) 와 §4 (AI Live) 는 *별개의 단계* 이며, §3 PASS 가 §4 진입을
   자동 허가하지 *않습니다*.

2. **Live Gate / Canary 없이 실전 자동매매는 불가능합니다.**
   §4.2 의 4개 게이트 + §4.3 의 운영자 명시 옵트인 *모두* 통과하지 않은
   상태에서 실주문이 발생하면 그것은 *버그* 이며, 다층 안전 가드 (#34 RiskManager
   / #38 OrderGuard / #40 OrderExecutor / #41 PermissionGate / #45 AIExecutionGate)
   가 차단합니다.

3. **AI Paper 단계의 매수/매도 판단은 *실제 주문이 아닙니다*.**
   §3 PASS 후 AI Agent 가 매수/매도/보류/청산을 "결정" 해도, broker 는
   `MockBroker` 또는 `KIS 모의투자` 만 호출됩니다. `KisBrokerAdapter.place_order(
   is_paper=False)` 는 `NotImplementedError` 입니다.

4. **실전은 별도 승인 전까지 차단됩니다.**
   `ENABLE_LIVE_TRADING=false` default 가 유지되는 한, RiskManager 가 LIVE_*
   모드의 모든 주문을 `REJECTED` 처리합니다. 운영자가 `.env` 를 *명시 PR* 로
   변경하기 전까지 실주문은 *발생하지 않습니다*.

---

## 6. 안전 기본값 재확인

```bash
# .env / .env.example default — 본 PR 시점 영구 유지.
KIS_IS_PAPER=true                    # KIS API 모의투자 모드
ENABLE_LIVE_TRADING=false            # LIVE_* 모드에서도 실거래 차단
ENABLE_AI_EXECUTION=false            # LIVE_AI_EXECUTION 에서 AI 자동 실행 차단
ENABLE_FUTURES_LIVE_TRADING=false    # 선물 실거래 차단 (선물 AI Execution 은 영구 BLOCKED)
```

이 4개 flag 의 default 변경은 다음 정적 가드로 차단됩니다:
- `backend/tests/test_repository_hygiene.py` — `.env.example` / `.env.staging.example`
  / GitHub workflow 모두 `false` 강제 검증.
- `scripts/security_scan.py` — `.env` 의 실제값에 `true` / `false` 위반 시 HIGH
  severity finding.
- `.github/workflows/desktop-release.yml` — 빌드 전 / 후 self-check regex 로
  `ENABLE_LIVE_TRADING=true` 같은 패턴 0건 검증.

---

## 7. 관련 문서 (cross-reference)

본 문서를 읽은 뒤 *반드시* 확인할 정책 문서:

| 문서 | 의미 |
|---|---|
| [`docs/promotion_policy.md`](promotion_policy.md) | 운용 모드 7단계 승격 매트릭스 (SIMULATION → PAPER → LIVE_SHADOW → LIVE_MANUAL_APPROVAL → ... → LIVE_AI_EXECUTION) |
| [`docs/paper_gate_policy.md`](paper_gate_policy.md) | Paper Gate (#72) — Paper 4주 운용 결과 평가 |
| [`docs/live_manual_gate.md`](live_manual_gate.md) | Live Manual Gate (#73) — 단계 2 진입 readiness |
| [`docs/ai_assist_gate.md`](ai_assist_gate.md) | AI Assist Gate (#74) — AI 제안 품질 검증 |
| [`docs/ai_execution_gate.md`](ai_execution_gate.md) | AI Execution Activation Gate (#75) — 단계 4 활성화 readiness |
| [`docs/ai_execution_policy.md`](ai_execution_policy.md) | AIExecutionGate (#45) — 주문 시점 final 가드 |
| [`docs/risk_policy.md`](risk_policy.md) | RiskManager (#34) — 모든 주문 사전평가 |
| [`docs/manual_approval_policy.md`](manual_approval_policy.md) | Manual Approval (#41) — 단계 2 큐 |
| [`docs/order_executor_contract.md`](order_executor_contract.md) | OrderExecutor (#40) — broker 호출 *유일* 진입점 |
| [`docs/emergency_stop_policy.md`](emergency_stop_policy.md) | 3-Level Kill Switch (#37) — 즉시 중지 |
| [`docs/paper_trading_policy.md`](paper_trading_policy.md) | PaperTrader (#42) — paper broker 강제 |
| [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) | Pre-market Check (#80, #91) — 시작 전 점검 |
| [`docs/futures_promotion_policy.md`](futures_promotion_policy.md) | 선물 7단계 승격 (AI_EXECUTION 영구 BLOCKED) |

---

## 8. 변경 정책

본 문서는 *최상위 안전 경계* 를 정의합니다. 다음 변경은 **운영자 명시 옵트인
PR 외에는 절대 금지** :

1. §6 안전 기본값 4개 flag 의 default 변경.
2. §2 단계 정의 / 순서 변경.
3. §4 AI Live 조건 약화 (§4.1, §4.2, §4.3 항목 *삭제*).
4. §5 금지 문구 약화 (4개 문구 *삭제* 또는 의미 약화).
5. §4.4 영구 BLOCKED 항목 해제.

위 변경 PR 은 본 문서 + `CLAUDE.md` + 관련 게이트 정책 문서 *모두* 동시
갱신을 요구합니다.

---

## 9. CLAUDE.md 절대 원칙 상속

- ✅ AI 가 broker 주문 API 를 *직접* 호출하지 않는다 — CLAUDE.md 원칙 1.
- ✅ 모든 주문은 `RiskManager → PermissionGate → OrderExecutor` 순서를 거친다 —
  CLAUDE.md 원칙 2.
- ✅ 기본 운용모드는 `SIMULATION` 또는 `PAPER`, `LIVE_AI_EXECUTION` 은 기본
  비활성 — CLAUDE.md 원칙 3.
- ✅ API Key / App Secret / 계좌번호 / Anthropic Key 는 frontend 에 저장 / 커밋
  금지 — CLAUDE.md 원칙 4.
- ✅ 실제 증권사 / AI API 호출은 backend 에서만 — CLAUDE.md 원칙 5.
- ✅ 선물은 주식 MVP 이후 별도 게이트 + Futures AI Execution 영구 BLOCKED —
  CLAUDE.md 원칙 6 + #76.
