# EXE 앱 버전 / 빌드 commit 확인 (#57 / 7-05)

사용자가 현재 실행 중인 EXE 가 **최신 main 기준 빌드인지** 확인할 수 있도록
앱 버전 / 채널 / git commit / branch / build time / dirty 여부를 Settings 탭의
"앱 버전 / 빌드 정보" 카드에 표시한다.

## 표준 build metadata

| 필드 | 예시 | 의미 |
|---|---|---|
| `version` | `1.0.0` | 앱 SEMVER (frontend=package.json, backend=env/stamp) |
| `channel` | `paper-beta` | 빌드 채널 |
| `commit` | `abc1234` | git short hash (7자리) |
| `commit_full` | `abc1234…` | git full hash (API 응답용) |
| `branch` | `main` | 빌드 시점 branch / ref |
| `build_time` | `2026-05-24T10:30:00+09:00` | ISO 빌드 시각 |
| `source` | `github-actions` / `local-build` | 빌드 출처 |
| `is_dirty` | `false` | 커밋되지 않은 로컬 변경 포함 여부 |
| `is_live_authorization` | `false` (불변) | 실거래 권한 아님 |
| `contains_secret` | `false` (불변) | secret 미포함 |

## 주입 경로

### Frontend (권위 소스 — 사용자가 보는 화면)
`vite.config.js` 가 **build-time** 에 git 메타데이터를 `import.meta.env.VITE_*`
로 baking 한다. 우선순위: 명시 env(CI 주입) → `git` 명령 → `unknown`.
git 실패해도 빌드는 절대 실패하지 않는다. `utils/buildInfo.js::getBuildInfo()`
가 이를 읽어 `AppVersionCard` 가 표시.

### Backend sidecar
`GET /api/system/build-info` 가 backend 빌드 메타데이터를 반환한다. 우선순위:
1. `app/system/build_stamp.py` — `scripts/generate_build_stamp.py` 가 빌드 시 생성
   (gitignore). PyInstaller `--collect-submodules app` 로 bundle → packaged EXE
   에서도 정확.
2. 환경변수 `AUTOTRADE_*`
3. `git` 명령 (dev)
4. `unknown`

`AppVersionCard` 는 backend build-info 도 조회해 **frontend ↔ backend commit
일치 여부**를 안내한다 (일부만 업데이트된 빌드 감지).

### CI (desktop-release.yml)
"Compute build metadata" step 이 `VITE_*` + `AUTOTRADE_*` 를 `GITHUB_ENV` 로
export → 프론트 번들 + backend stamp 가 동일 commit / 빌드 시각을 baking.

## UI 위치

Settings(설정) 탭 상단 "🏷️ 앱 버전 / 빌드 정보" 카드
(`data-testid="app-version-card"`).

표시 항목: 앱 버전 / 채널 / 빌드 commit / 브랜치·source / 빌드 시각 / dirty 여부
/ (backend sidecar commit) + GitHub main 비교 안내.

## EXE 실행 후 확인 절차

1. EXE 실행 후 Settings(설정) 탭을 연다.
2. "앱 버전 / 빌드 정보" 카드에서 **앱 버전** 확인.
3. **빌드 commit** (예: `abc1234`) 확인.
4. **빌드 시각** 확인.
5. **채널** (paper-beta 등) 확인.
6. GitHub `main` 의 최신 commit 과 위 빌드 commit 을 비교.
7. commit 이 다르면 **구버전일 가능성** — 최신 빌드로 업데이트 권장.
8. "로컬 변경 포함(dirty)" 이 **예** 면 커밋되지 않은 로컬 변경을 포함한
   비공식 빌드일 수 있다.
9. 화면에 API key / Secret / 계좌번호 원문이 **표시되지 않는지** 확인.
10. frontend commit 과 backend sidecar commit 이 다르면 일부만 업데이트된
    빌드일 수 있다 (카드가 경고).

## 안전 invariant (테스트로 lock)

- 매수 / 매도 / 실거래 시작 / Place Order / ENABLE_* 버튼 0개.
- 입력 form(input/textarea/select) 0개.
- 응답·화면에 Secret / API key / 계좌번호 원문 0건 (whitelist 필드만).
- `is_live_authorization=false` / `contains_secret=false` 불변.
- build metadata 가 없거나 unknown 이어도 crash 0건 ("확인 불가" 표시).
- `.env` 에 build metadata 저장 0건 (Vite define / build stamp / env 만).

## 관련 파일

- frontend: `vite.config.js` (주입), `src/utils/buildInfo.js` (+`.test.js`),
  `src/components/common/AppVersionCard.jsx` (+`.test.jsx`),
  `src/components/tabs/Settings.jsx` (mount), `src/services/backend/client.js`
- backend: `app/system/build_info.py`, `app/api/routes_system.py`
  (`GET /api/system/build-info`), `tests/test_build_info_api.py`
- build: `scripts/generate_build_stamp.py`, `scripts/build_backend_sidecar.ps1`,
  `.github/workflows/desktop-release.yml`, `.gitignore`
