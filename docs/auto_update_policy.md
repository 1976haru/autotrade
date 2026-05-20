# Auto Update Policy — Agent Trader v1 베타

> Agent Trader v1 의 *자동 업데이트* 동작 정책 + 안전 invariant.
>
> 본 문서는 **A 단계 (UI/UX + release 조회)** 의 정책을 정의한다.
> **B 단계 (Tauri updater plugin 활성화 + 서명 자동 설치)** 는 별도 PR.

## 1. 사용자 관점 시나리오

1. 사용자가 Agent Trader v1 (EXE) 를 실행한다.
2. 앱이 시작될 때 GitHub Releases `latest` 를 1회 조회한다.
3. *현재 버전 < 최신 버전* 이면 Dashboard 상단에 **🆕 새 버전 배너** 노출.
4. 배너에 변경 내용 (release notes) 요약 + **"업데이트 적용"** + **"나중에"** 버튼.
5. A 단계에서 "업데이트 적용" = GitHub Release 다운로드 페이지를 브라우저로
   여는 *fallback*. 사용자가 setup.exe 를 다운로드 → 더블클릭 → 기존 앱 위에
   설치.
6. 설치 완료 후 앱을 *재시작* 하면 최신 코드 반영.
7. 사용자 `.env` (KIS API key / 계좌번호 / Anthropic key 등) 는 *덮어쓰지 않음*.

B 단계에서는 5번 단계가 *원클릭 자동 설치* + 자동 재시작으로 바뀐다.

## 2. 안전 invariant (A/B 양 단계 공통)

- **사용자 `.env` 보존**: 업데이트 패키지가 `%APPDATA%\Autotrade\.env` 같은
  사용자 입력 파일을 *덮어쓰지 않는다*. installer 의 bundle 에 `.env` 어떤
  형태로도 포함 0건 (workflow safety guard 로 검증).
- **실거래 flag 변경 0건**: 업데이트가 `ENABLE_LIVE_TRADING` /
  `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` / `KIS_IS_PAPER`
  default 값을 *변경하지 않는다*. installer 가 들고 다니는 `.env.example` 의
  default 는 항상 안전 값.
- **자동 주문 트리거 0건**: 업데이트 배너의 어떤 버튼도 broker / OrderExecutor /
  route_order 를 호출하지 않는다 ("Place Order" / "매수" / "매도" / "실거래
  시작" 라벨 0개, 테스트로 lock).
- **Secret 노출 0건**: release notes 에 secret 패턴 (`sk-...` / `ghp_...` /
  `Bearer ...` 등) 이 발견되면 frontend 에서 `[REDACTED]` 로 마스킹.

## 3. 현재 단계 (A 단계) 의 구현

### 3-1. Frontend
- `frontend/src/desktop/updaterClient.js`
  - `parseVersion` / `compareVersions` / `isNewer` — SemVer 정렬 정확
  - `sanitizeText` — secret 패턴 redaction
  - `fetchLatestRelease({ owner, repo })` — GitHub REST `releases/latest` 조회
  - `checkForUpdate({ currentVersion })` — UPDATE_AVAILABLE / UP_TO_DATE / FAILED
  - `openUpdateUrl(url)` — `window.open(url, "_blank")` (noopener/noreferrer)

- `frontend/src/components/UpdateBanner.jsx`
  - 4 상태 (IDLE/CHECKING, UP_TO_DATE, UPDATE_AVAILABLE, FAILED)
  - 안전 배지 3종 영구: "사용자 .env 보존" / "실거래 OFF 유지" /
    "주문 기능 아님 · 앱 코드 업데이트만"
  - UPDATE_AVAILABLE 상태에 "재시작 안내" 영구 노출
  - FAILED 시 수동 다운로드 페이지 링크

- `Dashboard.jsx` 상단에 `<UpdateBanner />` carrier — `BackendOfflineBanner`
  보다 위 (베타테스터가 자동 업데이트 알림을 가장 먼저 인지).

### 3-2. Tauri 설정 (변경 없음)
- `src-tauri/tauri.conf.json`:
  - `plugins.updater.active` = **false** (A 단계 유지)
  - `plugins.updater.pubkey` = "" (B 단계 진입 시 채움)
  - `bundle.createUpdaterArtifacts` = **false** (A 단계 유지)
- `Cargo.toml` `tauri-plugin-updater` 의존성 유지 — B 단계에서 즉시 active 전환
  가능.

### 3-3. CI
- `.github/workflows/desktop-release.yml`:
  - `softprops/action-gh-release@v2` 의 `generate_release_notes: true` 가
    GitHub 자동 release notes 생성. 운영자가 release page 에서 수동 편집 가능.
  - `docs/release_notes.md` 의 해당 버전 섹션 내용을 release body 에 *수동
    복사* 권장 (자동 sync 는 후속 PR).
  - `TAURI_PRIVATE_KEY` secret 미존재 — A 단계에서는 updater artifact 생성 X.

## 4. B 단계 (TAURI_PRIVATE_KEY 활성화) 작업 항목

본 PR 에는 *포함되지 않음*. 별도 PR 에서:

1. **서명 키 생성** (로컬, *비밀*):
   ```
   npm install --global @tauri-apps/cli@^2
   tauri signer generate -w ~/.tauri/agent-trader.key
   ```
   - private key (`~/.tauri/agent-trader.key`) — 절대 commit 금지
   - public key — `tauri.conf.json` `plugins.updater.pubkey` 에 commit

2. **GitHub Secret 등록**:
   - `TAURI_PRIVATE_KEY` = private key 파일 내용 (전체)
   - `TAURI_KEY_PASSWORD` = (선택) 키 password

3. **tauri.conf.json** 수정:
   ```json
   "plugins": {
     "updater": {
       "active": true,
       "endpoints": [
         "https://github.com/1976haru/autotrade/releases/latest/download/latest.json"
       ],
       "pubkey": "(public key)"
     }
   },
   "bundle": {
     "createUpdaterArtifacts": true
   }
   ```

4. **workflow** 의 `Tauri build` step `env`:
   ```yaml
   TAURI_SIGNING_PRIVATE_KEY:          ${{ secrets.TAURI_PRIVATE_KEY }}
   TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_KEY_PASSWORD }}
   ```

5. **frontend** `UpdateBanner` 의 "업데이트 적용" 핸들러를 `@tauri-apps/
   plugin-updater` 의 `check().downloadAndInstall()` 로 교체 (분기 — 데스크톱
   모드일 때만).

6. **첫 release 후** `latest.json` artifact 가 GitHub Release 에 첨부됨 →
   다음 버전부터 자동 설치 가능.

자세한 키 관리 정책: [`docs/desktop_update_policy.md`](desktop_update_policy.md).

## 5. SmartScreen 경고

베타 시점에는 코드 서명 인증서가 아직 없을 수 있다. setup.exe 첫 실행 시
Windows SmartScreen 경고가 뜨면 **"추가 정보" → "실행"** 을 클릭. 이 경고는
*악성코드* 의미가 아니라 *Microsoft 가 이 인증서를 본 적이 없다* 의미.

향후 EV 코드 서명 인증서 도입은 별도 PR.

## 6. 실패 시 fallback

`checkForUpdate` 가 실패하면 (network / GitHub rate limit / DNS 등):
- 배너에 노란색 ⚠ 표시 + 에러 메시지
- "다시 시도" 버튼
- "수동 다운로드 페이지 열기" 링크 → `https://github.com/1976haru/autotrade/releases`

사용자는 항상 GitHub Release 페이지에서 setup.exe 를 직접 받을 수 있다.

## 7. 참고

- [`docs/release_notes.md`](release_notes.md) — 버전별 변경 내용
- [`docs/desktop_packaging.md`](desktop_packaging.md) — 패키징 설계
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — 서명 키 관리
- [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) — 베타테스터 가이드
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — EXE 빌드 상태

## 8. Stale popup 방지 정책 (#5-04)

베타테스터가 "최신 버전" 으로 *오인* 할 수 있는 케이스를 *정적 + 동작* 양면
으로 차단한다.

### 8-1. 노출 분리

| 상태 | 표시 컴포넌트 | 트리거 |
|---|---|---|
| 초기 안내 (welcome) | `<ReleaseNotesModal>` | localStorage ack 없음 / 다른 version |
| 릴리스 노트 (release update) | `<ReleaseNotesModal>` | `RELEASE_NOTES[0]` 이 존재할 때 |
| 자동 업데이트 알림 | `<UpdateBanner>` | GitHub Release fetch 결과 `UPDATE_AVAILABLE` |
| 최신 버전 확인 실패 | `<UpdateBanner>` (FAILED state) | fetch 실패 |

### 8-2. 정적 invariant (코드 단)

- `frontend/src/components/UpdateBanner.jsx` 는 `../config/releaseNotes` 를
  **import 하지 않는다**. (`UpdateBanner.test.jsx` 가 소스 grep 으로 lock.)
- `frontend/src/desktop/updaterClient.js` 도 동일. fetch 결과만 carry.
- 따라서 GitHub Release fetch 가 실패해도 `RELEASE_NOTES` / `WELCOME_NOTES`
  의 하드코딩 항목이 *최신 업데이트* 인 척 노출될 수 없다.

### 8-3. 동작 invariant (UI 단)

- FAILED state headline: "ℹ️ 최신 버전 확인 불가" (`update-fail-headline`).
  `Failed to fetch` raw 문자열은 *headline* 에 노출 X — 기술 상세 `<details>`
  안에서만 (`sanitizeText` 통과).
- FAILED state 와 `BackendOfflineBanner` 는 *별개 항목* — banner 안에 명시
  disclaimer (`update-fail-not-backend` testid).
- `<update-release-notes>` 는 *UPDATE_AVAILABLE state* 에서만 렌더 — FAILED /
  UP_TO_DATE 에서는 존재 X.
- "이번 안내 확인" / "이번 버전 공지 확인" 버튼은 `<ReleaseNotesModal>` 만
  소속. `<UpdateBanner>` 에는 없음.

### 8-4. 재팝업 방지

- "이번 안내 확인" 클릭 시 `localStorage::agent-trader-welcome-ack` 에
  `note.version` 저장 → `useReleaseNotesAutoPopup` 이 mount 시 동일 version
  이면 0회 popup.
- 기존 `agent-trader-last-seen-version` (legacy) key 도 backwards compat 으로
  ack 인정.
- 모달의 "닫기" / backdrop click 은 ack 저장 안 함 — 다음 접속 시 동일
  안내가 다시 뜬다 (운영자가 본 PR 시점 의도된 동작 — 사용자가 안내를
  *명시 확인* 하지 않으면 노출 유지).

### 8-5. 시나리오 별 기대 표시

| 시점 / 상태 | 화면 |
|---|---|
| 첫 실행, 새 release 없음 | welcome modal (1회). UpdateBanner = UP_TO_DATE or FAILED |
| 새 release 있음 | UpdateBanner = UPDATE_AVAILABLE + release notes |
| 인터넷 차단 | UpdateBanner = FAILED ("최신 버전 확인 불가"). welcome modal 정상 동작 |
| backend down | BackendOfflineBanner = 별도. UpdateBanner 는 영향 X |
| "이번 안내 확인" 후 재실행 | welcome modal 0회. UpdateBanner 는 무관하게 정상 동작 |
