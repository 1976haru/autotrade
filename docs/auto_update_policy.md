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

## 9. GitHub Release 연동 contract (#5-05)

본 절은 *어떤 GitHub Release artifact 가 UpdateBanner 의 진실인가* 를 명시한다.

### 9-1. 발행 (CI 측, workflow)

`.github/workflows/desktop-release.yml` 가 *수동 trigger only* 로 실행되며,
다음 invariant 를 준수한다 — `test_repository_hygiene.py` 가 정적으로 lock:

- `workflow_dispatch` 트리거만 사용 (자동 push/tag/schedule 0건)
- runner = `windows-latest` 한정
- `inputs.release_tag` 는 sanitize step (SemVer 정규식) 통과 후만 후속 step
  에 흐른다. 비-SemVer 입력은 즉시 fail-fast.
- artifact 업로드 경로 = `src-tauri/target/release/bundle/nsis/*-setup.exe` *만*
- `actions/upload-artifact@v4` + `if-no-files-found: error` 필수 — 빈 결과
  허용 0건.
- `create_release=true` 일 때만 `softprops/action-gh-release@v2` 가 *같은*
  setup.exe 파일을 GitHub Release **draft** 에 첨부. 다른 file 패턴 0건.
- `inputs.draft=true` 기본값 — 운영자가 GitHub UI 에서 *수동 publish*.

artifact 안전 가드:
- `.env` / `*.key` / `*.pem` / `*.p12` / `*.pfx` / `*.crt` / `*.cer` /
  `*.keystore` / `*.jks` 파일이 bundle 에 포함되면 PowerShell safety step
  이 *FATAL* 로 빌드 차단.
- secret 패턴 (`sk-...` / `ghp_...` / `Bearer ...`) 도 release notes /
  workflow source 자체에서 검출되면 차단.

### 9-2. 소비 (앱 측, UpdateBanner)

`frontend/src/desktop/updaterClient.js` 의 `fetchLatestRelease` 가 다음 URL
을 *유일한 진실* 로 사용:

```
https://api.github.com/repos/1976haru/autotrade/releases/latest
```

응답에서 추출되는 필드:
- `tag_name` → `result.latestVersion`
- `html_url` → `result.releaseUrl` (release 페이지)
- `body` → `result.releaseNotes` (sanitize 통과)
- `assets[].browser_download_url` → `result.setupExeAsset.downloadUrl`
  (이름이 `*-setup.exe` 인 첫 asset 우선)

UpdateBanner UI 매핑:
- **UPDATE_AVAILABLE** 상태에서 `setupExeAsset.downloadUrl` 이 있으면 카드
  하단에 **"setup.exe 직접 받기"** `<a download>` 링크 노출 (testid
  `link-setup-exe-direct`, `target=_blank` + `rel="noopener noreferrer"`).
  asset 이 없으면 본 링크 0건 — release 페이지 버튼 (`btn-update-apply`)
  만으로 fallback.
- **FAILED** 상태에서는 `link-manual-download` 가 release 페이지
  (`/releases`) 로 안내. 직접 setup.exe 링크 없음 — 운영자가 release 페이지
  에서 직접 확인.
- **UP_TO_DATE** 상태에서는 어떤 download 링크도 노출 X.

### 9-3. GitHub Release 가 없거나 fetch 실패할 때

- updaterClient → `{ state: FAILED, error }` 반환.
- UpdateBanner → headline `"ℹ️ 최신 버전 확인 불가"` 노출. `Failed to fetch`
  raw 는 *기술 상세* details 안에서만, `sanitizeText` 통과.
- 본 상태는 **backend offline 과 별개 항목** — `update-fail-not-backend`
  배너에 명시. 같은 시점에 backend 도 down 이면 `BackendOfflineBanner` 가
  *별도로* 노출.
- 자동 release / 더미 release / stale 노출 0건 — `UpdateBanner.jsx` 가
  `../config/releaseNotes` 를 import 하지 않음을 정적 grep 으로 lock (§8).

### 9-4. PR 머지 후 단계 (운영자)

본 PR 시점에는 desktop-release workflow 를 *실행하지 않는다*. 머지 후 운영자가:

1. GitHub Actions 탭 → `desktop-release` workflow → **Run workflow** 클릭.
2. `release_tag` 에 SemVer 입력 (예: `v1.0.1-beta.1`).
3. `draft=true` / `create_release=true` 권장.
4. 빌드 완료 후 Actions 페이지 *Artifacts* + GitHub Releases (draft) 에서
   setup.exe 확인.
5. release 페이지에서 운영자가 *수동 publish* → UpdateBanner 가 다음 사용자
   접속 시 UPDATE_AVAILABLE 노출.

## 10. Phase 3 (auto-install) 준비 체크리스트 (#5-06)

본 절은 *후속 PR* (별도 옵트인) 에서 자동 *설치* 까지 가능한 Phase 3 으로
전환하기 위한 사전 준비를 정의한다. 본 PR 시점에는 **어떤 step 도 실행하지
않는다** — 본 문서가 그 절차를 *기록* 만 한다.

### 10-0. 용어 정리

| 용어 | 의미 |
|---|---|
| **Phase 1** | 수동 다운로드 — 사용자가 GitHub Release 에서 setup.exe 받음 (현 PR 까지의 상태) |
| **Phase 2** | UpdateBanner 가 GitHub Release `latest` 조회 + 변경 안내 + 직접 다운로드 링크. 자동 설치 0건 (현 PR 의 *현재* 동작) |
| **Phase 3** | Tauri updater plugin 활성화 + 서명된 latest.json + `downloadAndInstall()` + relaunch |
| **public key** | `tauri signer generate` 가 만드는 *공개* 키 — `tauri.conf.json::plugins.updater.pubkey` 에 commit. 사용자 앱이 latest.json 의 서명을 *검증* 할 때 사용 |
| **private key** | 같은 명령이 만드는 *비밀* 키 — *절대 repo 에 commit 금지*. GitHub Secret 으로만 사용. workflow 의 Tauri build 가 latest.json 에 *서명* 할 때 사용 |
| **latest.json** | Tauri updater manifest. version / platforms.windows-x86_64.{url, signature} 포함. GitHub Release 에 `releases/latest/download/latest.json` 으로 첨부 |

### 10-1. 자동 설치 동작 흐름 (Phase 3 활성화 후)

```
앱 실행
  └─ UpdateBanner mount
       └─ check() — tauri-plugin-updater 가 endpoints[0] 의 latest.json 조회
            └─ latest.json 서명을 public key 로 검증
                 ├─ 검증 실패 → state=FAILED ("최신 버전 확인 불가") — 수동 fallback
                 └─ 검증 성공 + current < latest
                      └─ UPDATE_AVAILABLE 표시 + "업데이트 적용" 버튼
                           └─ 사용자 클릭 → downloadAndInstall()
                                ├─ 다운로드 (NSIS setup.exe + 서명)
                                ├─ 서명 재검증
                                ├─ 설치 (앱 종료 → 새 버전 설치 → relaunch)
                                └─ 사용자 .env (%APPDATA%\Autotrade\.env) *보존*
```

실패 시 (서명 검증 실패 / 다운로드 실패 / 설치 권한 부족 등) 어떤 단계에서
멈춰도 **수동 다운로드 fallback 은 항상 유지** — release 페이지 링크 +
"setup.exe 직접 받기" anchor 가 그대로 노출.

### 10-2. 8단계 활성화 절차 (운영자 수동 — 별도 PR)

#### Step 1 — `tauri signer generate` 로 키 쌍 생성

운영자 로컬 PC 에서 (절대 CI / 공개 서버에서 실행 금지):

```powershell
# Tauri CLI v2 가 이미 설치돼 있어야 함 (`cargo install tauri-cli --version "^2" --locked`)
tauri signer generate -w "$env:USERPROFILE\.tauri\agent-trader.key"
# 출력:
#   Your secret key was written to ...\.tauri\agent-trader.key
#   Your public key was written to  ...\.tauri\agent-trader.key.pub
```

산출:
- `agent-trader.key` — **private key** (절대 외부 노출 금지)
- `agent-trader.key.pub` — **public key** (commit 대상)

> **password 옵션**: `tauri signer generate -p` 로 password 보호도 가능.
> 사용 시 `TAURI_KEY_PASSWORD` GitHub Secret 도 함께 등록.

#### Step 2 — public key 를 `tauri.conf.json` 에 commit

`src-tauri/tauri.conf.json` 의 `plugins.updater.pubkey` 를 빈 문자열에서
public key 내용으로 교체. 본 step 은 *별도 PR* 의 코드 변경 — 본 PR 에서는
빈 값 유지.

```json
"plugins": {
  "updater": {
    "active": true,                                  // ← Step 2 에서 true 전환
    "endpoints": [
      "https://github.com/1976haru/autotrade/releases/latest/download/latest.json"
    ],
    "pubkey": "dW50cnVzdGVkIGNvbW1lbnQ6IG..."        // ← Step 1 에서 받은 public key
  }
}
```

`bundle.createUpdaterArtifacts` 도 같은 PR 에서 `true` 로 전환:

```json
"bundle": {
  "createUpdaterArtifacts": true                     // ← Step 2 에서 true 전환
}
```

#### Step 3 — private key 를 GitHub Secret 에 등록

GitHub repo Settings → Secrets and variables → Actions → **New repository
secret**:

| Name | Value |
|---|---|
| `TAURI_PRIVATE_KEY` | `agent-trader.key` 파일의 *전체* 내용 (Base64 + comment 헤더 포함) |
| `TAURI_KEY_PASSWORD` | (선택) password 보호 사용 시 비밀번호 |

> `TAURI_PRIVATE_KEY` Secret 이 등록되지 않은 상태로 workflow 를 돌리면
> `cargo tauri build` 가 createUpdaterArtifacts=true 인 경우 *서명 step 에서
> 실패* 한다. 그래서 본 PR 시점에는 `createUpdaterArtifacts=false` 를
> 유지해 미등록 상태에서도 setup.exe 빌드는 계속 가능하다.

#### Step 4 — `desktop-release.yml` 의 Tauri build step `env` 연결

본 PR 시점에는 Tauri build step `env:` 의 signing 관련 줄이 *주석 처리* 되어
있다 (`#` 시작). Step 4 PR 에서 이 줄들의 `#` 만 제거:

```yaml
- name: Tauri build
  env:
    TAURI_SIGNING_PRIVATE_KEY:          ${{ secrets.TAURI_PRIVATE_KEY }}
    TAURI_SIGNING_PRIVATE_KEY_PASSWORD: ${{ secrets.TAURI_KEY_PASSWORD }}
    NODE_ENV: production
  working-directory: src-tauri
  run: cargo tauri build
```

Step 4 PR 머지 후 첫 workflow 실행에서 `latest.json` 이 NSIS setup.exe 옆에
함께 생성된다. workflow 의 upload-artifact + softprops/action-gh-release
`files:` 패턴에도 `latest.json` 을 별도 줄로 추가해야 GitHub Release 에
첨부된다 — Step 4 PR 의 핵심 변경.

#### Step 5 — `createUpdaterArtifacts=true` 전환

Step 2 PR 에 이미 포함된 변경. 본 step 은 *분리 PR* 로 진행해도 됨 — Step
2-5 는 같은 PR 에 묶어도 무방하지만 운영자가 단계별로 검증하고 싶으면
독립 PR.

#### Step 6 — `UpdateBanner` 의 "업데이트 적용" 핸들러 교체

`frontend/src/components/UpdateBanner.jsx` 의 `onApply` 가 현재는
`openImpl(url)` 로 release 페이지만 연다. Phase 3 에서는:

```js
import { check } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

async function onApply() {
  const update = await check();
  if (update?.available) {
    await update.downloadAndInstall();
    await relaunch();   // ← Step 7
  }
}
```

* desktop runtime (Tauri) 에서만 실행. 브라우저 / GitHub Pages demo 에서는
  기존 `openImpl(url)` fallback.
* `@tauri-apps/plugin-updater` + `@tauri-apps/plugin-process` 를
  `frontend/package.json` 에 의존성 추가 필요.

#### Step 7 — `relaunch()` 호출

`@tauri-apps/plugin-process::relaunch()` 가 앱을 종료하고 *새 설치된 버전*
으로 자동 재시작. 사용자 `.env` 는 `%APPDATA%\Autotrade\` 에 그대로 남아
있으므로 다시 같은 모의투자 API 키로 시작.

#### Step 8 — 실패 시 수동 다운로드 fallback 유지

Phase 3 활성화 후에도 다음 fallback 은 *그대로* 유지:
- **FAILED state** → "최신 버전 확인 불가" + release 페이지 링크.
- **UPDATE_AVAILABLE 에서 자동 설치 실패** → 현재 PR 의 "setup.exe 직접 받기"
  anchor 가 그대로 작동. 사용자가 수동으로 받아 더블클릭.
- **검증 실패** (서명 검증 실패) → Tauri 가 install 을 *거부*. UpdateBanner
  는 FAILED 처럼 표시 + release 페이지 안내.

### 10-3. Phase 3 활성화 전 체크리스트 (운영자 수동)

전환 *직전* 에 다음을 모두 통과해야 한다 — 누락 시 자동 업데이트가 망가져서
복구가 어렵다 (사용자 PC 에 깨진 latest.json 이 캐시될 수 있음).

| # | 항목 | 확인 방법 |
|---|---|---|
| 1 | `tauri signer generate` 가 만든 키 쌍을 운영자 PC 안전 저장소 (1Password / Bitwarden / encrypted vault) 에 백업 | 운영자 수동 |
| 2 | `TAURI_PRIVATE_KEY` GitHub Secret 이 등록됨 | repo Settings → Secrets 확인 |
| 3 | `tauri.conf.json::plugins.updater.pubkey` 값이 1번 키 쌍의 public key 와 *완전히 일치* | base64 diff |
| 4 | workflow 의 Tauri build step `env` 에 signing 줄이 활성화됨 | workflow 실행 로그에 "Signing artifact" 등장 |
| 5 | 첫 Phase 3 release 빌드가 `latest.json` 을 GitHub Release 에 첨부 | release 페이지에서 `latest.json` 다운로드 + JSON 파싱 |
| 6 | `latest.json` 의 `platforms.windows-x86_64.signature` 가 빈 값이 아님 | jq 또는 수동 확인 |
| 7 | 이전 Phase 2 사용자 (현재 운영자가 배포한 EXE 사용자) 가 *수동* 으로 새 Phase 3 setup.exe 를 받아 한 번 설치해야 다음부터 자동 업데이트 시작 | 운영자 공지 |
| 8 | 자동 업데이트 실패 시 운영자가 즉시 `active=false` 로 되돌릴 수 있는 hotfix PR draft 준비 | 별도 branch 에 revert PR 보관 |

### 10-4. .env / Secret / 사용자 데이터 보존 invariant

Phase 3 활성화 후에도 다음은 *변경되지 않는다*:

- 사용자 `.env` (`%APPDATA%\Autotrade\.env`) 는 *덮어쓰기 0건*. 새 버전이
  설치되어도 KIS 모의투자 키 / 계좌번호 / Anthropic 키 등 사용자 입력은 그대로.
- 안전 flag default (`KIS_IS_PAPER=true`, `ENABLE_LIVE_TRADING=false` 등) 는
  setup.exe 안의 `.env.example` 에만 존재 — 사용자 `.env` 와 별도 파일.
- 자동 업데이트로 broker / OrderExecutor / route_order 코드가 추가되어도
  *RiskManager 다층 가드* 는 항상 작동 (CLAUDE.md 절대 원칙).

### 10-5. 본 PR (#5-06) 의 범위 — *문서 / 테스트만*

본 PR 은 위 8단계를 *어느 것도 실행하지 않는다*. 본 PR 의 코드 변경은:

- `tauri.conf.json` 의 `active=false` / `createUpdaterArtifacts=false` /
  `pubkey=""` *유지* (변경 0건).
- `desktop-release.yml` 의 `TAURI_SIGNING_PRIVATE_KEY` env 줄 *주석 유지*
  (코멘트 안에만 존재 — actual env 0건).
- 신규 `test_repository_hygiene.py` 정적 가드 — 위 invariant 가 향후 PR
  에서 *실수로 켜지지 않도록* lock.
- 본 §10 문서 신설.

Phase 3 활성화는 운영자가 위 체크리스트 통과 + 별도 옵트인 PR 후에만 가능.

## 11. 사용자 `.env` 보존 정책 (#5-07)

본 절은 *업데이트 / 재설치 / 앱 제거* 흐름에서 사용자의 KIS 모의투자 키 /
계좌번호 / 로그 / 포트 캐시가 어떻게 보존되는지를 *단일 진실* 로 정리한다.
운영자가 키를 다시 채울 일이 없도록 모든 단계에서 사용자 입력은 *건드리지
않는다* — 본 정책은 정적 테스트로 lock.

### 11-1. `.env` 표준 경로

| 항목 | 경로 |
|---|---|
| 운영자가 KIS 키를 입력하는 *유일한* 파일 | `%APPDATA%\Autotrade\.env` |
| 풀 경로 예시 | `C:\Users\<사용자명>\AppData\Roaming\Autotrade\.env` |
| backend sidecar 로그 | `%APPDATA%\Autotrade\logs\backend-YYYYMMDD.log` |
| 포트 캐시 (재생성 가능) | `%APPDATA%\Autotrade\backend-port.json` |

탐색기에서 주소창에 `%APPDATA%\Autotrade\` 를 그대로 붙여넣으면 위 폴더로
바로 이동한다 — 환경변수 치환이 자동.

### 11-2. 보존되는 흐름 매트릭스

| 동작 | `.env` | `logs\` | `backend-port.json` |
|---|---|---|---|
| **앱 업데이트 (Phase 1/2: 새 setup.exe 수동 설치)** | ✅ 그대로 | ✅ 그대로 | ✅ 그대로 (또는 다음 실행 시 자동 갱신) |
| **앱 업데이트 (Phase 3: downloadAndInstall + relaunch)** | ✅ 그대로 | ✅ 그대로 | ✅ 다음 실행 시 자동 갱신 |
| **같은 버전 setup.exe 재설치** | ✅ 그대로 | ✅ 그대로 | ✅ 그대로 |
| **앱 *제거* (Windows 설정 → 앱)** | ✅ 그대로 (Windows 가 AppData 자동 삭제 안 함) | ✅ 그대로 | ✅ 그대로 |
| **운영자가 `%APPDATA%\Autotrade` 폴더 *수동* 삭제** | ❌ 사라짐 | ❌ 사라짐 | ❌ 사라짐 |

### 11-3. installer 가 사용자 secret 을 덮어쓰지 않는 메커니즘

본 invariant 가 깨지지 않도록 코드 / 설정 단에서 강제되는 가드:

1. **`tauri.conf.json::bundle.resources` 가 빈 배열** — installer 가 settings
   파일을 자체 bundle 에 포함시키지 *못한다*.
2. **`src-tauri/binaries/README.md` 가 `backend/.env` 를 bundle 제외 명시** —
   sidecar PyInstaller `--onefile` 산출물은 `.env` 를 *함께 묶지 않는다*.
3. **NSIS installer 의 설치 대상은 `%ProgramFiles%\Agent Trader v1\`** —
   `%APPDATA%` 와 *물리적으로 다른* 디렉터리. installer 가 `%APPDATA%` 를
   *건드릴 권한* 자체가 없다.
4. **`desktop-release.yml` Step 8 (Safety guard 빌드 후)** — bundle 디렉터리
   에 `.env` / `.env.local` 파일이 포함되면 **FATAL** 로 빌드 차단. 인증서 /
   키 확장자도 동일하게 차단.
5. **`app_desktop_launcher.py`** 는 `.env` 를 *읽기만* — `unlink` / `remove`
   / `write_text` 어떤 함수도 `.env` 대상으로 호출하지 않는다 (정적 grep
   가드: `test_env_preservation_policy::test_launcher_does_not_write_to_env`).
6. **`frontend/src/desktop/updaterClient.js`** 는 GitHub REST 만 호출 —
   파일시스템 접근 0건 (`test_env_preservation_policy::
   test_updater_client_does_not_touch_env`).
7. **`UpdateBanner.jsx`** 가 모든 state 에서 "사용자 .env 보존" 안전 배지를
   영구 노출 (`badge-no-env-overwrite` testid, 기존 UI invariant 테스트로
   lock).

### 11-4. 업데이트 후 "KIS 키가 사라진 것 같아요" 트러블슈팅

KIS 키가 *실제로* 사라지는 경우는 위 §11-2 매트릭스에 없다 — 대부분은
**파일명 / 위치 / 확장자 실수**. 다음 순서로 점검:

1. **파일 확장자 확인**: 메모장으로 저장하면 Windows 가 자동으로 `.txt` 를
   붙여 `.env.txt` 로 만든다. 탐색기에서 *보기 → 파일 확장명* 체크박스를
   켜고 `Autotrade` 폴더를 본다.
   - ❌ `.env.txt` ← **launcher 가 인식하지 못한다**
   - ✅ `.env` ← 정상
2. **파일 위치 확인**: 정확히 `%APPDATA%\Autotrade\.env` 인지.
   `C:\Users\<사용자>\Autotrade\.env` 가 아니라 *AppData\Roaming* 안.
3. **파일 권한 확인**: 마우스 우클릭 → 속성 → "읽기 전용" 체크 해제.
4. **백업에서 복구**: 운영자가 다른 PC 또는 1Password / Bitwarden 에 백업한
   `.env` 가 있으면 그 내용을 위 경로에 직접 붙여넣는다 (한 줄에 `KEY=VALUE`
   형식).
5. **launcher 로그 확인**: `%APPDATA%\Autotrade\logs\backend-YYYYMMDD.log`
   에서 다음 라인 검색:
   ```
   safety: .env resolved from C:\Users\...\AppData\Roaming\Autotrade\.env
   secret-presence: KIS_APP_KEY=present
   ```
   `not found in any candidate` 또는 `KIS_APP_KEY=missing` 이면 §1-3 확인.

> **운영자가 KIS 키 / 계좌번호 / Anthropic 키 원문을** *화면 캡처 / GitHub
> issue / 채팅* **에 붙여넣지 *마세요*.** 모든 진단은 위 launcher 로그의
> `present` / `missing` 라벨만으로 충분합니다.

### 11-5. Secret 비노출 invariant

| 위치 | 노출 정책 |
|---|---|
| 앱 UI (UpdateBanner / Settings / Monitoring) | 사용자 secret 입력창 / 표시창 0개 (다른 PR 의 frontend 테스트로 lock) |
| backend sidecar 로그 (`%APPDATA%\Autotrade\logs\`) | secret 키 *원문* 0건. `present` / `missing` 라벨만 (`app_desktop_launcher.py::_print_safety_snapshot`) |
| GitHub repo (`git ls-files`) | KIS PST 토큰 / API key / PEM marker 0건 (`test_repository_hygiene::test_no_real_kis_token_pattern_tracked`, `test_no_tauri_private_key_committed`) |
| GitHub Actions artifact / Release | `.env` / 인증서 / 키 파일 0건 (`desktop-release.yml` Step 8 safety guard) |
| GitHub Release notes (`release.body`) | `sanitizeText` 통과 — `sk-...` / `ghp_...` / `Bearer ...` 패턴 `[REDACTED]` 마스킹 |

### 11-6. `backend-port.json` 은 *재생성 가능 캐시*

`%APPDATA%\Autotrade\backend-port.json` 은 frontend 가 backend sidecar 의
fallback 포트 (8000/8001/8002) 를 빠르게 찾기 위한 *캐시* 다 — 사용자 입력이
아니라 launcher 가 매 실행 시 작성. 본 파일을 수동 삭제해도 다음 앱 실행 시
자동 재생성되며 `.env` 와는 무관하다.

내용 예시 (secret 0건):
```json
{
  "host":       "127.0.0.1",
  "port":       8001,
  "mode":       "free",
  "written_at": "2026-05-20T12:34:56.789Z"
}
```

### 11-7. 운영자 백업 권장 사항

`.env` 가 우발적으로 사라지지 않게:

1. **첫 입력 후 즉시 안전 저장소에 백업** — 1Password / Bitwarden / 암호화된
   USB. 사진 / clipboard / 메모 앱 (평문) 금지.
2. **PC 교체 / 재설치** 시 위 백업에서 *직접* 위 경로에 붙여넣기. 새 PC 의
   `%APPDATA%\Autotrade\` 디렉터리가 *없으면* 첫 앱 실행 후 자동 생성됨 —
   그 다음 `.env` 를 복사.
3. **여러 PC 동기화 금지** — `%APPDATA%` 는 *그 PC 의 사용자* 전용. OneDrive
   / Dropbox 로 동기화하면 KIS 토큰이 클라우드에 평문 저장돼 *Secret 보안
   원칙* 위반.

## 12. 수동 다운로드 fallback 매트릭스 (#5-08)

자동 업데이트 흐름의 *어떤 단계에서 실패해도* 사용자가 setup.exe 를 *수동*
으로 받을 수 있는 경로를 정리. UpdateBanner 가 각 상황에 맞는 정확한 안내
+ 적어도 *하나 이상*의 작동 가능한 anchor 를 항상 노출한다.

### 12-1. 실패 시나리오 별 사용자 화면

| 시나리오 | UpdateBanner state | 노출되는 fallback |
|---|---|---|
| GitHub Release fetch 실패 (네트워크 / DNS) | FAILED | `link-manual-download` (release 페이지) + `btn-update-check` (다시 시도) + `update-fail-env-preserved` (.env 보존 안내) |
| `releases/latest` 가 *없음* (첫 release 미발행) | FAILED (`http 404`) | 위 동일 |
| Latest release 의 `assets[]` 가 빈 배열 | UPDATE_AVAILABLE 또는 UP_TO_DATE | `update-asset-missing-notice` (노란 banner) + `link-manual-download` (release 페이지) — `link-setup-exe-direct` 는 미노출 |
| setup.exe asset 만 누락 (MSI / latest.json 만 첨부) | UPDATE_AVAILABLE | 동일 — release 페이지에서 직접 자산 확인 안내 |
| Tauri updater Phase 3 미활성 (현재 PR 시점) | UPDATE_AVAILABLE | apply 버튼이 *자동 설치 대신* 다운로드 페이지 열기. 직접 setup.exe + release 페이지 anchor 모두 노출 |
| 다운로드 권한 / 디스크 부족 | (Phase 3 활성화 후) FAILED 로 격하 | release 페이지 anchor + 다시 시도 |
| Tauri updater 서명 검증 실패 (Phase 3) | FAILED | 동일 + "검증 실패 — release 페이지에서 직접 받으세요" 안내 (Phase 3 PR 추가) |

### 12-2. fallback anchor 3-tier 우선순위

| 순위 | testid | 노출 조건 | 동작 |
|---|---|---|---|
| 1 | `link-setup-exe-direct` | UPDATE_AVAILABLE + `setupExeAsset.downloadUrl` 있음 | setup.exe 직접 다운로드 (브라우저) |
| 2 | `btn-update-apply` | UPDATE_AVAILABLE | release 페이지 *html_url* 열기 (사용자가 자산 선택) |
| 3 | `link-manual-download` | UPDATE_AVAILABLE + FAILED 둘 다 | release 페이지 (또는 `/releases` 인덱스) anchor — *항상 최소 하나 작동* |

### 12-3. 사용자에게 항상 전달되는 4가지 안내 (모든 fallback 경로 공통)

1. **"최신 버전 확인 불가" / "새 버전이 있습니다"** — 현재 state 헤드라인 (오류처럼 보이지 않게 friendly)
2. **"현재 설치된 버전(vX.Y.Z)은 그대로 사용 가능"** — 운영자가 *지금 당장* 앱을 닫을 필요 없음
3. **"백엔드 연결 실패와 별개"** (FAILED 상태) — Auto Trader backend 와 update fetch 가 *다른 채널* 임을 명시
4. **"%APPDATA%\Autotrade\.env 는 보존"** — 사용자 KIS 키 / 계좌번호 / 로그가 어떤 시나리오에서도 영향 없음

### 12-4. 정책 invariant (테스트로 lock)

- UPDATE_AVAILABLE 에 *항상* `link-manual-download` 노출 (release 페이지 fallback)
- UPDATE_AVAILABLE + `setupExeAsset` 부재 → `update-asset-missing-notice` 노출 + 현재 설치 버전 그대로 사용 가능 안내
- FAILED 에 *항상* `update-fail-env-preserved` 노출 (.env 보존 reassurance)
- 모든 fallback 안내에 BUY/SELL/매수/매도/실거래 라벨 0건
- secret 패턴 (`Bearer ...` / `sk-...`) raw 노출 0건 — `sanitizeText` 통과
- FAILED 상태에 `update-release-notes` details 0건 (stale popup 회귀 방지, #5-04 가드 유지)
