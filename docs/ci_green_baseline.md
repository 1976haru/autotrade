# CI Green Baseline — main 기준 통과 조건 + 머지 정책

> 본 문서는 **반복 CI 실패의 근본 원인 분석 + 영구 잠금 정책**입니다.
> 본 문서가 정의한 green baseline 을 만족하지 못하는 PR 은 머지 금지.

## 1. 현재 상태 (main 기준 — 2026-05-19)

| 워크플로우 | 상태 | 검증 명령 |
|---|---|---|
| **Backend CI** (`backend-ci.yml`) | ✅ GREEN | `cd backend && python -m ruff check app tests && python -m pytest -q` |
| **Frontend CI** (`frontend-ci.yml`) | ✅ GREEN | `cd frontend && npm ci && npm run lint && npx vitest run && npm run build` |
| **Security Scan** (`scripts/security_scan.py`) | ✅ 0 findings | `python scripts/security_scan.py` |

local + CI 일치하는 baseline 결과:
- Backend pytest: **4570 PASS / 5 skipped / 25 deselected** (clean env)
- Backend ruff: clean
- Frontend lint: 0 errors / 130 warnings (warnings 는 CI 차단 안 함)
- Frontend vitest: 1934 PASS (107 test files)
- Frontend build: OK
- Security scan: 0 findings (894 files)

## 2. 반복 실패의 *근본 원인* — 카테고리별

### A. 로컬 `.env` 와 documented default 의 불일치

가장 빈번한 *local-only* 실패. 운영자 `.env` 가 `DEFAULT_MODE=PAPER`,
`KIS_APP_KEY=...`, `ENABLE_AI_EXECUTION=true` 같은 *비-default* 값을 carry 하면
*documented default* 를 검증하는 unit test 들이 fail. CI 는 `.env` 가 없으므로
green.

영향받는 테스트 (본 PR 에서 *영구 fix*):
- `tests/test_routes.py::test_status_exposes_safety_flags`
- `tests/test_routes.py::test_status_safety_flags_block_present`
- `tests/test_brokers_kis_stub.py::test_constructor_reads_settings_credentials_when_unset`
- `tests/test_brokers_kis_stub.py::test_get_price_raises_when_no_credentials`
- `tests/test_ai_routes.py::test_analyze_persists_default_mode_on_audit_row`
- `tests/test_ai_routes.py::test_analyze_audit_row_carries_mode_even_on_provider_error`

**Fix 패턴**: 각 파일에 autouse 픽스처를 추가 — `monkeypatch.setenv` 로 documented
default 를 *프로세스 env 에 명시* 하고 `get_settings.cache_clear()`. 프로세스
env 는 `.env` 파일보다 *우선* 이므로 로컬 `.env` 가 있어도 테스트는 안정.

```python
@pytest.fixture(autouse=True)
def _clean_safety_env(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODE", "SIMULATION")
    monkeypatch.setenv("ENABLE_LIVE_TRADING", "false")
    ...
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
```

### B. 하드코딩된 Windows 경로 (`C:/trade/autotrade`)

이전에 `tests/test_data_quality.py` 가 subprocess cwd 를 절대 경로로 박아 Linux
CI 가 `FileNotFoundError`. 이미 PR 에서 *영구 fix* — `Path(__file__).resolve().parents[2]`
로 OS 독립적 repo_root 계산. main 기준 잔존 0건.

남은 mention 은 docstring/주석 안내 텍스트뿐 (테스트 영향 없음).

### C. 테스트 DB 테이블 누락 (`agent_decision_log`)

`TestClient(app)` 를 직접 인스턴스화한 테스트가 `app.db.session.engine` 의
운영 DB 를 사용해 fresh CI 환경에서 `no such table` 으로 실패한 적 있음.
**해결**: conftest 의 `client` fixture (in-memory SQLite + `Base.metadata.create_all`)
를 사용 + API 라우트 핸들러에 graceful fallback (`no such table` → 빈 envelope).

### D. ruff 누적 violation

E402 / F401 / F841 / F541 등 신규 PR 추가 시 *그 PR 내에서* 즉시 해결.
- `tests/*` 는 `pyproject.toml::tool.ruff.lint.per-file-ignores` 로
  E402 / F841 / F541 ignore
- `app/api/routes_*.py` 는 E402 ignore

## 3. Backend CI green 조건 — 매 PR 머지 전 체크리스트

```bash
cd backend
python -m pip install -r requirements.txt
python -m ruff check app tests
python -m pytest -q
```

**기준**:
- ruff: `All checks passed!`
- pytest: 모든 테스트 PASS (skipped/deselected 만 허용)
- 신규 테스트가 `.env` / `KIS_APP_KEY` / `DEFAULT_MODE` 같은 *로컬 환경* 에
  의존하면 안 됨 → 위 §2.A 픽스처 패턴 사용

## 4. Frontend CI green 조건

```bash
cd frontend
npm ci
npm run lint                 # 0 errors 필수, warning 은 허용
npx vitest run --exclude '**/*.stress.test.jsx'
npm run build
```

**기준**:
- lint: 0 errors (warning 만 있어도 CI 통과)
- vitest: 모든 PASS
- build: PASS

**lint baseline 정책** — `frontend/eslint.config.js` 에 등록된 baseline 정책 그대로:
- `react-hooks/set-state-in-effect`: `off`
- `react-hooks/use-memo`: `off`
- `no-useless-escape`: `warn`
- `no-useless-assignment`: `off`
- test 파일에 `globals.node` 추가

새 PR 에서 *new lint error* 추가 시 즉시 fix. baseline 자체를 흔드는 변경
(예: `error → off` 추가) 은 별도 PR 로 명시.

## 5. Security Scan green 조건

```bash
python scripts/security_scan.py
```

**기준**: `HIGH/MEDIUM/LOW/INFO 모두 0 findings`. 단 1건이라도 발견되면 **머지 금지**.

False positive 발생 시:
- 디렉토리 단위 allowlist: `scripts/security_scan.py` 의 `_SKIP_GLOBS`
- 라인 단위 ignore: 해당 라인 끝에 `# security-scan: ignore` 또는
  `// security-scan: ignore` (테스트 fake 값은 `backend/tests/_fake_secrets.py`
  의 표준 placeholder 사용 — 자세히는 `docs/security_scan.md`)

## 6. PR 머지 전 체크 순서 (operator 명시 절차)

매 PR 머지 직전, 다음 순서로 통과 여부 확인:

1. **Backend CI workflow** 가 GitHub Actions 에서 ✅ green
2. **Frontend CI workflow** 가 ✅ green
3. **Security Scan** (별도 CI 또는 수동 `python scripts/security_scan.py`)
   이 ✅ 0 findings
4. PR 리뷰 / 사용자 승인
5. *Squash & merge* (또는 머지 코밋)

**위 1~3 중 하나라도 빨간불이면 머지 금지**. 회피하려고 force-push / `--no-verify`
사용 금지 — 원인을 *고치고* 다시 push.

## 7. 머지 정책 (`CLAUDE.md` / README 부속)

```
- Backend CI 빨간불이면 기능 PR 머지 금지
- Frontend CI 빨간불이면 기능 PR 머지 금지
- Security Scan 빨간불이면 무조건 머지 금지
- desktop-release 는 backend + frontend CI 가 모두 green 인 뒤에만 실행
- pre-existing failure 는 본 문서 §8 에 등록 후 별도 fix PR
```

## 8. Pre-existing 실패 등록부

**현 시점**: 등록된 pre-existing 실패 0건. `main` 기준 모든 CI green.

신규 pre-existing 실패가 발견되면 이 표에 추가:

| 발견일 | 실패 항목 | 분류 (A~F) | 원인 요약 | 임시 우회 | 책임 PR |
|---|---|---|---|---|---|
| _(none)_ | _(none)_ | — | — | — | — |

## 9. 본 PR 의 변경 항목

- `backend/tests/test_routes.py` — autouse `_clean_safety_env` 픽스처
- `backend/tests/test_brokers_kis_stub.py` — autouse `_clean_kis_env` 픽스처
- `backend/tests/test_ai_routes.py` — autouse `_clean_default_mode` 픽스처
- `docs/ci_green_baseline.md` (신규) — 본 문서
- `README.md` — §6 머지 정책 요약 링크 추가

**운영 코드 변경 0건** — broker / OrderExecutor / route_order / Strategy /
RiskManager / Alembic migration / `.env.example` / `.github/workflows/*` 모두
그대로. 본 PR 은 *테스트 픽스처 + 문서* 만.

## 10. 안전 invariant (본 PR 에서 lock)

| 항목 | 강제 위치 |
|---|---|
| ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING / KIS_IS_PAPER default 변경 0건 | `.env.example` (변경 X) |
| broker / OrderExecutor / route_order import 추가 0건 | 본 PR 변경 0건 |
| Anthropic / OpenAI / httpx / requests import 추가 0건 | 본 PR 변경 0건 |
| `settings.enable_*` mutation 0건 | 픽스처는 *프로세스 env* 만 손대고 settings 객체는 *재로드* |
| EXE 빌드 / desktop-release 실행 0건 | 본 PR 워크플로 변경 0건 |
