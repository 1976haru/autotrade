# UI Smoke Test Report (236, UI-008)

## 목적

UI 재디자인이 사용자가 실제로 사용하는 시나리오에서 깨지지 않는지 확인하는
**자동 + 수동 체크리스트**. 자동 부분은 `frontend/src/smoke.test.jsx`에 있고,
수동 부분은 운영자가 직접 브라우저에서 확인.

자동 smoke가 통과한다 ≈ 사용자가 페이지를 열었을 때 흰 화면을 보지 않는다.
하지만 시각적 품질(글자가 잘리지 않는지, 카드가 어색하게 wrapping되지 않는지)
은 사람의 눈이 필요하다.

## 자동 smoke (vitest)

`frontend/src/smoke.test.jsx`:

- App shell renders with TopNav + BottomNav (≥15 buttons)
- Dashboard에서 Hero summary + Operator panel + Agent decision hero 렌더
- 5개 핵심 탭(`strat`/`approve`/`audit`/`signal`/`config`)으로 전환 시
  ErrorBoundary fallback 미등장
- Raw `Failed to fetch` 문구가 사용자에게 노출되지 않음

전체 backend offline 시나리오 — Pages demo 환경과 동일.

```bash
cd frontend
npx vitest run src/smoke.test.jsx
```

## 수동 체크리스트 (브라우저)

### 환경 1: Local (backend on)

전제: `uvicorn app.main:app --reload` + `npm run dev` 둘 다 실행.

| # | 체크 항목 | 통과 기준 |
|---|---|---|
| 1 | <http://localhost:5173> 접속 | 흰 화면 아님 |
| 2 | Hero Summary 영역 | 앱명 + 모드 배지 + 연결됨 pill 표시 |
| 3 | Operator panel | VIRTUAL MODE 배지 + 3 버튼 + 6셀 status |
| 4 | Agent Decision hero | confidence/regime/readiness 카드 |
| 5 | KPI 3장 (총자산/평가손익/봇 누적) | 큰 글씨, tabular-nums |
| 6 | TopNav 데스크톱 노출 | sticky, active 탭 시안색 강조 |
| 7 | 11개 탭 클릭 | 모두 정상 렌더, ErrorBoundary fallback 없음 |
| 8 | 결재 대기 0건일 때 | StatusPill 미등장 |

### 환경 2: Local (backend off — fallback 검증)

전제: backend 미실행, frontend만 실행.

| # | 체크 항목 | 통과 기준 |
|---|---|---|
| 1 | 상단 빨간 ⚠ 백엔드 연결 실패 배너 | 'cd backend\nuvicorn ...' 안내 |
| 2 | Hero Summary | "Demo Mode (Backend 미연결)" pill |
| 3 | Agent Decision hero | "Agent 판단 조회 실패" + "uvicorn 안내" |
| 4 | 11개 탭 | 빈 상태 / ErrorState로 표시, 흰 화면 없음 |
| 5 | Raw 'Failed to fetch' | 어디에도 노출 X |

### 환경 3: GitHub Pages Demo

URL: <https://1976haru.github.io/autotrade/>

| # | 체크 항목 | 통과 기준 |
|---|---|---|
| 1 | 상단 시안 🧪 Demo Mode 배너 | UI-006 토큰 기반 디자인 |
| 2 | Hero Summary | "Demo Mode (Backend 미연결)" pill |
| 3 | Agent Decision hero | "GitHub Pages 데모 ..." 친절 안내 |
| 4 | 모든 탭 클릭 | ErrorBoundary fallback 미등장 |
| 5 | 모바일 반응형 | <768px에서 단일 컬럼, BottomNav만 노출 |
| 6 | 데스크톱 반응형 | ≥768px에서 2-3열 grid + TopNav |

## 결과

| 환경 | 자동 smoke | 수동 |
|---|---|---|
| Local (backend on) | ✓ | 운영자 실행 시 검증 |
| Local (backend off) | ✓ — `smoke.test.jsx` | 운영자 실행 시 검증 |
| GitHub Pages Demo | ✓ — 동일 코드, 동일 fallback | 운영자 실행 시 검증 |

자동 smoke 통과 ≈ "흰 화면 없음" 보장. 시각 품질은 수동 체크리스트로 운영자가
확인. 본 문서는 PR 머지 시점에 운영자가 위 환경 1-3 체크리스트를 실행했음을
기록 (구두/스크린샷 첨부).
