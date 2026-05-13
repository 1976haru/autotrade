# PWA 설치 가이드 (체크리스트 #63)

## 1. 목적

React/Vite 프론트엔드를 스마트폰 홈화면에 *설치 가능한* PWA(Progressive Web
App)로 만든다. 별도의 네이티브 앱(Android / iOS) 개발 전까지, 운영자가
스마트폰에서 *관제 대시보드*에 빠르게 접근할 수 있게 한다.

**중요**: 본 PWA는 *모바일 관제용*이며 *실제 매매 앱이 아니다*. 오프라인
상태에서는 주문 / 승인 / 봇 시작 / Kill Switch 토글이 동작하지 않는다는 점이
사용자에게 명시적으로 안내된다.

## 2. 절대 원칙 (CLAUDE.md)

| 원칙 | 강제 위치 |
|---|---|
| Service Worker가 API 응답을 *캐시하지 않는다* | `sw.js`의 `/api/*` 분기 — network-only, 실패 시 503 sentinel JSON. `cache.put` 호출 0건 (테스트로 lock) |
| 계좌 정보 / 주문 정보 / Secret 캐시 0건 | API/WebSocket는 모두 network-only. `caches.put`은 정적 자산(SVG / manifest / HTML hashed assets)에만 적용 |
| 오프라인에서 주문 가능처럼 보이지 *않음* | `OfflineBanner`가 "주문 / 승인 / 봇 시작 / Kill Switch 토글이 동작하지 않습니다" 명시. API 호출은 503으로 회신 |
| 푸시 알림 구현 *금지* | SW에 `push` event listener / `pushManager` / `showNotification` 호출 0건 (정적 grep 테스트로 lock) |
| Background Sync / Periodic Sync 0건 | `registerSync` / `registerPeriodicSync` 호출 0건 (테스트로 lock) |
| 실제 broker API 호출 0건 | 본 PR은 frontend 정적 자산 + UI 컴포넌트만. backend 변경 0건 |
| LIVE flag 토글 / Secret 입력 UI 0건 | PWA install hint / offline banner 모두 display only |

## 3. 추가된 파일

### 정적 자산 (`frontend/public/`)
- `manifest.webmanifest` — name / short_name / start_url=./ / scope=./ /
  display=standalone / theme_color=#0ea5e9 / background_color=#f0f9ff /
  categories=[finance, productivity] / icons 3개 (192, 512, maskable-512)
- `sw.js` — Service Worker (network-only API + stale-while-revalidate
  static + offline.html fallback)
- `offline.html` — Navigation 실패 시 표시할 친화 오프라인 안내
- `favicon.svg` — 32x32 SVG (브랜드 컬러)
- `icons/icon-192.svg` / `icon-512.svg` / `maskable-512.svg`

### Frontend 코드
- `src/registerServiceWorker.js` — SW 등록 helper (실패해도 앱 무영향)
- `src/components/common/OfflineBanner.jsx` — navigator.onLine 감시 + 액션 비활성 안내
- `src/components/common/PwaInstallHint.jsx` — beforeinstallprompt 처리 + iOS Safari fallback
- `src/main.jsx` — `registerServiceWorker()` 호출 추가 (실패 시 noop)
- `src/App.jsx` — `<OfflineBanner />` + `<PwaInstallHint />` 마운트
- `index.html` — `<link rel="manifest">`, theme-color, apple-touch-icon, apple-mobile-web-app meta

### 테스트
- `src/registerServiceWorker.test.js` — 11건 (SSR 안전 / 등록 경로 / scope / 실패 시 null)
- `src/components/common/OfflineBanner.test.jsx` — 7건 (online/offline toggle / 비활성 안내 / 푸시 부재)
- `src/components/common/PwaInstallHint.test.jsx` — 9건 (standalone / iOS / beforeinstallprompt / dismiss / invariant)
- `src/pwa-assets.test.js` — 13건 (manifest / sw.js / offline.html / index.html 정적 검증)

## 4. Manifest 내용

```jsonc
{
  "name": "AI 자동매매 관제 (Agent Trader)",
  "short_name": "AI자동매매",
  "description": "AI 에이전트 기반 자동매매 관제 대시보드. 가상/모의 환경 + 운영자 승인 흐름 — 실시간 주문 기능은 백엔드 연결이 필요합니다.",
  "start_url": "./",
  "scope":     "./",
  "display":   "standalone",
  "orientation": "portrait-primary",
  "theme_color":      "#0ea5e9",
  "background_color": "#f0f9ff",
  "lang": "ko",
  "categories": ["finance", "productivity"],
  "icons": [
    { "src": "icons/icon-192.svg",    "sizes": "192x192", "type": "image/svg+xml", "purpose": "any" },
    { "src": "icons/icon-512.svg",    "sizes": "512x512", "type": "image/svg+xml", "purpose": "any" },
    { "src": "icons/maskable-512.svg","sizes": "512x512", "type": "image/svg+xml", "purpose": "maskable" }
  ]
}
```

`start_url`과 `scope`를 `./`로 두어 GitHub Pages(`/autotrade/`)와 로컬 dev(`/`)
양쪽 모두에서 작동한다.

## 5. Service Worker 캐시 정책

| 요청 종류 | 정책 | 캐시 |
|---|---|---|
| `/api/*` GET | network-only | **0건** (캐시 절대 안 함) |
| `/api/*` POST / PUT / DELETE | network-only, 실패 시 503 JSON | 0건 |
| WebSocket (`upgrade: websocket`) | 통과 (SW가 가로채지 않음) | 0건 |
| Navigation (HTML) | network-first, 실패 시 `offline.html` | HTML 자체 0건 |
| 동일 origin 정적 자산 (JS/CSS/SVG/manifest) | stale-while-revalidate | runtime cache OK |
| Cross-origin | 통과 | 0건 |

**API 응답을 캐시하지 않는다**는 invariant는 `pwa-assets.test.js`가 정적 grep으로
강제한다. `cache.put`이 `isApiRequest` 분기 내에서 호출되면 테스트가 실패.

캐시 버전은 `sw.js`의 `SW_VERSION = "agent-trader-v1"` 상수로 관리한다. 캐시
구조가 바뀌면 이 값을 올린다 — `activate` 핸들러가 이전 버전 캐시를 정리한다.

## 6. 모바일 설치 방법

### Android Chrome
1. 스마트폰 Chrome에서 `https://1976haru.github.io/autotrade/` 또는 로컬
   네트워크의 `http://<PC-IP>:5173/` 접속.
2. 우상단 메뉴(⋮) → **"앱 설치"** 또는 **"홈 화면에 추가"**
3. 또는 화면 하단 `PwaInstallHint` 카드의 **"➕ 홈화면에 추가"** 버튼.

### iOS Safari
1. 스마트폰 Safari에서 사이트 접속 (Chrome iOS는 SW가 제한적이라 Safari 권장).
2. 하단 **공유 버튼** → **"홈 화면에 추가"** → "추가".
3. iOS는 `beforeinstallprompt`를 지원하지 않으므로 `PwaInstallHint`는
   안내 텍스트만 표시 (자동 install 버튼 없음).

### Desktop Chrome (참고)
- 주소창 우측 ⊕ 아이콘 → "설치"
- standalone 윈도우로 분리되어 작업표시줄 / Dock에 고정 가능

## 7. 오프라인 제한 사항

오프라인 상태(`navigator.onLine === false`) 또는 backend unreachable일 때:

- **표시되는 것**: 마지막 로드된 정적 자산(HTML / JS / CSS / 아이콘) + cached SVG
- **표시되지 않는 것**: 실시간 계좌 / 미체결 / 포지션 / Agent 결정 / Backtest
  결과 — 모두 `/api/*` 요청으로 가져오는데 SW가 503 JSON으로 회신
- **버튼 동작**:
  - ✗ 주문 / 승인 / 거부 / 취소 — backend로 도달하지 못해 실패
  - ✗ 봇 시작 / 정지 — 동일
  - ✗ Kill Switch (LEVEL 1/2/3 토글, 해제) — 동일
  - ✓ 탭 전환 / 캐시된 UI 보기 — 동작

`OfflineBanner`가 이 사실을 사용자에게 항상 명시한다. `offline.html`에는 동일
경고 + "확인해 보세요" 체크리스트 + "다시 시도" 버튼이 있다.

## 8. 푸시 알림 부재

본 PR은 **푸시 알림을 구현하지 않는다**. 이유:
- 푸시 알림은 서버 키(VAPID) 보관 + 사용자 endpoint 영구화 + 권한 모델 검토가
  필요해 별도 보안 절차가 필수.
- 운영자 분기에 따라 모바일 push 트리거가 LIVE 주문에 연동되는 사고 가능성이
  있어 보수적으로 차단.

`OfflineBanner` / `PwaInstallHint` / `offline.html` 모두 "푸시 알림은 보안
검토 후 별도 제공 예정" 안내를 노출한다.

SW 코드에는 `push` event listener / `pushManager` / `showNotification` 호출이
*0건*이며, 정적 grep 테스트(`pwa-assets.test.js`)가 lock한다.

## 9. 모바일 주요 화면 사용성

PWA standalone 모드에서 다음 화면이 정상 작동:
- **Dashboard** — 핵심 요약 + Agent 결정 hero + risk pin
- **Approvals** — 결재 큐 + 처리 내역 (#61에서 모바일 stack 레이아웃 정비)
- **Strategy/Risk** — 정책 + Emergency Stop history (`KillSwitchPanel` read-only)
- **Settings** — 운영 환경 / 모드 표시

큰 audit log / 전체 backtest table / Futures detail은 화면이 좁아 PC 우선이지만,
탭 진입 자체는 가능. 큰 UI 개편은 후속.

## 10. SW 등록 흐름

`main.jsx`가 React 마운트 직후 `registerServiceWorker()` 호출:
```js
import { registerServiceWorker } from './registerServiceWorker.js'
// ...
createRoot(...).render(...)
registerServiceWorker()
```

`registerServiceWorker`는:
1. SSR / 비-브라우저 환경 → null 반환 (noop)
2. file:// 등 비-HTTP 프로토콜 → null
3. `navigator.serviceWorker.register(BASE_URL + 'sw.js', { scope: BASE_URL })`
4. 실패 시 `console.warn` + `onError` 콜백 + null 반환 (앱은 계속 작동)

`BASE_URL`은 vite의 `import.meta.env.BASE_URL`. GitHub Pages 빌드는
`/autotrade/`, 로컬 dev는 `/`로 자동 분기.

## 11. 검증 (Lighthouse / DevTools)

브라우저에서 수동 검증:

| 검증 항목 | 도구 | 기대 |
|---|---|---|
| Manifest detected | DevTools → Application → Manifest | name / short_name / icons 표시 |
| Service Worker registered | DevTools → Application → Service Workers | `Status: activated and is running` |
| Installable | Chrome 주소창 ⊕ 아이콘 노출 | 표시됨 |
| Cache contents | DevTools → Application → Cache Storage | `agent-trader-v1-precache` 와 `-runtime` 만, API 응답 0건 |
| Lighthouse PWA 점수 | DevTools → Lighthouse | Installable + Service Worker + Manifest 모두 PASS |

자동 Lighthouse 통합은 CI 후속.

## 12. 남은 PWA backlog

본 PR 범위 밖 (후속 옵트인):
- **PNG 아이콘**: 현재 SVG로 충분하지만 Android adaptive icon이 일부 launcher에서
  PNG를 더 안정적으로 표시. 빌드 스텝에서 SVG→PNG 변환 (예: sharp / resvg)
  추가 검토.
- **iOS splash screens**: iOS PWA는 launch 시 흰 화면 — `apple-touch-startup-image`
  세트(아이폰별 해상도) 추가 검토.
- **버전 업데이트 prompt**: SW가 새 버전을 받았을 때 사용자에게 "새 버전 사용
  가능" 토스트 + skipWaiting. 본 PR은 silent activate.
- **백엔드 health-check 정기 체크**: navigator.onLine 외에 backend ping으로
  "backend 다운이지만 네트워크 ON" 케이스 분리 — 현재는 `BackendOfflineBanner`
  (#214)가 별도로 처리.
- **Lighthouse CI**: PWA 점수 회귀 방지.
- **로그인 / 인증** (PWA 한정 아닌 영역): 본 PWA가 신뢰 네트워크 가정인 동안은
  미적용.
- **Push 알림**: 별도 보안 검토 PR. VAPID 키 관리 / endpoint 보관 정책 /
  운영자 토글 UX.

## 13. 절대 invariant (변경 금지)

1. SW가 `/api/*` 응답을 캐시하지 않는다 — `cache.put` 호출이 isApiRequest 분기
   안에 등장하면 즉시 lint/test 실패.
2. SW에 `push` event / `pushManager` / `showNotification` / `registerSync` /
   `registerPeriodicSync` 호출 0건 — 정적 grep 테스트로 lock.
3. `OfflineBanner`는 "주문 / 승인 / 봇 시작 / Kill Switch 토글이 동작하지
   않습니다" 문구를 *항상* 노출 (테스트로 lock).
4. `PwaInstallHint`는 LIVE 주문 / 푸시 활성 라벨을 *노출하지 않는다* (테스트로
   lock — "즉시 매수" / "Place Order" / "알림 켜기" 모두 0건).
5. SW 등록 실패가 앱 mount를 중단시키지 *않는다* — `registerServiceWorker`는
   try/catch + 항상 null 반환.
6. `manifest.webmanifest`는 PWA installability 핵심 키(name / start_url /
   display / icons 192+512) 미포함 시 lint/test 실패.
7. backend 코드 / `.env` / KIS key / Anthropic key 변경 0건.

## 14. 관련 PR / 체크리스트

- #214 GitHub Pages Demo Mode (BackendOfflineBanner)
- #222 responsive PC/mobile layout
- #50 Futures UI hidden by default
- #61 Approval UI 핵심 구조화
- #62 Risk Control Panel
- #63 PWA installation (본 PR)
