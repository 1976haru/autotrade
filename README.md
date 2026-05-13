# 에이전트 트레이더 v1 · Agent Trader v1

> **AI 에이전트가 시장을 분석하고, 사용자는 핵심 판단과 위험만 확인하는 자동매매 관제 시스템**

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
