# 에이전트 트레이더 v1 · Agent Trader v1

> **AI 에이전트가 시장을 분석하고, 사용자는 핵심 판단과 위험만 확인하는 자동매매 관제 시스템**

> ⚠ **현재 상태**: MVP / Paper / Shadow 검증을 위한 연구·운영 플랫폼.
> **실거래 자동매매 허가 상태가 *아닙니다*** — LIVE 관련 flag 는 기본 `false`
> 이며, 모든 실거래 전환은 별도 PR 과 사용자 명시 승인 후에만 가능합니다.
> 자세한 현재 상태: [`docs/status/current_state.md`](docs/status/current_state.md) (#88).

| 정보 | 값 |
|---|---|
| 프로그램명 (한글) | 에이전트 트레이더 v1 |
| 프로그램명 (영문) | Agent Trader v1 |
| 내부 버전 | 1.0.0 |
| 운영 모드 | 가상 / 모의 / 관제 중심 — 실거래는 별도 승인 전까지 비활성화 |

국내주식 단타 자동매매 연구 플랫폼입니다. 현재 단계의 목표는 **수익 자동화**가 아니라 **검증 가능한 전략 엔진, 리스크 통제, 모의투자/Shadow Mode, AI 에이전트 advisory, 관제 PWA**를 순서대로 구축하는 것입니다.

## 확인 주소

| 환경 | 주소 |
|---|---|
| Local UI | <http://localhost:5173> |
| Local API Docs | <http://127.0.0.1:8000/docs> |
| **GitHub Pages Demo** | <https://1976haru.github.io/autotrade/> |

스마트폰에서 같은 Wi-Fi의 PC dev 서버를 보려면:

```bash
cd frontend
npm run dev -- --host 0.0.0.0
# 접속: http://<PC_IP>:5173 (예: http://192.168.0.49:5173)
```

## 배포 / 접속 / 보안 체크리스트

운영자 / 베타테스터가 단계별로 점검할 수 있는 통합 체크리스트:

- 📋 **[`docs/deployment_checklist.md`](docs/deployment_checklist.md)** — 0단계(목표 확정) ~ 12단계(실거래 전 최종 점검) 연번 체크리스트
- 🌐 [`docs/deployment_strategy.md`](docs/deployment_strategy.md) — 전체 배포 / 운영 정책
- 📱 [`docs/mobile_access_guide.md`](docs/mobile_access_guide.md) — LAN / Tailscale 접속 절차
- 📲 [`docs/pwa_installation.md`](docs/pwa_installation.md) — 스마트폰 홈화면 PWA 설치 + Service Worker 캐시 정책 + 오프라인 제한 (#63)
- 🔔 [`docs/notification_policy.md`](docs/notification_policy.md) — Telegram 알림 설정 + 우선순위 + Secret 관리 + 알림 종류 (#64)
- 🧪 [`docs/staging_environment.md`](docs/staging_environment.md) — docker-compose.staging 실행 가이드 + LIVE flag 금지 정책 + smoke script (#67)
- 📜 [`docs/audit_log_policy.md`](docs/audit_log_policy.md) — 통합 감사 이벤트 facade + append-only 정책 + Secret 거부 + archive (#68)
- 💾 [`docs/backup_restore.md`](docs/backup_restore.md) — DB 백업 + 복구 절차 + Secret 백업 금지 + 일별 retention (#69)
- 📈 [`docs/monitoring_policy.md`](docs/monitoring_policy.md) — 서버 / API / 데이터 / 주문 안정성 모니터링 + 알림 후보 + read-only invariants (#70)
- 🏁 [`docs/mvp_completion.md`](docs/mvp_completion.md) — MVP 완료 판정 + P0 상태표 + Paper/Shadow 진입 조건 (#71)
- 🧾 [`docs/paper_gate_policy.md`](docs/paper_gate_policy.md) — Paper 4주 운용 평가 게이트 + PASS=Live Manual Approval 검토 가능 (실거래 허가 아님) (#72)
- 🪪 [`docs/live_manual_gate.md`](docs/live_manual_gate.md) — Live Manual Approval 진입 readiness 게이트 + 극소액 정책 + Approval API 강제 (실거래 허가 아님) (#73)
- 🤖 [`docs/ai_assist_gate.md`](docs/ai_assist_gate.md) — AI Assist 품질 검증 게이트 + failure reason 태깅 + PASS≠LIVE_AI_EXECUTION 허가 (시스템 검증 자료, 투자 조언 아님) (#74)
- 🤖🔒 [`docs/ai_execution_gate.md`](docs/ai_execution_gate.md) — AI Execution 활성화 readiness 게이트 + 극소액·whitelist·시간 정책 + 선물 영구 차단 + READY_FOR_REVIEW≠실제 활성화 (#75)
- 📉🔒 [`docs/futures_promotion_policy.md`](docs/futures_promotion_policy.md) — 선물 7단계 승격 정책 (Simulation → Paper → Manual → AI Assist) + FUTURES_AI_EXECUTION 영구 BLOCKED + 자동 롤오버 금지 (#76)
- 📊 [`docs/alpha_decay_monitor.md`](docs/alpha_decay_monitor.md) — 전략 알파 감쇠 read-only 모니터 + 단기 부진 vs 구조적 저하 분류 + 자동 비활성 절대 금지 (#77)
- 🔗 [`docs/correlation_guard_policy.md`](docs/correlation_guard_policy.md) — sector/theme 익스포저 사전 검사 + 신규 BUY 집중도 제한 + SELL/EXIT 우회 (#78)
- 🏷️ [`docs/loss_tagging_policy.md`](docs/loss_tagging_policy.md) — 손실 거래 *추정 원인* 자동 태깅 + 7카테고리 25태그 + append+review only (확정 원인 아님) (#79)
- 🚦 [`docs/pre_market_check_policy.md`](docs/pre_market_check_policy.md) — 장 시작 전 자동 점검 + 11카테고리 + 모드별 required + manual ack 비우회 (#80)
- 📚 [`docs/strategy_registry.md`](docs/strategy_registry.md) — 6개 실제 전략 메타데이터 + 초보자용 한글명 + 가짜 전략명 추가 영구 금지 (#81~#83)
- 🧩 [`docs/strategy_signal_aggregator.md`](docs/strategy_signal_aggregator.md) — 4 단타 전략 vote → 종목별 통합 advisory 후보 (#84)
- 🎯 [`docs/strategy_selection_agent.md`](docs/strategy_selection_agent.md) — 시장 상태 + 4 전략 → 최적 조합 선택 + blocked reason (#85)
- 💻 [`docs/desktop_packaging.md`](docs/desktop_packaging.md) — Tauri v2 Windows 설치형 앱 skeleton + backend 자동 실행 (#86)
- 🔄 [`docs/desktop_update_policy.md`](docs/desktop_update_policy.md) — 데스크톱 자동 업데이트 / 서명 키 / 자동 적용 금지 (#86)
- 👶 [`docs/beta_tester_install_guide.md`](docs/beta_tester_install_guide.md) — 초보자용 설치 가이드 (SmartScreen / 진단 / Secret 보호) (#86)
- 📱 [`docs/tailscale_smartphone_access.md`](docs/tailscale_smartphone_access.md) — Tailscale 스마트폰 원격 관제 — 포트포워딩 금지 (#86)
- 🔧 [`docs/first_run_setup_wizard.md`](docs/first_run_setup_wizard.md) — 첫 실행 설정 wizard (skeleton + .env fallback) (#86)
- 📋 [`docs/system_audit_2026_05.md`](docs/system_audit_2026_05.md) — 6 전략 + 전 영역 단일 진실 카탈로그 + 22 invariant (#87)
- 🧹 [`docs/system_hygiene_report.md`](docs/system_hygiene_report.md) — `.gitignore` / workflow / env example / Pages 점검 결과 (#88)
- 📊 [`docs/status/current_state.md`](docs/status/current_state.md) — 현재 main 상태 단일 진실 (#88)
- ✅ [`docs/status/completed_checklist_060_088.md`](docs/status/completed_checklist_060_088.md) — #60~#88 체크리스트 표 (#88)
- ⚠ [`docs/status/known_risks.md`](docs/status/known_risks.md) — 알려진 위험 8 카테고리 (#88)
- 🛣 [`docs/status/next_steps.md`](docs/status/next_steps.md) — P0/P1/P2/P3 우선순위 + 실거래 baseline (#88)
- 📦 [`docs/dependency_policy.md`](docs/dependency_policy.md) — frontend/backend/desktop 의존성 + Paper freeze 정책 (#88)
- 🧪 [`docs/kis_paper_oneclick.md`](docs/kis_paper_oneclick.md) — 한투 모의투자 AI 자동매매 *원클릭* 테스트 (3 모드 / 점수판 / 안전 invariant) (#89)
- 💻 [`docs/desktop_exe_status.md`](docs/desktop_exe_status.md) — Windows installer 빌드 상태 + Rust 툴체인 점검 결과 + **GitHub Actions 자동 빌드 활성화** (#89, #90, 2026-05-15 갱신)
- 🚀 [`docs/exe_oneclick_installation.md`](docs/exe_oneclick_installation.md) — **EXE 원클릭 설치 + 한투 모의 테스트 흐름** — 베타테스터 / 지인 배포 가이드 (#90)
- 🚦 [`docs/pre_market_checklist.md`](docs/pre_market_checklist.md) — Pre-market Checklist 초보자 흐름 (#91)
- 📊 [`docs/release_readiness_policy.md`](docs/release_readiness_policy.md) — Release Readiness Report advisory meta-aggregator (실거래 허가 아님) (#92)
- 🔐 [`docs/security_scan.md`](docs/security_scan.md) — **Secret / 인증서 / 번들 누출 정적 스캐너** + fake placeholder 표준 + CI 자동 회귀 차단 (#93)
- ⏱️ [`docs/alpha_decay.md`](docs/alpha_decay.md) — **신호 단위 알파 감쇠** advisory 분석 (1m~60m bucket) + EXPIRED 신호 신규 진입 금지 안내 (#94, #77 전략 단위와 별개)
- 🔗📊 [`docs/correlation_guard.md`](docs/correlation_guard.md) — **포트폴리오 수익률 상관관계** advisory (Pearson 매트릭스) + BLOCK 시 신규 진입 권고 차단 (#95, #78 sector/theme cap 과 별개)
- 🏷️🧠 [`docs/loss_tagging.md`](docs/loss_tagging.md) — **결정/실행 단계 손실 원인 root cause** 추정 태깅 (16 tag × 5 cat) + AI Agent 학습 자료 (#96, #79 post-trade 와 별개)
- 📦 [`docs/beta_distribution_plan.md`](docs/beta_distribution_plan.md) — 베타테스터 배포 + Tauri / Electron
- 🔄 [`docs/auto_update_plan.md`](docs/auto_update_plan.md) — Phase 1-2-3 단계별 업데이트
- 🔒 [`docs/local_security_policy.md`](docs/local_security_policy.md) — Secret hygiene + Tailscale + 사고 대응

> **15개 절대 원칙 요약** (자세한 내용은 `deployment_checklist.md`):
> 외부 공개 서버 / 포트포워딩 / 운영자 `.env` 공유 / 공개 SaaS / GitHub Pages에 실 데이터 — *모두 금지*.
> 외부 접속은 **Tailscale 우선**, 베타테스터는 *각자 자기 PC*, **LIVE / AI / FUTURES flag는 기본 false**.

## 현재 상태

- `frontend/`: React/Vite 기반 관제 UI. 백엔드 라우트와 실연결.
- `backend/`: FastAPI + SQLAlchemy + Alembic 기반 엔진.
- `docs/`: 운용모드, 승격정책, 브로커 선택, 리스크 정책, **배포 체크리스트** 문서.

## MVP Completion (#71)

- 현재 MVP 판정 문서: [`docs/mvp_completion.md`](docs/mvp_completion.md)
- **MVP 완료는 *실거래 허가가 아닙니다*** — Mock / Paper / Shadow 검증
  단계로 진입할 수 있다는 의미입니다.
- 실거래 전에는 반드시 [`docs/promotion_policy.md`](docs/promotion_policy.md)
  와 [`docs/live_activation_blockers.md`](docs/live_activation_blockers.md)
  의 추가 옵트인 PR / 명시 승인이 필요합니다.
- 현재 판정 자동 요약은
  `python scripts/summarize_mvp_status.py --format markdown` 명령으로
  확인할 수 있습니다 (read-only, .env / API 호출 0건).

## 핵심 원칙

1. AI는 초기 단계에서 주문 API를 직접 호출하지 않습니다.
2. 모든 주문은 `RiskManager`와 `PermissionGate`를 통과해야 합니다.
3. 기본 운용모드는 `SIMULATION` 또는 `PAPER`이며, `LIVE_AI_EXECUTION`은 기본 비활성화입니다.
4. API Key, Secret, 계좌번호는 프론트엔드에 저장하지 않습니다.
5. 선물 기능은 주식 MVP 안정화 이후 별도 모듈로 확장합니다 — 1차 범위·국내/해외선물 비교·실거래 진입 조건은 [`docs/futures_scope.md`](docs/futures_scope.md) 참조.

## MVP 범위

1차 MVP는 **검증 가능한 안전한 자동매매 연구 플랫폼** 구축을 목표로 한다. 아래 표는 위 [핵심 원칙](#핵심-원칙)과 [`docs/promotion_policy.md`](docs/promotion_policy.md)의 단계별 승격 정책을 위배하지 않는다.

### 1차 포함 (In-Scope)

| 영역 | 포함 내용 |
|---|---|
| 거래 대상 | 국내주식 단타 |
| 운용모드 | `SIMULATION`, `PAPER` (KIS 모의투자), `LIVE_SHADOW` (실 시세 read-only) |
| 주문 경로 | MockBroker 주문, KIS 모의투자(Paper) 주문, Virtual(가상자금) 주문까지만 |
| AI | 시세/지표 분석, 매매 후보 제안·판단, **Virtual AI Execution** (가상 자금 한정) |
| 선물 | `FuturesMockBroker` 기반 가상 시뮬레이션까지만 ([`docs/futures_scope.md`](docs/futures_scope.md)) |
| 클라이언트 | React/Vite 기반 **PWA 관제 UI** (Dashboard / 승인 / 백테스트 / 감사로그 등 11개 탭) — 1차 앱 |
| 보안 | API Key·Secret·계좌번호는 backend `.env` 또는 환경변수로만 주입 |

### 1차 제외 (Out-of-Scope)

| 영역 | 제외 사유 / 후속 단계 |
|---|---|
| 실제 broker LIVE 주문 (`KisBrokerAdapter.place_order(is_paper=False)`) | `LIVE_MANUAL_APPROVAL` 라우팅 PR에서 옵트인 후 활성화 |
| `LIVE_AI_EXECUTION` 실제 주문 연결 | 8개 옵트인 조건 모두 충족 시 별도 PR (`promotion_policy.md`) |
| 실제 선물 실거래 (LIVE `FuturesBrokerAdapter`) | 주식 MVP 안정화 이후 별도 모듈로 확장 — 9단계 blocker 체크리스트 [`docs/live_activation_blockers.md`](docs/live_activation_blockers.md) §3.1 + 1차 범위 [`docs/futures_scope.md`](docs/futures_scope.md) |
| 프론트엔드의 API Key / App Secret / 계좌번호 저장·노출 | 절대 원칙 4번에 의해 영구 제외 |
| 네이티브 iOS/Android 앱 | 1차는 PWA 관제 UI로 대체. 네이티브 진입은 MVP 종료 후 재평가 |
| 사용자 승인 없는 무인 LIVE 자동매매 | `PermissionGate` 우회 금지 — 영구 제외 |

이 범위는 [`docs/promotion_policy.md`](docs/promotion_policy.md)와 [`CLAUDE.md`의 "현재 단계"](CLAUDE.md) 단계별 승격 흐름과 일치한다.

## 개발 환경 셋업

### Backend

```bash
cd backend
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm ci
```

## 실행 명령

### 개발 서버

```bash
# backend (port 8000)
cd backend
uvicorn app.main:app --reload

# frontend (port 5173)
cd frontend
npm run dev
```

Backend API 문서: <http://127.0.0.1:8000/docs>
Frontend: <http://localhost:5173> 또는 <http://127.0.0.1:5173>

### 스마트폰에서 PC UI 확인

같은 Wi-Fi 위에서 모바일 폰으로 dev 서버를 보고 싶다면 `--host` 옵션으로 모든
인터페이스를 바인딩한 뒤 PC IP로 접속한다.

```bash
cd frontend
npm run dev -- --host 0.0.0.0
```

접속 주소: `http://<내PC_IP>:5173` (예: `http://192.168.0.10:5173`)

### 백엔드가 꺼져 있어도 빈 화면이 아니다

백엔드 미기동 상태로 프론트만 띄우면 상단에 "백엔드 연결 실패" 배너 + uvicorn
실행 가이드가 표시되고, 각 탭은 빈 데이터 상태로 렌더된다. 탭 내부에서 발생한
runtime error는 ErrorBoundary가 격리해 BottomNav와 다른 탭은 계속 사용할 수
있다.

### 테스트 / 린트 / 빌드

```bash
# backend lint + 단위 테스트
cd backend
ruff check app tests
pytest -q

# frontend lint + 단위 테스트 + 프로덕션 빌드
cd frontend
npm run lint
npm test           # 일회 실행 (CI에서 동일 명령 사용)
npm run test:watch # 개발 중 watch 모드
npm run build
```

### 최소 6개 전략 백테스트 실행 방법

본 명령은 등록된 6개 단타 전략 (`sma_crossover` / `rsi_reversion` /
`vwap_strategy` / `orb_vwap` / `volume_breakout` / `pullback_rebreak`) 을
모두 baseline 백테스트하고, 결과를 `reports/backtest/` 에 JSON / CSV / Markdown
으로 저장한다.

```bash
# 기본 실행 (repo root 에서)
python scripts/run_backtest_all_strategies.py

# 옵션: 기간 / 심볼 / 비용 / 부분 실행
python scripts/run_backtest_all_strategies.py \
    --symbol 005930 \
    --start 2026-01-01 --end 2027-12-31 \
    --commission-bps 15 --tax-bps 23 --slippage-bps 5 \
    --output-dir reports/backtest

python scripts/run_backtest_all_strategies.py \
    --strategies sma_crossover rsi_reversion

# 테스트 + 보안 스캔
python -m pytest backend/tests/test_backtest_all_strategies.py -q
python scripts/security_scan.py
```

산출 파일 3종:
- `reports/backtest/strategy_backtest_summary.json` — 전체 지표 + run_meta
- `reports/backtest/strategy_backtest_ranking.csv` — risk_adjusted_score 순위
- `reports/backtest/strategy_backtest_report.md` — 운영자 검토용 markdown

**본 절차는 실거래가 아닙니다.** broker / OrderExecutor / route_order / KIS
실 API / Anthropic / Telegram 호출 0건. `ENABLE_LIVE_TRADING` /
`ENABLE_AI_EXECUTION` / `KIS_IS_PAPER` 환경변수 변경 0건. 산출 파일은
`.gitignore` 로 커밋 차단. 자세한 정책 / 지표 정의 / 한계 점:
[`docs/backtest_strategy_report.md`](docs/backtest_strategy_report.md).

### 실제 데이터 기반 검증 파이프라인 (Step 3-02 ~ 3-07)

`MockMarketData` 가 아닌 *실제 / 준실제* OHLCV 데이터로 6 전략 baseline +
parameter grid search + walk-forward 과최적화 탐지 + stress test → 통합
paper 후보 export 까지 1회 명령으로 실행 가능. 각 단계 산출물은 다음 단계
CLI 의 input 으로 carry. 모든 단계 metric 은 **표준화된 14 키**
(`app.analytics.metrics`) 사용.

```bash
# 3-02 — 실제 데이터 baseline 백테스트 (CSV → yfinance fallback).
python scripts/run_backtest_real_data.py

# 3-03 — 제한된 parameter grid search (6 전략 × 29 조합).
python scripts/run_parameter_optimization.py

# 3-04 — Walk-forward 검증 (train/validation 분리, OVERFIT_RISK 탐지).
python scripts/run_walk_forward_validation.py \
    --from-paper-config reports/parameter_optimization/paper_candidate_config.json

# 3-05 — Stress test (10 시나리오 × 후보 매트릭스).
python scripts/run_stress_test.py \
    --from-walk-forward reports/walk_forward/walk_forward_summary.json

# 3-07 — Paper 후보 통합 export (모든 단계 통과 후보만 상위 N=2).
python scripts/run_paper_candidate_aggregator.py \
    --from-backtest     reports/backtest_real/real_data_backtest_summary.json \
    --from-optimization reports/parameter_optimization/parameter_optimization_summary.json \
    --from-walk-forward reports/walk_forward/walk_forward_summary.json \
    --from-stress-test  reports/stress_test/stress_test_summary.json
# → reports/strategy_optimization/paper_candidate_config.json
```

각 단계 결과의 모든 JSON 객체는 `is_order_signal=false` /
`auto_apply_allowed=false` / `is_live_authorization=false` invariant. 산출물은
`reports/*` gitignore — git 미커밋. 14 표준 metric 키 정의 + 빈 거래 / 손실 0 /
JSON 직렬화 정책: [`docs/performance_metrics.md`](docs/performance_metrics.md)
(3-06). 단계별 정책:
[`docs/real_data_backtest.md`](docs/real_data_backtest.md) (3-02) /
[`docs/parameter_optimization.md`](docs/parameter_optimization.md) (3-03) /
[`docs/walk_forward_validation.md`](docs/walk_forward_validation.md) (3-04) /
[`docs/stress_test.md`](docs/stress_test.md) (3-05) /
[`docs/paper_candidate_aggregator.md`](docs/paper_candidate_aggregator.md) (3-07) /
[`docs/strategy_optimization_report.md`](docs/strategy_optimization_report.md) (3-08) /
[`docs/agent_strategy_input_schema.md`](docs/agent_strategy_input_schema.md) (4-01) /
[`docs/strategy_combination_recommendation.md`](docs/strategy_combination_recommendation.md) (4-02) /
[`docs/overfit_warning_agent.md`](docs/overfit_warning_agent.md) (4-03).

**Paper 후보 export 는 자동 실거래 활성화가 아닙니다.** `paper_candidate_config.json`
은 운영자 검토 자료 — 검토 후 *수동* 으로 Paper Auto Loop 에 입력. 후보가
없으면 `candidates: []` + `reasons_no_candidate` 채워서 파일 생성 (억지 생성 X).

산출물 (`reports/`, gitignore): per-단계 `*_summary.json` + `*_ranking.csv` +
`*_report.md`. 각 단계 결과의 모든 JSON 객체는
`is_order_signal=false` / `auto_apply_allowed=false` /
`is_live_authorization=false` invariant. 자세한 정책:
[`docs/real_data_backtest.md`](docs/real_data_backtest.md) (3-02),
[`docs/parameter_optimization.md`](docs/parameter_optimization.md) (3-03),
[`docs/walk_forward_validation.md`](docs/walk_forward_validation.md) (3-04).

### DB 마이그레이션 (Alembic)

```bash
cd backend

# 모델 변경 → 새 마이그레이션 생성
alembic revision --autogenerate -m "<설명>"

# 최신으로 업그레이드
alembic upgrade head

# 한 단계 롤백
alembic downgrade -1
```

서버 시작 시 lifespan에서 `alembic upgrade head`를 자동 실행하므로, 일반적인 dev 흐름에서는 마이그레이션 생성만 하면 됩니다.

## 운용모드와 안전 플래그

`backend/.env` (또는 환경변수)에서 설정합니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DEFAULT_MODE` | `SIMULATION` | 운용모드 (SIMULATION / PAPER / LIVE_SHADOW / LIVE_MANUAL_APPROVAL / LIVE_AI_ASSIST / LIVE_AI_EXECUTION) |
| `ENABLE_LIVE_TRADING` | `false` | 실거래 전체 차단 플래그 |
| `ENABLE_AI_EXECUTION` | `false` | AI 자동 실행 차단 플래그 |
| `MARKET_DATA_PROVIDER` | `mock` | 시장 데이터 소스 (`mock` / `yfinance`) |
| `ANTHROPIC_API_KEY` | (비어있음) | 비어 있으면 AI 라우트는 안내 메시지만 반환 |

### Frontend feature flags (#50)

`frontend/.env` (또는 환경변수)에서 설정. UI 노출 전용 — backend safety flag와 별개.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VITE_ENABLE_FUTURES_TAB` | `false` | Futures 탭을 PC TopNav에 노출. 모바일 BottomNav에는 flag=true여도 직접 노출되지 않음 (사용자 혼동 방지). 자세한 정책: [`docs/futures_ui.md`](docs/futures_ui.md) |

**Futures 탭은 기본 비활성**입니다. 실제 선물 주문은 비활성 — 본 UI는 Simulation Only / Read-only 화면입니다.

## CI

- **Backend CI** (`.github/workflows/backend-ci.yml`) — `backend/` 변경시 ruff + pytest
- **Frontend CI** (`.github/workflows/frontend-ci.yml`) — `frontend/` 변경시 eslint + vitest + vite build

`main`, `develop`, `feature/**` 푸시와 `main`/`develop` 대상 PR에서 자동 실행됩니다.

## Pre-commit hooks (선택)

로컬 커밋 시점에 lint와 파일 hygiene 검사를 자동화하려면 한 번만 셋업하세요.

```bash
pip install pre-commit       # 1회
pre-commit install           # 1회 (각 clone마다)

# 모든 파일에 대해 수동 실행
python -m pre_commit run --all-files
```

활성 훅:

- 파일 hygiene — trailing whitespace, EOF, YAML 검사, 1MB 초과 파일 차단, merge conflict marker 검사
- `ruff` (backend Python 변경 시)
- frontend `eslint` (frontend `*.js` / `*.jsx` 변경 시)

테스트는 commit 시 실행되지 않습니다(느림). CI가 동일한 lint + tests를 다시 검증합니다.

## 폴더 구조

```text
auto-trader/
├─ frontend/       # React/Vite 관제 UI
├─ backend/        # FastAPI 엔진 (alembic, db, ai, market, backtest, permission, execution, risk, brokers)
├─ docs/           # 설계/운용/리스크 문서
├─ scripts/        # 보조 스크립트
├─ .github/        # CI workflow
├─ CLAUDE.md       # Claude Code 작업 지침
└─ README.md
```

## 작업 흐름 (브랜치)

| 브랜치 | 역할 |
|---|---|
| `main` | 항상 안정 상태. backend pytest와 frontend build가 통과한 커밋만 허용. 릴리스 가능 기준선. |
| `develop` | 다음 릴리스를 위한 통합 브랜치. 여러 feature를 모은 뒤 검증되면 `main`으로 머지. |
| `feature/<번호>-<짧은-슬러그>` | 단일 기능/버그 단위 작업 브랜치. `develop` 또는 `main` 대상으로 PR 생성. |

CI(`backend-ci.yml` / `frontend-ci.yml`)는 `main`, `develop`, `feature/**` 푸시와 `main` / `develop` 대상 PR에서 자동 실행된다.

- LIVE / LIVE_AI_EXECUTION / 선물 관련 위험 코드는 stub 또는 TODO로 남기고, 별도 옵트인 PR에서 단계별로 구현.
- API Key, App Secret, 계좌번호, `.env` 파일은 어떤 브랜치에도 커밋하지 않는다 (`.gitignore`로 강제).
