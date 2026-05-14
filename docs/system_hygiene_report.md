# System Hygiene Report — 2026-05 (#88)

> **점검 목적**: 새 기능 추가가 *아니라* repository formatting / ignore rules
> / workflow YAML / status 문서를 정리해 GitHub 원격 저장소 기준으로 확인된
> 보완사항을 해소한다. 운영 로직 / broker / Strategy / RiskManager /
> OrderExecutor / `.env` / 안전 flag 어떤 것도 변경하지 않는다.

## 1. 점검 대상 파일

| 카테고리 | 파일 |
|---|---|
| ignore / Secret | `.gitignore` |
| dependency | `backend/requirements.txt` |
| env example | `backend/.env.example`, `.env.staging.example` |
| workflow YAML | `.github/workflows/backend-ci.yml`, `frontend-ci.yml`, `backend-ci-nightly.yml`, `frontend-ci-nightly.yml`, `pages-deploy.yml`, `desktop-release.yml` |
| 문서 | `README.md`, `docs/final_completion_summary.md` |
| Pages 배포 | `frontend/vite.config.js` (미존재), `frontend/index.html`, `frontend/public/manifest.webmanifest`, `frontend/public/sw.js` |

## 2. 수정한 항목 (본 PR 에서 실제 변경한 것)

### 2.1 `.gitignore` 명확화

**전**: 단순 한 줄 그룹화 + `.venv-310/` 미명시. `backend/.venv-310/` 가
`git status` 에 untracked 로 노출.

**후**: 7개 섹션 (env/secrets, Python, Node, IDE/OS, logs/data, reports,
backups, Tauri) + 명시 규칙:
- `.venv-310/` + `backend/.venv-310/` 추가
- `.cache/`, `coverage/`, `frontend/.env`, `*.local` 추가
- `src-tauri/target/`, `src-tauri/gen/`, `*.msi`, `*.nsis`, `*.dmg`, `*.pkg`
  추가 (#86 desktop)

**검증**:
```bash
$ git check-ignore -v backend/.venv-310/
.gitignore:37:backend/.venv-310/	backend/.venv-310/

$ git check-ignore -v backups/test.sql.gz
.gitignore:75:*.sql.gz	backups/test.sql.gz

$ git check-ignore -v dist/
.gitignore:42:dist/	dist/
```

### 2.2 신규 문서 생성

| 경로 | 목적 |
|---|---|
| `docs/status/current_state.md` | 현재 main 상태 단일 진실 — 운영 모드 / 안전 flag / 6 전략 / 최근 #80~#88 |
| `docs/status/completed_checklist_060_088.md` | #60~#88 체크리스트 표 |
| `docs/status/known_risks.md` | 8개 카테고리 알려진 위험 (배포 / 의존성 / 데이터 / CI / Frontend / Secret) |
| `docs/status/next_steps.md` | P0/P1/P2/P3 우선순위 + 실거래 전환 baseline |
| `docs/dependency_policy.md` | frontend / backend / desktop 의존성 관리 정책 + Paper freeze |
| `docs/system_hygiene_report.md` | 본 문서 |

### 2.3 신규 정적 테스트

`backend/tests/test_repository_hygiene.py` — 14개 invariant:
- `.gitignore` 가 `.env` ignore + `.env.example` allowlist
- `.gitignore` 가 `.venv-310/` / `backups/*` / `*.sql.gz` ignore
- `requirements.txt` 가 패키지별 1줄 구조
- `.env.example` / `.env.staging.example` 에 Secret 의심값 없음
- workflow YAML 파일 6개 존재 + 비어 있지 않음
- workflow YAML 에 실계좌 / token / `sk-` 의심값 없음
- `docs/status/current_state.md` 존재
- `docs/system_hygiene_report.md` 존재
- README 에 *실거래 허가 아님* 문구 존재
- `sw.js` 가 `/api` 응답 캐시 금지 명시
- `docs/dependency_policy.md` 존재

### 2.4 README 갱신

- "현재 상태 — 실거래 허가 아님" 배너 추가 (필수 문구)
- `#84` ~ `#88` 신규 문서 링크 추가
- `docs/status/*` + `docs/dependency_policy.md` + `docs/system_hygiene_report.md`
  4개 링크 추가

### 2.5 `docs/final_completion_summary.md` index 추가

본 문서가 너무 길어 *현재 상태* 와 *과거 스냅샷* 이 혼재. 본 PR 은 *기존
내용을 유지* 하면서 *최상단에 status/ 인덱스* 를 삽입해 사용자가 어디로
가야 하는지 헷갈리지 않게 한다.

## 3. 수정하지 *않고* 후속으로 넘긴 항목

| 항목 | 이유 | 후속 |
|---|---|---|
| `requirements.txt` major 상한 (`<X.0`) | 대규모 dependency 변경은 회귀 위험 — 별도 PR | P2-5 |
| `requirements.lock.txt` 도입 | 같은 사유 + tooling 결정 (uv vs pip-tools) 필요 | P0-2 |
| Pages 배포 구조 단순화 (3-경로 → 1-경로) | 운영 중단 위험 — 별도 PR + 운영자 검토 | P2-1 |
| Frontend lint baseline 0 정리 | 본 PR 의 hygiene 범위 외 (lint 자체는 0 추가) | P2-2 |
| `app/` / broker / RiskManager / OrderExecutor / Strategy 코드 | **본 PR 절대 변경 금지** | — |
| `.env*` 값 (LIVE flag 등) | 안전 default 그대로 보존 | — |
| DB schema / Alembic migrations | 본 PR 절대 추가 금지 | — |

## 4. Secret 보호 확인

| 확인 | 결과 |
|---|---|
| `.gitignore` 에 `.env` / `.env.*` 차단 | ✅ |
| `.gitignore` 의 `.env.example` / `.env.staging.example` allowlist | ✅ (`!.env.example`, `!.env.staging.example`) |
| `*.pem` / `*.key` ignore | ✅ |
| `src-tauri/.gitignore` 에 `*.key` 차단 (이미 #86 에서) | ✅ |
| `backups/*` ignore | ✅ (`!backups/.gitkeep` 만 추적) |
| `backend/.env.example` 에 실 Secret | ❌ 없음 — 모든 KIS / Anthropic / Telegram 필드 빈 값 |
| `.env.staging.example` 에 실 Secret | ❌ 없음 — placeholder 만 (`staging_only_placeholder_change_me` 등) |
| workflow YAML 에 echo Secret | ❌ 없음 — `${{ secrets.GITHUB_TOKEN }}` 같은 ref 만 |

## 5. `.gitignore` 확인 — 핵심 패턴 매트릭스

| 패턴 | 라인 | 효과 |
|---|---|---|
| `.env` | 16 | 운영 secret 차단 |
| `.env.*` | 17 | `.env.local` 등 모든 env 변종 |
| `!.env.example` | 18 | 빈 placeholder 만 추적 |
| `!.env.staging.example` | 19 | staging placeholder |
| `*.pem`, `*.key` | 20, 21 | TLS / signing key |
| `backend/.env` | 22 | backend 전용 secret |
| `frontend/.env` | 23 | frontend 전용 secret |
| `.venv-310/`, `backend/.venv-310/` | 36-37 | local virtualenv |
| `dist/`, `build/`, `node_modules/` | 42-44 | build artifact |
| `data/`, `*.sqlite`, `*.db` | 57-59 | 운영 DB |
| `reports/*` + `!reports/.gitkeep` | 64-65 | DailyReport markdown |
| `backups/*`, `*.sql.gz` | 71-75 | DB backup |
| `src-tauri/target/`, `*.msi`, `*.nsis` | 81-86 | desktop build |

## 6. Workflow YAML 검증

```bash
$ python -c "import yaml,pathlib; [yaml.safe_load(p.read_text(encoding='utf-8')) for p in pathlib.Path('.github/workflows').glob('*.yml')]"
# (모두 정상 파싱)
```

결과:
- `backend-ci-nightly.yml` ✅ OK
- `backend-ci.yml` ✅ OK
- `desktop-release.yml` ✅ OK
- `frontend-ci-nightly.yml` ✅ OK
- `frontend-ci.yml` ✅ OK
- `pages-deploy.yml` ✅ OK

YAML 들여쓰기 / 줄바꿈 / `on / jobs / steps` 구조 정상. checkout / setup-node /
setup-python 표준 패턴.

**Secret 노출 위험 0건**:
- workflow 어디에서도 `echo $TOKEN` 같은 Secret 출력 0건
- `.env` 파일 생성 시 *mock / test 값만* 사용 (예: `desktop-release.yml` 의
  `NODE_ENV: production`)
- LIVE flag 어떤 workflow 에서도 true 설정 0건

## 7. requirements / env example 확인

### 7.1 `backend/requirements.txt` (11 줄, 11 패키지)

- 패키지 1개당 1줄 ✅
- 주석 0줄 (모든 줄이 패키지)
- 설치 가능한 형식 ✅
- major 상한 *미설정* (현재 정책 — `dependency_policy.md` §2.1 참고)

### 7.2 `backend/.env.example` (60 줄)

- 환경변수 1개당 1줄 ✅
- 모든 Secret 필드 빈 값 (`KIS_APP_KEY=`, `ANTHROPIC_API_KEY=` 등) ✅
- LIVE flag 모두 false default ✅
- `KIS_IS_PAPER=true` default ✅
- `DEFAULT_MODE=SIMULATION` default ✅
- 실 token / `sk-` / account number 0건 ✅

### 7.3 `.env.staging.example` (42 줄)

- 환경변수 1개당 1줄 ✅
- `STAGING_PG_PASSWORD=staging_only_placeholder_change_me` (명시적
  placeholder) ✅
- `STAGING_DEFAULT_MODE=SIMULATION` ✅
- 모든 KIS / Anthropic / Telegram 필드 빈 값 ✅

## 8. GitHub Pages / PWA 확인 결과

### 8.1 `pages-deploy.yml` 구조

- `actions/deploy-pages@v4` (Source = GitHub Actions 모드) ✅
- `peaceiris/actions-gh-pages@v4` → `gh-pages` branch force-push ✅
- `sync-main-root` job — `dist` 산출물 main root 자동 sync ✅

**위험**: 세 경로가 *병행* 하며 main root 에 build 산출물이 commit 됨 (소스
↔ artifact 혼재). 운영 중단 위험 때문에 본 PR 에서 *cleanup 하지 않음* —
`known_risks.md` §1.1 + `next_steps.md` §P2-1 으로 후속.

### 8.2 `frontend/public/manifest.webmanifest`

```json
{
  "name":       "Agent Trader v1",
  "short_name": "AgentTrader",
  "start_url":  "/autotrade/",
  "scope":      "/autotrade/",
  ...
}
```

GitHub Pages base path `/autotrade/` 와 lockstep. ✅

### 8.3 `frontend/public/sw.js`

```js
// /api 로 시작하면 무조건 API. WebSocket (ws/wss) 도 같은 취급.
if (url.pathname.startsWith("/api/")) return true;
// ...
// 1) /api/* — network-only. 실패 시 503 sentinel JSON (캐시 안 함).
```

`/api/*` 응답 **캐시 0건** ✅ — Secret 노출 / stale 데이터 위험 0건.

### 8.4 `frontend/vite.config.js`

본 파일은 *존재하지 않음* — vite 8 의 default 동작 사용. `VITE_BASE_PATH=/autotrade/`
는 `pages-deploy.yml` 의 env 로만 주입.

## 9. dependency pinning 관련 후속 과제

본 PR 은 *정책 명시만* — 실제 lock 파일 도입은 [`docs/dependency_policy.md`](dependency_policy.md)
§2.3 의 P0-2 작업.

## 10. 테스트 결과

| 테스트 | 결과 |
|---|---|
| `backend/tests/test_repository_hygiene.py` (신규) | 14 PASS (정적 검사) |
| `backend/tests/test_system_audit_invariants.py` (#87, 회귀 확인) | 22 PASS |
| `backend` 전체 regression (사전 7건 제외) | (#87 baseline 동일 — 본 PR 무회귀) |
| `frontend` 전체 regression | (#86 baseline 동일 — 본 PR 변경 0건) |
| frontend lint | (베이스라인 동일 — 본 PR `.gitignore` / docs 만 변경) |

## 11. 안전 invariant 확인 (본 PR — *모두 0건* 확인)

| 위반 시 빠른 검출 위치 | 본 PR 결과 |
|---|---|
| `broker.place_order(` 호출 0건 | ✅ 본 PR 코드 변경 0건 |
| `broker.cancel_order(` 호출 0건 | ✅ |
| `route_order(` 호출 0건 | ✅ |
| `OrderExecutor` 호출 0건 | ✅ |
| `KisBrokerAdapter(...)` 실 instance 0건 | ✅ |
| `ENABLE_LIVE_TRADING=true` 0건 | ✅ `.env.example` default `false` 유지 |
| `ENABLE_AI_EXECUTION=true` 0건 | ✅ |
| `ENABLE_FUTURES_LIVE_TRADING=true` 0건 | ✅ |
| `KIS_IS_PAPER=false` 0건 | ✅ |
| Secret / API Key 변경 0건 | ✅ 빈 placeholder 그대로 |
| frontend localStorage Secret 저장 0건 | ✅ 본 PR 코드 변경 0건 |
| DB schema 변경 0건 | ✅ |
| Alembic migration 추가 0건 | ✅ |
| `app/` 운영 로직 변경 0건 | ✅ |
| 새 매매기능 추가 0건 | ✅ |
| LIVE 활성화 버튼 추가 0건 | ✅ |

## 12. 안전 invariant — 테스트로 lock (#88 신규)

`backend/tests/test_repository_hygiene.py` 의 14개 정적 invariant:
1. `.gitignore` 가 `.env` 차단
2. `.gitignore` 가 `.env.example` allowlist (`!.env.example`)
3. `.gitignore` 가 `.venv-310/` 또는 `backend/.venv-310/` 차단
4. `.gitignore` 가 `backups/*` 차단
5. `backend/requirements.txt` 가 패키지별 1줄 구조
6. `backend/.env.example` 에 Secret 의심값 없음 (`sk-` / `pat_` / 실 계좌 형식)
7. `.env.staging.example` 에 Secret 의심값 없음
8. workflow YAML 파일 6개 존재 + 비어 있지 않음
9. workflow YAML 에 echo secret / 실 계좌 / token 0건
10. `docs/status/current_state.md` 존재
11. `docs/system_hygiene_report.md` 존재
12. README 에 *실거래 허가 아님* 문구 존재
13. `frontend/public/sw.js` 가 `/api` 캐시 금지 명시
14. `docs/dependency_policy.md` 존재

## 13. 참고

- [`docs/status/current_state.md`](status/current_state.md)
- [`docs/status/completed_checklist_060_088.md`](status/completed_checklist_060_088.md)
- [`docs/status/known_risks.md`](status/known_risks.md)
- [`docs/status/next_steps.md`](status/next_steps.md)
- [`docs/dependency_policy.md`](dependency_policy.md)
- [`docs/system_audit_2026_05.md`](system_audit_2026_05.md) — #87 audit
