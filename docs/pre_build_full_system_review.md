# Pre-Build Full System Review — 1~4단계 통합 점검 (2026-05-19)

> 본 문서는 EXE 빌드 *전* 1~4 단계 전체를 정적 + 동적으로 점검한 결과입니다.
> EXE 빌드 / desktop-release workflow 실행 / 실거래 호출 / 주문 실행 모두
> **수행하지 않음** — 본 PR 은 *audit + 문서* 만.

## 1. 점검 환경

| 항목 | 값 |
|---|---|
| 점검 일자 | 2026-05-19 |
| Main HEAD | `d0d4505` — `Merge pull request #78 from feature/step4-risk-profile-paper-comparison` |
| 작업 트리 | clean (변경 0건) |
| Python | 3.12 (CI), local 3.14 |
| Node | 20 (CI) |
| 점검 브랜치 | `audit/pre-build-full-system-review-step1-4` |

## 2. 단계별 점검 결과

### 2.1 1단계 — Desktop / Backend Sidecar / 기반 인프라

| 항목 | 상태 | 비고 |
|---|---|---|
| `/health` endpoint 등록 | ✅ | `app.main:health` |
| `/api/status` endpoint 등록 | ✅ | `routes_status` 라우터 |
| `backendLauncher` (frontend desktop sidecar) | ✅ | `frontend/src/desktop/backendLauncher.js`, 35 test PASS |
| CORS + Tauri origin regex | ✅ | `main.py::_TAURI_ORIGIN_REGEX` |
| Fallback port (`fix/desktop-backend-startup-readiness`) | ✅ | port discovery + `backend-port.json` write |
| Migration non-blocking (`MIGRATION_NONBLOCKING`) | ✅ | `main.py::lifespan` 분기 — `/health` 가 migration 진행 중에도 200 |
| 로그 보기 / 재시도 | ✅ | `desktop-backend.log`, startup marker (`[startup] lifespan begin ...`) |
| Backend Offline Banner | ✅ | `BackendOfflineBanner.jsx` |
| Update Banner / Version Badge | ✅ | `UpdateBanner.jsx` |

### 2.2 2단계 — Auto Paper Loop + Paper / Live 분리

| 항목 | 상태 | 비고 |
|---|---|---|
| AutoPaperLoop 상태 모델 (PAUSED / WAITING_MARKET / RUNNING / STOPPED / EMERGENCY_STOP / MARKET_CLOSED) | ✅ | `app/auto_paper/loop.py::AutoPaperState`, legacy IDLE / EMERGENCY alias 유지 |
| 시작 / 정지 / 긴급정지 / 리셋 | ✅ | `routes_auto_paper` 4 endpoints |
| Pre-market gate (`LoopPreMarketBlockedError`) | ✅ | `start()` 첫 검사 — 통과 못하면 409 |
| Paper / Virtual 전용 실행 (`forced_paper=True`) | ✅ | `AutoPaperStatus` 영구 invariant |
| Market clock (KST 09:00–15:30) | ✅ | `app/scheduler/market_clock.py`, lazy demote / promote |
| Paper ledger (in-memory ring) | ✅ | `app/auto_paper/ledger.py`, `record_paper_event()` |
| AI Paper BUY/SELL/HOLD/EXIT skeleton | ✅ | `app/auto_paper/decisions.py::PaperDecision` |
| Paper / Live 분리 cross-cutting 잠금 | ✅ | `tests/test_ai_paper_live_separation.py` 54 PASS |
| KIS LIVE `place_order(is_paper=False)` → `NotImplementedError` | ✅ | `app/brokers/kis.py:181` |

### 2.3 3단계 — Backtest / Optimization / Walk-Forward / Stress / Metrics

| 항목 | 상태 | 비고 |
|---|---|---|
| 실제 데이터 백테스트 (#23, #24) | ✅ | `app/backtest/engine.py` + 47 tests |
| 파라미터 최적화 (#25) | ✅ | `app/backtest/parameter_optimization.py` |
| Walk-forward (#26) | ✅ | `app/backtest/walk_forward.py` |
| Stress Test (#27 + analytics) | ✅ | `app/analytics/stress_test.py`, tests 통과 |
| 공통 성과지표 (#28) | ✅ | `app/analytics/metrics.py` |
| Paper 후보 aggregator (#29) | ✅ | `app/analytics/paper_candidate_aggregator.py` 통과 |
| Operator report (#30 / #31) | ✅ | `app/analytics/strategy_optimization_report.py` |
| 실데이터 확장 정책 | ✅ | `docs/real_data_expansion_plan.md` |

### 2.4 4단계 — AI Agent → PaperDecision 전 파이프라인

| 항목 | 상태 | 비고 |
|---|---|---|
| 4-01 Agent 입력 스키마 (`StrategyAgentInput`) | ✅ | merged PR #51 |
| 4-02 전략 조합 추천 (v1 + v2) | ✅ | PR #58 + #61, BALANCED-friendly |
| 4-03 과최적화 경고 (`OverfitWarningAgent`) | ✅ | merged |
| 4-04 장세별 전략 선택 (`MarketRegimeAgent`) | ✅ | PR #63 |
| 4-05 Paper 실행 전 설명 (`PaperStartExplanation`) | ✅ | PR #64 |
| 4-06 AI 직접 주문 금지 가드 | ✅ | PR #65 — agent 모듈 정적 grep + AST 가드 |
| 4-07 Agent → PaperDecision 연결 | ✅ | PR #66 — bridge |
| 4-08 Position sizing | ✅ | PR #67 |
| 4-09 Risk veto priority | ✅ | PR #68 |
| 4-10 AgentDecisionLog | ✅ | PR #69 |
| 4-Loop-09 Auto Loop consumes Agent | ✅ | PR #70 |
| 4-Live-Separation | ✅ | PR #71 — 54 cross-cutting tests |
| 4-11 E2E | ✅ | PR #72 — 17 backend + 4 frontend E2E |
| 4-RiskProfile (3 프리셋) | ✅ | PR #74 |
| 4-RiskProfileApply (파라미터 반영) | ✅ | PR #76 |
| 4-RiskProfileUI (선택자) | ✅ | PR #77 |
| 4-RiskProfileCompare (성향 비교) | ✅ | PR #78 |

## 3. 자동 테스트 결과

### 3.1 Backend pytest 전체

```
4634 passed, 5 skipped, 25 deselected, 6 warnings in 38.11s
```

- 5 skipped: market-clock 시간 의존 / staging-only 테스트
- 25 deselected: `slow` / `stress` (nightly 워크플로에서 별도 실행)
- 추가 통합 sweep: stress / analytics_metrics / paper_candidate_aggregator /
  E2E / live-separation / risk-profile 5종 모두 **405 PASS** (회귀 0)

### 3.2 Backend ruff

```
All checks passed!
```

### 3.3 Frontend

| 명령 | 결과 |
|---|---|
| `npm ci` | ✅ |
| `npm run lint` | 0 errors / 131 warnings (exit 0) |
| `npm run build` | ✅ (dist/assets/index-*.js 669 KB) |
| `npx vitest run --exclude '**/*.stress.test.jsx'` | **1962 PASS / 107 files** |

### 3.4 Security scan

```
scanned files : 904
HIGH/MEDIUM/LOW/INFO : 0 findings
```

## 4. 정적 가드 검증 (수동 grep)

### 4.1 `broker.place_order(` 호출

3 매치 — 모두 *sanctioned*:
- `agents/auto_trader_loop.py:10` — docstring 가드 안내
- `execution/executor.py:68` — **유일한 sanctioned 호출** (CLAUDE.md #40)
- `futures/mock.py:54` — `MockFuturesBroker` 테스트 path (futures mock)

### 4.2 `route_order(` 호출

15 매치 — 모두 *sanctioned*:
- `execution/order_router.py:64` — 정의 위치
- `ai/virtual_agent.py`, `ai/assist.py` — sanctioned AI assist 흐름 (#44)
- `agents/auto_trader_loop.py:557` — sanctioned orchestrator (#4-06 exempt)
- `api/routes_broker.py`, `routes_live_engine.py` — sanctioned HTTP entry
- `virtual/auto_close.py`, `strategies/live_engine.py` — sanctioned engines
- 나머지는 docstring 안내

`auto_paper/`, `agents/` 의 advisory 모듈 0건 (`test_agents_no_direct_order_guard`
+ `test_ai_paper_live_separation` cross-cutting 으로 영구 잠금).

### 4.3 `OrderExecutor(` 호출

3 매치 — 모두 sanctioned:
- `execution/paper_trader.py:214`
- `execution/order_router.py:354`
- `permission/gate.py:252`

### 4.4 안전 flag default

`backend/.env.example`:
```
DEFAULT_MODE=SIMULATION
ENABLE_LIVE_TRADING=false
ENABLE_AI_EXECUTION=false
ENABLE_FUTURES_LIVE_TRADING=false
KIS_IS_PAPER=true
```

모두 *안전한 default*. ⚠ flag `true` / `KIS_IS_PAPER=false` 시도 패턴 검색 결과 0건.

### 4.5 Frontend 금지 라벨

43 파일 매치 — 모두 *docstring/주석 가드* + *테스트 assertion* (forbidden phrase
가 *없음* 을 확인). active button label 로 0건 — `test_ai_paper_live_separation`
+ `RiskVetoCard.test` + `AutoPaperLoopCard.test` + `AgentRiskProfileSelector.test`
+ `PaperDecisionLogCard.test` 모두 통과.

### 4.6 Secret / 계좌번호 / 토큰

- `python scripts/security_scan.py`: **0 findings** (904 files)
- `git ls-files | grep .env`: 0 매치 (단 `.env.example` 만 추적, secret 0건)
- `reports/*` 파일: git tracked 0건 (gitignore `reports/*` 규칙)

## 5. 빌드 가능 판정 체크리스트

| 조건 | 결과 |
|---|---|
| Backend 전체 pytest 통과 (4634 PASS) | ✅ |
| Backend ruff 통과 | ✅ |
| Frontend lint 통과 (0 errors) | ✅ |
| Frontend build 통과 | ✅ |
| Frontend vitest 통과 (1962 PASS) | ✅ |
| security_scan 0 findings (904 files) | ✅ |
| 실거래 호출 0건 (정적 + 동적 spy) | ✅ |
| `.env` / secret 커밋 0건 | ✅ |
| 1~4단계 main 반영 완료 | ✅ — PR #51~#78 모두 merged |
| Paper Auto Loop E2E 통과 (17 PASS) | ✅ |
| AI Paper / Live 분리 테스트 통과 (54 PASS) | ✅ |
| 작업 트리 clean | ✅ |
| router 등록 누락 0건 (162 routes) | ✅ |
| 프런트엔드 client 중복 key 0건 | ✅ |
| stale mock export 누락 0건 (vitest 통과) | ✅ |
| Pre-market BLOCK / Risk veto / Emergency stop 우선순위 lock | ✅ |
| market closed/weekend 상태에서 RUNNING 불가 (lazy demote) | ✅ |
| LOW_LIQUIDITY / OVERFIT_RISK / STRESS_FAILED 자동 제외 | ✅ |
| AGGRESSIVE 가 RiskManager 우회 불가 | ✅ — `is_live_authorization=False` 영구 |

## 6. 발견한 문제 / 수정 내역

본 audit 에서 *기능 수정* 없음 — main 상태가 이미 모든 조건 통과.

| 항목 | 상태 |
|---|---|
| 신규 문제 발견 | 0건 |
| 본 PR 의 코드 수정 | 0 lines (docs only) |
| `app/` / `frontend/src/` 운영 코드 변경 | 0 lines |
| `.env.example` / workflow / Alembic 변경 | 0 lines |

## 7. 남은 리스크 (operational)

| 항목 | 영향도 | 대응 |
|---|---|---|
| **stale 브랜치 215+개** (`git branch -a` 371, merged 156) | 낮음 | EXE 빌드 차단 사유 아님. 운영자가 별도 cleanup PR 권고 |
| **Frontend lint warnings 131건** | 낮음 | CI 차단 안 함. baseline 정책 (`ci_green_baseline.md` §4) 유지 |
| **Local `.env` 의 `DEFAULT_MODE=PAPER`** | 정보성 | dev box 만 — CI 영향 0. `ci_green_baseline.md` §2.A autouse 픽스처로 영구 잠금 |
| **build chunk size > 500 KB** | 낮음 | rollup 정책 — runtime 영향 0, 추후 code-split 검토 |
| **`pandas-ta` 미설치 / 사용 안 함** | 0 | 의존 그래프 0건, 무관 |
| **선물 LIVE 영구 BLOCKED** | 정책 | 의도된 invariant (`FuturesRiskManager.evaluate_order` 항상 REJECTED) |
| **AGGRESSIVE 프리셋 사용 시 책임** | 정책 | UI 영구 경고 + 영구 invariant `is_live_authorization=False` 로 잠금 |

본 리스크 중 *어떤 것도* EXE 빌드를 차단하지 않음.

## 8. EXE 빌드 가능 여부 — **판정: 진행 가능 ✅**

본 audit 의 모든 빌드 전 체크리스트 통과. 이후 빌드 실행 가이드:

1. **수동 trigger only** — GitHub Actions → `desktop-release` → "Run workflow"
   (CLAUDE.md 절대 원칙: push / schedule / PR 자동 트리거 0건)
2. **Windows runner** — `runs-on: windows-latest`
3. **시점**: 본 audit PR 머지 후 main 으로 trigger
4. **추천 태그**: `v1.0.0-step4-complete` (또는 운영자 선호 시
   `v1.0.0-rc1-paper-only`)
5. **빌드 산출물 안전 검사** — workflow 의 빌드 후 가드:
   - `.env` / `*.pem` / `*.key` / `*.p12` / `*.pfx` / `*.crt` /
     `*.cer` / `*.keystore` / `*.jks` 번들 내 포함 0건 검증
   - signing private key 는 GitHub Secrets 만 (`TAURI_PRIVATE_KEY`)
6. **빌드 후 검증** — `docs/desktop_exe_status.md` §8-C 의 9-step 체크리스트

## 9. 빌드 후 수동 점검 권고

EXE 빌드 후 베타테스터 배포 *전*:

1. **clean Windows VM** 에서 setup.exe 더블클릭 설치
2. 바탕화면 아이콘 실행 → backend sidecar 자동 기동 확인 (`/health` 200)
3. Backend Offline Banner 정상 자동 dismiss
4. AutoPaperLoopCard 마운트:
   - 시작 / 정지 / 긴급정지 버튼 노출
   - AgentRiskProfileSelector 3 카드 노출 (기본 BALANCED)
   - "Paper 전용 · 실거래 아님" 영구 배지
   - "지금 매수" / "Place Order" / "ENABLE_*" 라벨 button 0개 (수동 확인)
5. KIS 모의 readiness 카드 진입 → 안전 flag 4종 OFF 표시
6. Risk 탭의 안전 게이트 (Paper Gate / Live Manual Gate / AI Assist Gate /
   AI Execution Gate / Alpha Decay / Correlation Guard) 모두 advisory only
7. 시작 → status RUNNING → consumer strip "Paper 전용 · 실제 주문 아님" 영구
8. `desktop-backend.log` 에 secret 0건, `[startup] lifespan begin` 마커 확인

## 10. 다음 단계

1. **사용자**: 본 audit PR 검토 → Create pull request → Merge → Confirm
2. main 최신화 후 GitHub Actions 탭 → `desktop-release` → "Run workflow"
3. 빌드 산출물 (`.msi` / `.exe`) artifact 다운로드
4. 위 §9 의 빌드 후 수동 점검 9 단계 수행
5. 베타테스터 배포 — `docs/beta_tester_install_guide.md` 참조

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
| `is_order_signal=False` | 모든 advisory dataclass (#4-Live-Separation) |
| `auto_apply_allowed=False` | 위 |
| `is_live_authorization=False` | 모든 프리셋 (CONSERVATIVE / BALANCED / AGGRESSIVE) 영구 |
| `mode="PAPER"` (AgentDecisionLog) | #4-10 dataclass 가드 |
| `forced_paper=True` (AutoPaperStatus) | #2-01 dataclass 가드 |
| KIS LIVE `place_order(is_paper=False)` → NotImplementedError | `app/brokers/kis.py:181` |
| FuturesRiskManager LIVE → REJECTED | `app/futures/risk_manager.py` |
| broker / OrderExecutor / route_order 호출은 `OrderExecutor.execute` 단일 진입점만 | #4-06 + #4-Live-Separation cross-cutting |

본 PR 에서 위 invariant 중 어떤 것도 *변경되지 않음*.
