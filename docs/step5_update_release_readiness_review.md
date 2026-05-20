# Step 5 — 자동 업데이트 / 릴리스 배포 점검 보고서

> 본 문서는 `audit/step5-update-release-readiness-review` 브랜치에서 5단계
> 7개 항목 (#5-01 ~ #5-07) 이 `main` 에 *모두 반영*됐는지 점검한 결과를 단일
> 진실로 기록한다. 코드 수정 없이 *점검 + 문서화* 중심.
>
> **본 점검은 EXE 빌드를 수행하지 않으며, desktop-release workflow 도
> 실행하지 않는다.** 실거래 / 주문 실행 호출 0건.

## 0. 점검 요약

| 항목 | 결과 |
|---|---|
| 작업 트리 (`git status`) | ✅ clean |
| 5-01 ~ 5-07 항목 main 반영 여부 | ✅ 7/7 모두 반영 |
| 전체 frontend vitest | ✅ 111 files / 2047 tests PASS |
| backend hygiene + env preservation pytest | ✅ 60 PASS |
| `python scripts/security_scan.py` | ✅ 936 files / **0 findings** |
| `npm run build` | ✅ clean, 174 ms |
| `desktop-release.yml` YAML safe_load | ✅ clean (`triggers=['workflow_dispatch']`, `jobs=['build-windows']`) |
| 안전 flag default (`.env.example`) | ✅ `KIS_IS_PAPER=true`, `ENABLE_LIVE_TRADING=false`, `ENABLE_AI_EXECUTION=false`, `ENABLE_FUTURES_LIVE_TRADING=false` |
| repo 안 실 private key 파일 | ✅ 0건 (`*.key` / `*.pem` / `*.p12` / `*.pfx` / `*.crt` / `*.cer` / `*.keystore` / `*.jks` 추적 0) |
| **5단계 기준 setup.exe 빌드 가능 여부** | ✅ **빌드 가능** (조건 8/8 모두 통과) |

## 1. 5-01 — 업데이트 정책 정의 — ✅ PASS

| 확인 | 결과 |
|---|---|
| `docs/auto_update_policy.md` 존재 | ✅ |
| 자동 업데이트 범위 명시 (Phase 1 수동 / Phase 2 알림 / Phase 3 자동 설치) | ✅ §10-0 용어 + §10-1 흐름 |
| 사용자 `.env` 보존 명시 | ✅ §10-4, §11 (통합 정책) |
| 실패 시 수동 다운로드 fallback 명시 | ✅ §6 fallback + §10-2 Step 8 |
| `.env` 표준 경로 명시 | ✅ §11-1 `%APPDATA%\Autotrade\.env` |
| 안전 invariant 4종 (사용자 `.env` 보존 / 실거래 flag 변경 0건 / 자동 주문 트리거 0건 / Secret 노출 0건) | ✅ §2 |

**확인 파일:**
- `docs/auto_update_policy.md` (143 → 600+ lines, §1-11)

## 2. 5-02 — Release Notes 표시 — ✅ PASS

| 확인 | 결과 |
|---|---|
| `docs/release_notes.md` 존재 + `v1.0.0` 섹션 | ✅ (5개 keyword 매칭: "초기 베타", "새 기능", "안전 invariant" 등) |
| frontend `RELEASE_NOTES` vs `WELCOME_NOTES` 분리 | ✅ `frontend/src/config/releaseNotes.js` 에 두 export 분리 + `kind: "welcome"` 명시 |
| `UpdateBanner` 가 fetch 한 `releaseNotes` 를 details 안에 노출 | ✅ `update-release-notes` testid (UPDATE_AVAILABLE state 한정) |
| Stale release note 가 최신 업데이트처럼 표시되지 않음 | ✅ #5-04 가드 (정적 import 차단) |
| Welcome modal 이 "초기 안내" 배지 + disclaimer 노출 | ✅ `release-notes-welcome-badge` + `release-notes-welcome-disclaimer` |

**확인 파일:**
- `docs/release_notes.md`
- `frontend/src/config/releaseNotes.js`
- `frontend/src/components/common/VersionBadge.jsx` (ReleaseNotesModal)
- `frontend/src/components/UpdateBanner.jsx`

## 3. 5-03 — 업데이트 확인 버튼 — ✅ PASS

| 확인 | 결과 |
|---|---|
| `UpdateBanner` 에 "업데이트 확인" 버튼 | ✅ `btn-update-check` testid (IDLE/CHECKING/UP_TO_DATE state) |
| `updaterClient.checkForUpdate()` 가 GitHub Release latest 조회 | ✅ `fetchLatestRelease({owner, repo})` → `https://api.github.com/repos/.../releases/latest` |
| SemVer 비교 (1.0.10 > 1.0.2) | ✅ `parseVersion` / `compareVersions` / `isNewer` 함수 |
| 실패 시 graceful fallback (FAILED state) | ✅ `update-fail-headline` "최신 버전 확인 불가" + `link-manual-download` GitHub Release 페이지 anchor |
| 안전 배지 3종 모든 state 영구 노출 | ✅ `badge-no-env-overwrite` / `badge-no-live-flag-change` / `badge-not-order-trigger` |

**확인 파일:**
- `frontend/src/components/UpdateBanner.jsx` — IDLE/CHECKING/UP_TO_DATE/UPDATE_AVAILABLE/FAILED 5 state UI
- `frontend/src/desktop/updaterClient.js` — UPDATE_STATES enum + helper 함수

## 4. 5-04 — 오래된 팝업 방지 — ✅ PASS

| 확인 | 결과 |
|---|---|
| 같은 welcome 공지가 ack 후 재팝업 안 됨 | ✅ `agent-trader-welcome-ack` localStorage 키 + legacy `agent-trader-last-seen-version` backwards compat |
| fetch 실패 시 stale release note 표시 0건 | ✅ `UpdateBanner.jsx` / `updaterClient.js` 가 `../config/releaseNotes` 를 **import 하지 않음** (정적 grep 가드: `test_update_banner_no_release_notes_import` 등) |
| FAILED headline = "최신 버전 확인 불가" (≠ "Failed to fetch") | ✅ `update-fail-headline` testid 라벨 lock |
| FAILED 상태 ≠ backend offline | ✅ `update-fail-not-backend` disclaimer |
| FAILED `error` 문자열에 secret 패턴 sanitize 적용 | ✅ `sanitizeText(result?.error)` (`Bearer ...` / `sk-...` / `ghp_...` 마스킹) |
| FAILED 상태에 `update-release-notes` details 0건 | ✅ JSX 조건부 렌더링 + 정적 테스트 |

**확인 파일:**
- `frontend/src/components/UpdateBanner.jsx`
- `frontend/src/components/UpdateBanner.test.jsx` — `Stale popup guard — static import invariant` + `behavior` describe 블록
- `frontend/src/config/releaseNotes.js` — Phase 4 import 가드 주석

## 5. 5-05 — GitHub Release 연동 — ✅ PASS

| 확인 | 결과 |
|---|---|
| `desktop-release.yml` 가 setup.exe 를 workflow artifact 로 업로드 | ✅ `actions/upload-artifact@v4`, `path=src-tauri/target/release/bundle/nsis/*-setup.exe`, `if-no-files-found: error` |
| `create_release=true` 일 때 GitHub Release draft 첨부 | ✅ `softprops/action-gh-release@v2`, `if: ${{ inputs.create_release }}`, `files=bundle/nsis/*-setup.exe` |
| `release_tag` SemVer sanitize step 존재 | ✅ Step 0 — PowerShell `^v?\d+\.\d+\.\d+(-[A-Za-z0-9.\-]+)?(\+[A-Za-z0-9.\-]+)?$` + 길이 ≤ 64 |
| artifact path 에 secret / `.env` / 인증서 / `*.key` 등 포함 0건 | ✅ 정적 PyYAML grep 가드 (`test_desktop_release_workflow_artifact_only_ships_setup_exe`) |
| `softprops/action-gh-release@v2` 만 사용 (다른 action 으로 우회 차단) | ✅ 정적 grep 가드 |
| `update-banner-direct-download` 링크 (UPDATE_AVAILABLE) | ✅ `link-setup-exe-direct` — `result.setupExeAsset.downloadUrl` 직접 사용, `target=_blank` + `rel="noopener noreferrer"` |

**확인 파일:**
- `.github/workflows/desktop-release.yml`
- `frontend/src/components/UpdateBanner.jsx` — `link-setup-exe-direct` anchor
- `frontend/src/desktop/updaterClient.js` — `fetchLatestRelease.assets[].downloadUrl`
- `backend/tests/test_repository_hygiene.py` — 6 workflow 정적 가드

## 6. 5-06 — Tauri updater Phase 3 준비 — ✅ PASS (활성화 0건)

| 확인 | 결과 |
|---|---|
| `tauri.conf.json::plugins.updater.active` | ✅ `false` (정적 가드: `test_tauri_updater_inactive_in_phase2`) |
| `tauri.conf.json::bundle.createUpdaterArtifacts` | ✅ `false` (정적 가드: `test_tauri_create_updater_artifacts_false`) |
| `plugins.updater.pubkey` | ✅ `""` (빈 값, 길이=0) |
| `plugins.updater.endpoints[0]` | ✅ `https://github.com/1976haru/autotrade/releases/latest/download/latest.json` (실 repo URL placeholder) |
| Phase 3 8단계 활성화 절차 문서화 | ✅ `docs/auto_update_policy.md` §10-2 (tauri signer generate → public key commit → GitHub Secret → workflow env → createUpdaterArtifacts → downloadAndInstall → relaunch → manual fallback) |
| 활성화 직전 8항목 체크리스트 | ✅ §10-3 |
| TAURI_PRIVATE_KEY repo commit 0건 | ✅ PEM marker (`BEGIN PRIVATE KEY` / `OPENSSH` / `RSA` / `EC` / `DSA` / `ENCRYPTED`) 추적 0건 (정적 가드: `test_no_tauri_private_key_committed`) |
| `desktop-release.yml` 의 signing env 키 모두 주석 처리 | ✅ Line 263-264 `# TAURI_PRIVATE_KEY:    ${{ secrets.TAURI_PRIVATE_KEY }}` (정적 가드: `test_desktop_release_workflow_phase3_signing_inactive` — PyYAML 로 step env 키 순회 시 활성 0건) |
| public key / private key 구분 명확 | ✅ §10-0 용어 테이블 + §10-2 Step 1-3 |

**확인 파일:**
- `src-tauri/tauri.conf.json`
- `.github/workflows/desktop-release.yml`
- `docs/auto_update_policy.md` §10
- `docs/desktop_exe_status.md` §8-H
- `backend/tests/test_repository_hygiene.py` — Phase 3 정적 가드 4개

## 7. 5-07 — .env 보존 테스트 — ✅ PASS

| 확인 | 결과 |
|---|---|
| `%APPDATA%\Autotrade\.env` 표준 경로 문서화 | ✅ `docs/auto_update_policy.md` §11-1 + `docs/exe_oneclick_installation.md` §3-2/§13-2/§13-3 + `docs/desktop_exe_status.md` §8-I-1 |
| 보존 매트릭스 (앱 업데이트 / 재설치 / 제거 / 수동 삭제) | ✅ §11-2 4행 표 |
| installer 가 사용자 secret 미덮어쓰기 4-layer 메커니즘 | ✅ §11-3 (installer scope / bundle exclusion / workflow safety guard / launcher 읽기만) |
| `UpdateBanner` 에 "사용자 .env 보존" 배지 | ✅ `badge-no-env-overwrite` testid + 한국어 라벨 정적 lock |
| KIS 키 사라짐 트러블슈팅 (.env.txt 함정 / AppData 경로 / 권한 / 백업 / launcher 로그) | ✅ §11-4 + `exe_oneclick_installation.md` §13-3 |
| Secret 비노출 5위치 invariant (UI / 로그 / repo / artifact / Release notes) | ✅ §11-5 |
| `backend-port.json` 재생성 가능 캐시 안내 | ✅ §11-6 |
| 운영자 백업 권장 (1Password / Bitwarden, OneDrive 금지) | ✅ §11-7 |
| `app_desktop_launcher.py` 가 `.env` 에 write/delete 호출 0건 | ✅ 정적 가드 `test_launcher_does_not_write_to_env` |
| `updaterClient.js` 가 `.env` / 파일시스템 접근 0건 | ✅ 정적 가드 `test_updater_client_does_not_touch_env` |

**확인 파일:**
- `docs/auto_update_policy.md` §11
- `docs/exe_oneclick_installation.md` §3-2, §13-2, §13-3
- `docs/desktop_exe_status.md` §8-I
- `backend/tests/test_env_preservation_policy.py` — 11 정적 invariant 테스트

## 8. 워크플로 안전 점검

`desktop-release.yml` 의 각 step 별 안전 차단 매트릭스 — 모두 통과:

| Step | 차단 항목 | 통과 여부 |
|---|---|---|
| 0 | release_tag 가 SemVer 가 아니면 fail | ✅ |
| 4 | `.env.example` 안전 flag default 가 unsafe 면 fail | ✅ |
| 4 | workflow 자체에 enable-flag-true 패턴 등장 시 fail | ✅ |
| 5 | repository_hygiene 정적 가드 (49 PASS) | ✅ |
| 5 | security_scan finding ≥ 1 이면 fail | ✅ |
| 8 | bundle 안에 `.env` / `.env.local` / 인증서 / 키 파일 검출 시 FATAL | ✅ |
| 9 | NSIS setup.exe 생성 안 되면 exit 1 | ✅ |
| 10 | upload-artifact `if-no-files-found: error` | ✅ |
| 11 | GitHub Release 첨부 = `bundle/nsis/*-setup.exe` 만 | ✅ |
| - | `workflow_dispatch` 만, push/schedule/pull_request 트리거 0건 | ✅ |
| - | `runs-on: windows-latest` 한정 | ✅ |
| - | `permissions: contents: write` 만 (최소 권한) | ✅ |

## 9. 테스트 결과 (집계)

| 분류 | 결과 |
|---|---|
| backend `pytest test_repository_hygiene.py test_env_preservation_policy.py -q` | **60 PASS** |
| frontend `vitest run src/components/UpdateBanner.test.jsx src/components/common/VersionBadge.test.jsx` | **51 PASS** |
| frontend `vitest run` (전체) | **111 files / 2047 PASS** |
| `python scripts/security_scan.py` | **936 files / 0 findings** |
| `npm run build` | **clean, 174 ms** |
| `python -c "yaml.safe_load(desktop-release.yml)"` | **clean** (`triggers=['workflow_dispatch']`, `jobs=['build-windows']`) |

## 10. 남은 리스크 (점검 시점)

본 5단계 묶음의 *현재* 알려진 리스크와 완화 방안:

| 리스크 | 영향 | 완화 |
|---|---|---|
| Tauri updater Phase 3 가 *아직 비활성* (`active=false`) | 자동 *설치* 0건 — 사용자가 setup.exe 를 *수동* 다운로드 + 더블클릭해야 함 | Phase 2 fallback (release 페이지 링크 + `link-setup-exe-direct` anchor) 가 그대로 작동. Phase 3 활성화는 `docs/auto_update_policy.md` §10-2 8단계 |
| WiX MSI 산출 보류 (외부 503) | MSI 미생성, NSIS `*-setup.exe` 만 | NSIS setup.exe 만으로 베타테스터 배포 가능. MSI 복원은 `WixTools314` 캐시 step 추가한 별도 PR |
| 코드 서명 인증서 없음 → SmartScreen 경고 가능 | 사용자가 "추가 정보 → 실행" 클릭 필요 | release notes / `exe_oneclick_installation.md` §4-2 에 명시 안내 |
| 본 점검 시점까지 `desktop-release.yml` *수동 실행 0회* | 실제 setup.exe 산출물 + GitHub Release draft 가 *아직 존재하지 않음* | 본 PR 머지 후 운영자가 1회 수동 실행 (사용자 요청서 §18 단계) |
| 첫 실 빌드에서 새로운 unknown CI 실패 가능 (Rust toolchain / cache / PyInstaller 등) | 첫 빌드만 ~30~45분 소요 + 실패 시 운영자 진단 필요 | workflow 의 step 별 safety guard + summary step 이 실패 지점을 명확히 surface |
| Phase 3 활성화 후 키 분실 / 손상 시 자동 업데이트 깨짐 | 사용자 PC 에 stale latest.json 캐시 가능성 | `docs/auto_update_policy.md` §10-3 체크리스트 8번 — hotfix revert PR draft 사전 준비 |

## 11. setup.exe 빌드 가능 판정

사용자 요청서 §7 의 8개 조건 매트릭스:

| # | 조건 | 결과 |
|---|---|---|
| 1 | `security_scan` 0 findings | ✅ 936 files / 0 findings |
| 2 | `UpdateBanner` 테스트 통과 | ✅ 35 PASS |
| 3 | env preservation 테스트 통과 | ✅ 11 PASS |
| 4 | repository hygiene 통과 | ✅ 49 PASS |
| 5 | `desktop-release.yml` 안전 경로 확인 | ✅ §8 매트릭스 모두 통과 |
| 6 | `.env` / key / secret artifact 포함 0건 | ✅ 정적 가드 + bundle.resources=[] + Step 8 FATAL guard |
| 7 | 작업 트리 clean | ✅ `git status` clean |
| 8 | 안전 flag default | ✅ `KIS_IS_PAPER=true`, LIVE/AI/FUTURES `=false` |

> **판정: ✅ 5단계 기준 setup.exe 빌드 가능.**
> 본 PR 머지 + 운영자 옵트인 후 `desktop-release` workflow 를 1회 수동 실행
> 가능.

## 12. 추천 release_tag

| 항목 | 값 |
|---|---|
| 현재 `tauri.conf.json::version` | `1.0.0` |
| 현재 `frontend/package.json::version` | `1.0.0` |
| 기존 git tag | (없음) |
| 추천 첫 release tag | **`v1.0.1-beta.1`** |
| 사유 | 1.0.0 은 *원본 코드 상태* — 5단계 자동 업데이트 / 배포 보강이 모두 반영된 첫 *베타 publishable* 산출물은 1.0.1 부터 시작. `-beta.1` suffix 로 SmartScreen 평판 미확보 + auto-updater Phase 2 (수동) 상태임을 명시 |

> 운영자가 `desktop-release` workflow → Run workflow → `release_tag: v1.0.1-beta.1`,
> `draft: true`, `create_release: true` 로 실행 권장.

## 13. 본 audit 의 범위 (변경 0건)

본 PR 은 *문서만* 추가한다. 다음은 *변경 0건*:

- `src-tauri/tauri.conf.json`
- `.github/workflows/desktop-release.yml`
- `frontend/src/components/UpdateBanner.jsx`
- `frontend/src/desktop/updaterClient.js`
- `frontend/src/config/releaseNotes.js`
- `backend/app_desktop_launcher.py`
- 안전 flag default (`KIS_IS_PAPER` / `ENABLE_LIVE_TRADING` 등)
- `backend/tests/test_repository_hygiene.py`
- `backend/tests/test_env_preservation_policy.py`

본 audit 가 만든 유일한 새 파일:
- **`docs/step5_update_release_readiness_review.md`** (본 문서)

## 14. 다음 단계 (운영자 수동)

1. 본 PR (`audit/step5-update-release-readiness-review`) 을 GitHub UI 에서 머지.
2. `main` 최신화 (`git pull origin main`).
3. GitHub → Actions 탭 → `desktop-release` workflow → **Run workflow**.
4. 입력:
   - `release_tag`: `v1.0.1-beta.1`
   - `draft`: `true` (운영자가 직접 publish)
   - `create_release`: `true`
5. ~30~45분 후 Actions 실행 페이지 *Artifacts* 에 `agent-trader-windows-installer-v1.0.1-beta.1.zip` + GitHub Releases (draft) 에 `*-setup.exe` 첨부 확인.
6. setup.exe 1개 다운로드 후 *별도 Windows VM / 백업 PC* 에서 더블클릭 설치 → smoke test:
   - 앱이 정상 실행되는지
   - `%APPDATA%\Autotrade\` 자동 생성되는지
   - UpdateBanner 가 표시되는지
7. 운영자가 만족하면 GitHub Release 페이지에서 *Publish* 클릭 → 베타테스터가 다운로드 가능.
