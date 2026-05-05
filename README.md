# Auto Trader Research Platform

국내주식 단타 자동매매 연구 플랫폼입니다. 현재 단계의 목표는 **수익 자동화**가 아니라 **검증 가능한 전략 엔진, 리스크 통제, 모의투자/Shadow Mode, 관제 PWA**를 순서대로 구축하는 것입니다.

## 현재 상태

- `frontend/`: 기존 ZIP의 React/Vite 기반 UI 프로토타입을 보존했습니다.
- `backend/`: FastAPI 기반 실제 엔진 뼈대를 새로 추가했습니다.
- `docs/`: 운용모드, 승격정책, 브로커 선택, 리스크 정책 문서를 추가했습니다.

## 핵심 원칙

1. AI는 초기 단계에서 주문 API를 직접 호출하지 않습니다.
2. 모든 주문은 `RiskManager`와 `PermissionGate`를 통과해야 합니다.
3. 기본 운용모드는 `SIMULATION` 또는 `PAPER`입니다.
4. API Key, Secret, 계좌번호는 프론트엔드에 저장하지 않습니다.
5. 선물 기능은 주식 MVP 안정화 이후 별도 모듈로 확장합니다.

## 권장 실행 순서

```bash
# backend
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload

# frontend
cd ../frontend
npm install
npm run dev
```

Backend API 문서: `http://127.0.0.1:8000/docs`

## 폴더 구조

```text
auto-trader/
├─ frontend/       # React/Vite 관제 UI
├─ backend/        # FastAPI 자동매매 엔진
├─ docs/           # 설계/운용/리스크 문서
├─ scripts/        # 보조 스크립트
├─ .github/        # CI workflow
├─ CLAUDE.md       # Claude Code 작업 지침
└─ README.md
```

## 다음 작업

1. 프론트의 랜덤 매매 시뮬레이션을 백엔드 MockBroker API로 교체
2. PostgreSQL 스키마 추가
3. 1분봉/5분봉 저장 구조 추가
4. 거래대금 돌파 전략 v1 구현
5. KIS 모의투자 토큰/계좌 조회 연동
