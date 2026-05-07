# Auto Trader Research Platform

국내주식 단타 자동매매 연구 플랫폼입니다. 현재 단계의 목표는 **수익 자동화**가 아니라 **검증 가능한 전략 엔진, 리스크 통제, 모의투자/Shadow Mode, 관제 PWA**를 순서대로 구축하는 것입니다.

## 현재 상태

- `frontend/`: React/Vite 기반 관제 UI. 백엔드 라우트와 실연결.
- `backend/`: FastAPI + SQLAlchemy + Alembic 기반 엔진.
- `docs/`: 운용모드, 승격정책, 브로커 선택, 리스크 정책 문서.

## 핵심 원칙

1. AI는 초기 단계에서 주문 API를 직접 호출하지 않습니다.
2. 모든 주문은 `RiskManager`와 `PermissionGate`를 통과해야 합니다.
3. 기본 운용모드는 `SIMULATION` 또는 `PAPER`이며, `LIVE_AI_EXECUTION`은 기본 비활성화입니다.
4. API Key, Secret, 계좌번호는 프론트엔드에 저장하지 않습니다.
5. 선물 기능은 주식 MVP 안정화 이후 별도 모듈로 확장합니다.

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

- `main`은 항상 안정 상태. backend pytest와 frontend build가 통과하는 커밋만 허용.
- 새 기능은 `feature/<번호>-<짧은-슬러그>` 브랜치에서 작업.
- LIVE / LIVE_AI_EXECUTION / 선물 관련 위험 코드는 stub 또는 TODO로 남기고, 별도 PR에서 단계별로 구현.
