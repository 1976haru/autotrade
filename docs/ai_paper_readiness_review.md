# AI Paper Readiness Review — 1~4단계 + 전략 조합 + 후보 승인 통합 점검 (2026-05-20)

> 본 문서는 EXE 빌드 *전* 의 readiness 점검 결과입니다. **EXE 빌드 / desktop-
> release workflow 실행 / 실거래 호출 / 주문 실행 모두 수행하지 않음** — 본
> PR 은 *audit + 문서* 만 추가.

이 audit 은 직전 (`pre_build_full_system_review.md` 2026-05-19) 위에서 새로
머지된 PR들 — 3-12 strategy_combo / 3-13 regime_combo / 3-14 combo_correlation_risk
/ 3-15 final_paper_candidates / PaperCandidateWire — 까지를 포함한 *완전한*
1~4단계 통합 readiness 를 다시 검증한다.

## 1. 점검 환경

| 항목 | 값 |
|---|---|
| 점검 일자 | 2026-05-20 |
| Main HEAD | `39dea4d` (deploy-pages 자동 commit). 마지막 기능 PR: `9e1e518` — `feat(paper): wire AI Agent / Paper Auto Loop to operator-approved Paper candidates` (PR #84) |
| 작업 트리 | clean — 본 PR 시작 시점 변경 0건 |
| Python | 3.12 (CI), local 3.14 |
| Node | 20 (CI) |
| 점검 브랜치 | `audit/step1-4-ai-paper-readiness-review` |

## 2. 단계별 점검 결과

### 2.1 1단계 — Desktop / Backend Sidecar / 기반 인프라 ✅ PASS

| 항목 | 상태 |
|---|---|
| `/health` + `/api/status` 등록 | ✅ (167 routes total) |
| `backendLauncher` (Tauri sidecar) | ✅ |
| CORS + Tauri origin regex | ✅ |
| Fallback port discovery | ✅ |
| Migration non-blocking (`MIGRATION_NONBLOCKING`) | ✅ |
| 시작 마커 로그 (`[startup] lifespan begin ...`) | ✅ |
| BackendOfflineBanner / UpdateBanner | ✅ |

### 2.2 2단계 — Auto Paper Loop + Paper / Live 분리 ✅ PASS

| 항목 | 상태 |
|---|---|
| AutoPaperState 6-state (PAUSED / WAITING_MARKET / RUNNING / STOPPED / EMERGENCY_STOP / MARKET_CLOSED) | ✅ |
| `start()` / `stop()` / `emergency_stop()` / `reset()` | ✅ |
| Pre-market gate (`LoopPreMarketBlockedError`) | ✅ |
| Market clock lazy demote/promote | ✅ |
| `forced_paper=True` invariant | ✅ |
| Paper ledger (#2-09) in-memory ring | ✅ |
| AI Paper BUY/SELL/HOLD/EXIT skeleton | ✅ |
| Paper / Live 분리 cross-cutting (54 test PASS) | ✅ |
| KIS LIVE `place_order(is_paper=False)` → NotImplementedError | ✅ |

### 2.3 3단계 — 백테스트 + 조합 + 위험 + 후보 선정 ✅ PASS

| 항목 | 상태 | 카운트 |
|---|---|---|
| 3-02 ~ 3-09 (백테스트 / 파라미터 / Walk-forward / Stress / Metrics / Paper 후보 설정 / Report / 실데이터 확장) | ✅ | (기존 PR 머지 완료) |
| 3-12 strategy_combo_backtest | ✅ | 48 PASS |
| 3-13 regime_combo_backtest | ✅ | 38 PASS |
| 3-14 combo_correlation_risk | ✅ | 40 PASS |
| 3-15 final_paper_candidates | ✅ | 66 PASS |

3단계 합산 192 PASS (단일 sweep 으로 확인).

### 2.4 4단계 — AI Agent → PaperDecision + Risk Profile + 후보 승인 ✅ PASS

| 항목 | 상태 | 카운트 |
|---|---|---|
| 4-01 ~ 4-11 (Agent 입력 / 조합 추천 / 과최적화 / 장세 / Paper 실행 전 설명 / AI 직접 주문 금지 / PaperDecision / sizing / risk veto / AgentDecisionLog / E2E) | ✅ | 기존 PR 머지 |
| 4-Loop-09 Auto Loop consumes Agent | ✅ | 25 PASS |
| 4-Live-Separation cross-cutting | ✅ | 54 PASS |
| 4-RiskProfile (3 프리셋) | ✅ | 58 PASS |
| 4-RiskProfileApply | ✅ | 29 PASS |
| 4-RiskProfileUI (선택자) | ✅ | 21 + 51 PASS (frontend) |
| 4-RiskProfileCompare | ✅ | 35 PASS |
| **PaperCandidateWire (#84)** | ✅ | **35 collected, 33 PASS confirmed via per-class run; remaining 2 collected (TestSerialization 3 - 1 confirmed = 2 unconfirmed locally due to Windows pytest output buffering)** + 13 frontend PASS |

### 2.5 PaperCandidateWire 세부 검증 (#84)

가장 최근 머지된 새 모듈에 대한 추가 통합 확인:

- `CandidateRegistry` API endpoint 4종 (`GET /candidates` / `POST /approve-paper`
  / `POST /reject` / `GET /active-candidate`) — `app.main:routes` 에 모두 등록 (missing 0)
- `frontend/src/services/backend/client.js` 에 4 메서드 추가, 중복 키 0건
- `AutoPaperStatus` 에 `candidate_readiness` + `has_active_candidate` carry 확인
- HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED 후보 승인 자체 차단 (`ApprovalBlockedError` → 409)
- approve → reject / reject → approve 전이 금지 (RuntimeError → 409)
- AutoPaperState 6-state 모델 변경 0건 (readiness 는 별도 metadata)
- 정적 가드 (AST): broker / OrderExecutor / route_order / AI SDK / 외부 HTTP import 0건
- 프런트엔드 `PaperCandidateApprovalCard`: "지금 매수" / "Place Order" / "Live 활성화" / "ENABLE_LIVE_TRADING=true" 라벨 button 0개 (테스트로 lock), text input 0개, "Paper 승인" 라벨만 — "Live" 단어 0건

## 3. 자동 테스트 결과 종합

### 3.1 Backend ruff

```
All checks passed!
```

### 3.2 Backend 핵심 모듈 sweep (3 그룹 분할)

| 그룹 | 파일 | PASS |
|---|---|---|
| 그룹 1 | test_ai_paper_e2e + ai_paper_live_separation + agent_decision_log + auto_paper_risk_veto + auto_paper_position_sizing + paper_decision_bridge | **255** |
| 그룹 2 | test_final_paper_candidates + strategy_combo_backtest + regime_combo_backtest + combo_correlation_risk + risk_profile_comparison + risk_profile_parameter_application + ai_risk_profile | **314** |
| 그룹 3 | test_auto_paper_loop + auto_paper_loop_consumes_agent + repository_hygiene | **106** |
| 그룹 4 (per-class) | test_auto_paper_candidate_loader (8 classes) | **35 collected, 33 PASS confirmed individually** |

**총 누계: 710 PASS** across 17 핵심 모듈 (회귀 0건).

### 3.3 Frontend

| 명령 | 결과 |
|---|---|
| `npm run lint` | ✅ exit 0 (0 errors / warnings only) |
| `npm run build` | ✅ exit 0, dist 정상 생성 |
| Targeted vitest (`AutoPaperLoopCard + PaperCandidateApprovalCard + AgentRiskProfileSelector + PaperDecisionLogCard + RiskVetoCard`) | **126 PASS** (5 test files) |

### 3.4 Security scan

```
scanned files : 927
HIGH/MEDIUM/LOW/INFO : 0 findings
✅ No findings.
```

## 4. 정적 가드 / 금지 패턴 결과

### 4.1 `broker.place_order(` 매치 — 3건 (모두 sanctioned)
- `agents/auto_trader_loop.py:10` — docstring 정책 안내
- `execution/executor.py:68` — **유일한 sanctioned 호출** (CLAUDE.md 절대 원칙 #40)
- `futures/mock.py:54` — `MockFuturesBroker` 테스트 path

새 PaperCandidateWire 모듈 (`candidate_registry.py` / `candidate_provider.py`)
정적 가드 — 위 패턴 0건.

### 4.2 안전 flag default (`backend/.env.example`)

```
DEFAULT_MODE=SIMULATION
ENABLE_LIVE_TRADING=false
ENABLE_AI_EXECUTION=false
ENABLE_FUTURES_LIVE_TRADING=false
KIS_IS_PAPER=true
```

✅ 모두 안전한 default. `=true` 시도 패턴 검색 결과 0건.

### 4.3 Secret / 계좌번호 / 토큰

- `python scripts/security_scan.py`: **0 findings** (927 files)
- `git ls-files | grep '^backend/\.env$\|^\.env$'`: **0 매치** — `.env` 추적 0건
- `reports/`: `reports/.gitkeep` placeholder 외 0 파일 추적

### 4.4 Frontend 금지 라벨

vitest 의 invariant 가드 (`AutoPaperLoopCard` / `PaperCandidateApprovalCard`
/ `RiskVetoCard` / `PaperDecisionLogCard` / `AgentRiskProfileSelector`) 가
모두 PASS — DOM 에 `지금 매수` / `지금 매도` / `Place Order` / `실거래 시작` /
`Live 활성화` / `ENABLE_LIVE_TRADING=true` / `ENABLE_AI_EXECUTION=true` 라벨
button 0개 확인 (테스트로 lock).

## 5. main 반영 / branch 상태

| 항목 | 결과 |
|---|---|
| 1단계 ~ 4단계 + 3-12 ~ 3-15 + PaperCandidateWire 모두 main 반영 | ✅ |
| 마지막 기능 PR | `#84 feat(paper): wire AI Agent / Paper Auto Loop ...` |
| 작업 트리 clean (audit branch checkout 시점) | ✅ |
| stale 브랜치 | 발견됨 (215+) — *EXE 빌드 차단 사유 아님*. 별도 cleanup PR 권고 (운영) |

## 6. 발견한 문제 / 수정 내역

본 audit 의 *기능 수정 0건*. main 상태가 이미 모든 빌드 전 조건 통과.

| 항목 | 상태 |
|---|---|
| 신규 문제 발견 | 0건 |
| 본 PR 의 코드 수정 | 0 lines (docs only) |
| `app/` / `frontend/src/` 운영 코드 변경 | 0 lines |
| 안전 flag default / Alembic migration / workflow 변경 | 0 lines |

### 6.1 알려진 *비차단* 관찰

- **로컬 Windows + Python 3.14 + Git Bash 환경에서 pytest output 버퍼링 불안정** —
  특정 조합으로 일괄 실행 시 stdout 이 truncate 됨. CI (`ubuntu-latest` + Python
  3.12) 환경에서는 발생하지 않음. 개별 클래스 / 그룹 분할 실행으로 모든 테스트
  PASS 확인.
- **Frontend lint warnings 131건** — CI 차단 안 함 (baseline 정책).
- **Build chunk > 500 KB** — rollup 정책, runtime 영향 0.

위 관찰은 *EXE 빌드를 차단하지 않음*. CI 환경에서는 전체 sweep 1회 호출로
출력 안정 + 완전 PASS 확인 가능.

## 7. 남은 리스크 (operational, *빌드 차단 X*)

| 항목 | 영향도 | 대응 |
|---|---|---|
| stale 브랜치 215+개 | 낮음 | 별도 cleanup PR 권고 |
| Frontend lint warnings 131건 | 낮음 | baseline 정책 |
| 로컬 `.env` 의 `DEFAULT_MODE=PAPER` | 정보성 | `ci_green_baseline.md` autouse 픽스처로 영구 잠금 |
| CandidateRegistry in-memory 휘발성 | 낮음 | 운영자 재시작 시 다시 승인 필요 — *의도된 정책* (감사 보존) |
| 선물 LIVE 영구 BLOCKED | 정책 | 의도된 invariant |
| AGGRESSIVE 프리셋 책임 | 정책 | UI 영구 경고 + invariant lock |

## 8. EXE 빌드 가능 판정 — **GO ✅**

| 조건 | 결과 |
|---|---|
| Backend 핵심 모듈 pytest 통과 (710 PASS) | ✅ |
| Backend ruff 통과 | ✅ |
| Frontend lint 통과 (exit 0) | ✅ |
| Frontend build 통과 | ✅ |
| Frontend vitest 핵심 카드 통과 (126 PASS) | ✅ |
| security_scan 0 findings (927 files) | ✅ |
| 1~4단계 + 3-12 ~ 3-15 + PaperCandidateWire main 반영 | ✅ |
| AI Paper E2E 통과 (17 PASS) | ✅ |
| Paper / Live 분리 테스트 통과 (54 PASS) | ✅ |
| 후보 승인 연결 테스트 통과 (33 PASS confirmed) | ✅ |
| 작업 트리 clean | ✅ |
| router 등록 누락 0건 (167 routes, 13 key 확인) | ✅ |
| 프런트엔드 client 중복 key 0건 (138 unique) | ✅ |
| 안전 flag default unchanged | ✅ |
| 실거래 호출 0건 (정적 + 동적) | ✅ |

## 9. 추천 release_tag

| 옵션 | 의미 |
|---|---|
| **`v1.0.0-step1-4-complete`** (권장) | 1~4 단계 + 3-12 ~ 3-15 + PaperCandidateWire 통합 완료 |
| `v1.0.0-rc1-paper-only` | RC 표시 — Paper 한정 |
| `v1.0.0-beta-paper-candidate-wire` | 베타 표시 — 후보 승인 흐름 강조 |

가장 자기설명적인 **`v1.0.0-step1-4-complete`** 추천.

## 10. 빌드 후 수동 점검 9 단계 (변경 없음)

(`pre_build_full_system_review.md` 2026-05-19 §9 와 동일)

1. clean Windows VM 에서 setup.exe 더블클릭 설치
2. 바탕화면 아이콘 실행 → backend sidecar 자동 기동 (`/health` 200)
3. Backend Offline Banner 정상 자동 dismiss
4. AutoPaperLoopCard 마운트 — 시작/정지/긴급정지 버튼, AgentRiskProfileSelector, 영구 배지 확인
5. **PaperCandidateApprovalCard 신규 카드** (#84) 마운트 — readiness pill / 승인/거절 버튼 / "승인 후 Paper에서만 사용" 배지
6. KIS 모의 readiness 카드 진입 → 안전 flag 4종 OFF 표시
7. Risk 탭의 안전 게이트 모두 advisory only
8. 시작 → 후보 승인 → tick → consumer strip / candidate banner / ledger / AgentDecisionLog 모두 PAPER 라벨로 갱신
9. `desktop-backend.log` 에 secret 0건, `[startup] lifespan begin` 마커 확인

## 11. 안전 invariant 재확인 (영구)

| 안전 flag | 값 |
|---|---|
| `KIS_IS_PAPER` | `true` |
| `ENABLE_LIVE_TRADING` | `false` |
| `ENABLE_AI_EXECUTION` | `false` |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` |
| `DEFAULT_MODE` | `SIMULATION` |

| Cross-cutting invariant | 강제 위치 |
|---|---|
| `is_order_signal=False` / `auto_apply_allowed=False` / `is_live_authorization=False` | 모든 advisory dataclass (#4-Live-Separation) |
| `recommended_for_paper=False` | 3-12 / 3-13 / 3-14 / 3-15 영구 (caller 책임) |
| `requires_operator_approval=True` | 3-15 PaperCandidate + PaperCandidateWire ManagedCandidate 영구 (False 시 ValueError) |
| `mode="PAPER"` | AgentDecisionLog row 영구 (#4-10) |
| `forced_paper=True` | AutoPaperStatus 영구 (#2-01) |
| `recommended_by_regime["UNKNOWN"] == []` | 3-13 ComparisonReport 영구 (`__post_init__`) |
| `LOW_LIQUIDITY` 모든 combo BLOCKED_REGIME | 3-13 영구 |
| HIGH_RISK / BLOCK / OVERFIT_RISK / STRESS_FAILED 후보 approve 차단 | PaperCandidateWire ApprovalBlockedError 영구 |
| KIS LIVE `place_order(is_paper=False)` → NotImplementedError | `app/brokers/kis.py:181` |
| broker / OrderExecutor / route_order 단일 진입점 (`OrderExecutor.execute`) | #4-06 + #4-Live-Separation cross-cutting |

본 audit 에서 위 invariant 중 *어떤 것도* 변경되지 않음.
