# Frontend Integration (#59)

본 문서는 **UI 프로토타입 → 실 관제앱 전환**의 표준 정책을 정의한다. Frontend 가 backend FastAPI를 우선으로 사용하고, GitHub Pages처럼 backend가 없는 환경에서는 *demo mode*로 자동 fallback하며, 어떤 경우에도 raw `Failed to fetch` 같은 문구가 사용자에게 노출되지 않는다.

## 1. 단일 API client

모든 backend 호출은 [`frontend/src/services/backend/client.js`](../frontend/src/services/backend/client.js)의 `backendApi` 객체를 통해 흐른다.

- **백엔드 base URL**: `import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000"`.
- **error 변환**: `backendFetch()`가 FastAPI `{detail: ...}`을 운영자 친화 문자열로 정규화 (`formatBackendErrorDetail`). HTTP 에러 시 `Error.status` / `Error.detail` carry — caller가 디버깅에 사용.
- **네트워크 에러 처리**: `Error.message`에는 brand "Failed to fetch" 같은 raw 메시지가 들어있을 수 있으므로, *UI 표시 직전*에 [`utils/errorMessage.js::friendlyErrorMessage`](../frontend/src/utils/errorMessage.js)로 변환.

### 컴포넌트 / hook 규칙
- **금지**: `fetch(...)`를 직접 호출하는 것. 모든 호출은 `backendApi.X()`를 거친다.
  - 신규 endpoint 추가 시 client.js에 method를 먼저 정의 후 hook / 컴포넌트에서 사용.
- **금지**: API key / Secret / 계좌번호를 frontend에 저장. 모든 인증은 backend가 담당.
- **금지**: backend 주문 / risk / broker 로직을 frontend에서 우회. UI는 *관제·승인·설정* 전용.

## 2. 데이터 출처 표시 — `DataSourceBanner` / `DemoModeBadge`

[`components/common/DataSourceBanner.jsx`](../frontend/src/components/common/DataSourceBanner.jsx)가 **데이터 출처를 한 줄로 라벨링**한다. 운영자가 "지금 보는 숫자가 실 backend에서 왔나, mock에서 왔나"를 즉시 인지할 수 있도록.

### 4가지 모드

| 모드 | 의미 | 색상 |
|---|---|---|
| `backend` | 실 FastAPI 응답 정상 수신 | 녹색 |
| `demo` | GitHub Pages — backend 없음 → demo fixture | 보라색 |
| `offline` | 로컬에서 backend가 unreachable | 빨강 |
| `mock-virtual` | VirtualOrder / 가상 주문 ledger 등 명시적 mock 카드 | 노랑 |

### 사용 패턴

**전체 banner** (Dashboard / 주요 탭 상단):
```jsx
import { BackendDataSourceBanner } from "../common/DataSourceBanner";
import { useBackendStatus } from "../../store/useBackendStatus";

const status = useBackendStatus();
return <BackendDataSourceBanner
  loading={status.loading} error={status.error}
  mode="backend"
  hint={status.error ? "본 카드의 일부 숫자는 mock / virtual 또는 빈 상태일 수 있습니다." : ""}
/>;
```

**inline chip** (카드 헤더 옆 작은 배지):
```jsx
import { DemoModeBadge, resolveDataSource } from "../common/DataSourceBanner";

const mode = resolveDataSource({ loading, error, mode: "backend" });
<SectionLabel>오늘 손익</SectionLabel>
<DemoModeBadge mode={mode} />
```

### `resolveDataSource(args)` 분기 규칙
- `mode === "mock-virtual"` → 그대로 `mock-virtual`
- `loading === true` → 입력 `mode` 그대로 (premature offline 안내 회피)
- `error` 있고 `isDemoBuild() === true` → `demo`
- `error` 있고 그 외 → `offline`
- 정상 → `backend`

## 3. Friendly error message — `Failed to fetch` 노출 금지

[`utils/errorMessage.js::friendlyErrorMessage(rawError)`](../frontend/src/utils/errorMessage.js)가 *모든* 에러 표시의 단일 진입점.

| 입력 | 출력 |
|---|---|
| `null` / 빈 문자열 | `null` (caller는 hint 없이 `<ErrorState>`만) |
| `Failed to fetch` / `NetworkError` / `ERR_NETWORK` / `Load failed` | demo build → "GitHub Pages 데모에서는 백엔드가 없어 mock 데이터만 표시됩니다…"; 그 외 → "백엔드 연결이 끊겼습니다. 'uvicorn app.main:app --reload' 실행 후 새로고침" |
| 기타 에러 | 원문 그대로 (backend가 의미 있는 한국어 메시지를 줬을 가능성) |

### 필수 invariant
- `Failed to fetch` / `NetworkError` 원문이 사용자 화면에 노출되지 *않는다* (테스트로 lock).
- raw 에러는 collapsed `<details>`에 한해 디버깅용으로 둘 수 있으나 default 표시 X.

## 4. Loading / Error / Empty state 통일

[`components/common/primitives.jsx`](../frontend/src/components/common/primitives.jsx)의 공용 primitive:
- `<EmptyState icon title hint testId />`
- `<ErrorState icon title hint retryLabel onRetry testId />`
- `<LoadingState icon title hint testId />`

각 카드는 hook의 `{ data, loading, error }` 패턴을 받아:
1. `error` → `<ErrorState hint={friendlyErrorMessage(error)} />`
2. `loading && !data` → `<LoadingState />`
3. `data` 비어 있음 → `<EmptyState />`
4. 정상 → 데이터 카드

## 5. GitHub Pages Demo Mode

GitHub Pages는 FastAPI를 호스팅하지 않으므로 `VITE_DEMO_MODE=true`로 빌드된다 (`.github/workflows/pages-deploy.yml`).

### Build flag
- `VITE_DEMO_MODE=true` → `isDemoBuild() === true`
- `VITE_BASE_PATH=/autotrade/` → Pages serves from `/autotrade/`
- 두 flag 모두 frontend 빌드 시점 결정 — runtime toggle 없음.

### Demo Mode UX
1. **`<BackendOfflineBanner />`** (App level) — 전체 화면용 안내. backend unreachable 감지 시:
   - demo build → `🧪 Demo Mode (GitHub Pages)` 보라색 banner + "실 broker 없음" 안내
   - 로컬 → `⚠ 백엔드 연결 대기 중` 빨강 banner + uvicorn 실행 가이드
2. **`<BackendDataSourceBanner />`** (Dashboard / 카드 level) — 데이터 출처 한 줄 라벨.
3. **`<DemoModeBadge />`** (카드 헤더 inline chip) — mode가 demo / offline / mock-virtual일 때만 표시. backend일 때는 시각 노이즈 줄이려 *null* 반환.
4. **`friendlyErrorMessage`** — demo / 로컬 분기 자동 감지.

### Demo Mode 운영 원칙
- **랜덤 시뮬레이션 결과를 실 거래처럼 표시 금지**: `Math.random` 기반 가짜 PnL / 가짜 체결을 *runtime UI*에 절대 출력하지 않는다 (테스트 fixture에는 OK).
- **mock / virtual fixture 사용 시 명시적 배지**: `mock-virtual` 모드 라벨 또는 카드 자체에 "VirtualOrder 추정" disclaimer.
- **실거래·실주문은 발생하지 않음**: GitHub Pages 환경에서 `VITE_DEMO_MODE=true` + backend 없음 → broker 호출 자체 불가능.

## 6. 로컬 vs 운영 vs Pages 매트릭스

| 환경 | `VITE_DEMO_MODE` | backend | 데이터 출처 banner |
|---|---|---|---|
| 로컬 (uvicorn 실행) | `false` (또는 미정의) | reachable | `backend` (녹색) |
| 로컬 (uvicorn 미실행) | `false` | unreachable | `offline` (빨강) — uvicorn 실행 가이드 |
| GitHub Pages | `true` | unreachable | `demo` (보라) — Pages 안내 |
| 운영 (실 backend) | `false` | reachable | `backend` (녹색) |

## 7. 신규 컴포넌트 / 신규 endpoint 추가 가이드

1. **API method 정의**: `services/backend/client.js`에 `backendApi.X()` 추가. fetch는 `backendFetch()` 통해서만.
2. **hook 작성**: `store/useX.js`에 `{ data, loading, error, refresh }` 패턴 hook.
3. **컴포넌트**: hook 결과를 `loading/error/empty/data` 4분기로 처리. `friendlyErrorMessage(error)`로 hint 변환.
4. **데이터 출처 명시**: 카드가 mock 또는 virtual 데이터일 가능성이 있으면 `<DemoModeBadge mode="mock-virtual" />`.
5. **테스트**: friendly error / loading / empty / data 4분기 + raw "Failed to fetch" 노출 X 검증.

## 8. 한계 / 다음 단계

- **WebSocket / Server-Sent Events**: 현재 frontend는 polling 기반 (`useAdaptivePollingByTopId`). WS 도입은 backend도 같이 작업해야 하며 별도 PR.
- **Service Worker offline cache**: PWA로 만들어 backend 재연결 시 자동 fetch 재시도 — 별도 옵트인.
- **Demo fixture 풍부화**: 현재 demo는 대부분 빈 상태. 운영자 demo / 영업 demo용 deterministic fixture는 별도 PR.
- **i18n**: 모든 안내 문구 한국어 고정. 영어 / 일본어 toggle은 별도 PR.
- **다크 모드**: light theme로 통일됨 (#243). 다크 toggle은 별도 PR.

## 9. 변경 시 동기화

- 신규 endpoint → `client.js` + `frontend_integration.md` §1 method 카탈로그
- 신규 friendly error 패턴 → `errorMessage.js` `_NETWORK_PHRASES` 추가
- 신규 데이터 출처 mode → `DataSourceBanner.jsx::_MODE_PALETTE` + `resolveDataSource`
- backend API contract 변경 → backend PR + frontend client method 동시 갱신

## 관련 문서

- `frontend/src/services/backend/client.js` — 단일 API client
- `frontend/src/components/common/DataSourceBanner.jsx` — 데이터 출처 banner / chip
- `frontend/src/components/BackendOfflineBanner.jsx` — App level 전체 안내
- `frontend/src/utils/errorMessage.js` — friendly error 변환
- `frontend/.env.example` — VITE_BACKEND_URL / VITE_ENABLE_FUTURES_TAB 문서화
- `.github/workflows/pages-deploy.yml` — GitHub Pages 빌드 + auto-sync 워크플로우
- `CLAUDE.md` — 절대 원칙 4-5번 (Secret 미저장, frontend는 관제 전용)
