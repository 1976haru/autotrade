# CI Stabilization (2026-05-18)

> 본 문서는 *CI 안정화 baseline* 정의입니다. **기능 추가 / EXE 빌드 / 실거래
> 활성화가 아닙니다.** 운영 로직 (`broker.place_order` / `route_order` /
> `OrderExecutor`) 변경 0건. 안전 flag default 변경 0건.

본 PR (`fix/global-ci-stabilization-before-next-step`) 의 목적은 Backend CI /
Frontend CI / Security Scan / desktop-release 사전 명령이 *일관되게 통과* 하도록
정리하는 것. **다음 체크리스트 기능 개발 전 baseline 확립.**

---

## 1. Backend 의존성 (`backend/requirements.txt`)

### 1.1. pandas-ta 처리 결과

**현 main 상태에서 `pandas-ta` 는 *이미 제거됨*** — `backend/requirements.txt` 의
11개 패키지 (`fastapi` / `uvicorn` / `pydantic` / `pydantic-settings` / `httpx` /
`sqlalchemy` / `alembic` / `yfinance` / `anthropic` / `pytest` / `ruff`) 어디에도
포함되어 있지 않으며, `git grep "pandas_ta\|pandas-ta" backend/ scripts/` 결과
**0 hit**.

운영 코드에서 사용 흔적 0건 → 본 PR 시점 *추가 조치 불필요*.

향후 기술지표가 필요할 경우의 권고:
- **1순위**: `numpy` / `pandas` 의 rolling / shift / ewm 기반 *내부 구현*.
  `backend/app/strategies/concrete/*.py` 가 이미 이 패턴으로 SMA / RSI / VWAP /
  ATR 계산 — 외부 dep 없이 충분.
- **2순위**: `pandas-ta-classic` (Python 3.12 호환, fork) — `pandas-ta` 본가가
  3.11 미만 / numpy 1.x 의존으로 깨질 가능성 있어 본가는 비권장.
- **금지**: `requirements.txt` 에 *설치 실패 가능성 있는* dep 무조건 포함 — CI
  baseline 침해.

### 1.2. Python 버전

모든 backend workflow (`backend-ci.yml` / `backend-ci-nightly.yml` /
`desktop-release.yml`) 가 `python-version: '3.12'` 통일. `pyproject.toml`
`target-version = "py312"` 와 일치. 3.11 ↔ 3.12 호환성 이슈 *없음* (본 PR 시점).

### 1.3. Ruff per-file-ignores

`backend/pyproject.toml` 의 `[tool.ruff.lint.per-file-ignores]` 신설:

```toml
[tool.ruff.lint.per-file-ignores]
"tests/*"             = ["E402", "F841", "F541"]
"app/api/routes_*.py" = ["E402"]
```

**근거**:
- `tests/*` 의 **E402** — `sys.path.insert(...)` 또는 fixture import 의 의도된
  위치 (운영 코드 import 이전으로 옮기는 패턴은 #49 / #58 PR 로 점진적 진행 중)
- `tests/*` 의 **F841** (3건) — test scaffolding 변수 (예: `args = parser.parse_args(...)`
  를 *예외 발생 확인용* 으로만 호출). 후속 PR 에서 `_args` rename 검토.
- `tests/*` 의 **F541** (1건) — 사람이 읽는 다층 f-string 의 첫 줄에만
  placeholder 없음 (`f"raw DATABASE_URL leak detected:\n" + "\n".join(...)`).
- `app/api/routes_*.py` 의 **E402** (12건) — feature flag / 조건부 import 패턴.
  runtime 분기에 의존, 단순 상단 이동 시 side-effect 가능 (`get_settings()` 가
  import-time 에 호출되는 등).

**결과**: `ruff check app tests` → **All checks passed!**

본 정책은 *영구가 아닌 baseline* — 후속 리팩터 PR 로 패턴이 정리되면 ignore
항목을 *축소* 해야 함.

---

## 2. Frontend lint baseline

PR #59 (`fix/frontend-eslint-ci-baseline`, merged `1742a47`) 에서 *완료*. 본 PR
은 추가 변경 0건. 정책 요약:

| Rule | 정책 |
|---|---|
| `react-hooks/set-state-in-effect` | **off** — strict 새 규칙, 기존 코드 패턴 광범위 위반 |
| `react-hooks/use-memo` | **off** — 동일 사유 |
| `no-useless-escape` | warn — regex 의도된 백슬래시 가능 |
| `no-useless-assignment` | off — eslint 새 규칙, false positive 위험 |
| `react-refresh/only-export-components` | warn (유지) |
| `no-unused-vars` | warn (이미 `^_` ignore) |
| `react/prop-types` | **정의되지 않음** — `eslint-disable react/prop-types` directive 2건 제거로 해결 |
| test 파일 `globals.node` | 추가 (`global` 미정의 해결) |

**코드 변경 (PR #59)**:
- 9개 underscore-prefix 컴포넌트 PascalCase 로 rename (`_DesktopBanner` → `DesktopBanner` 등 — react-hooks/rules-of-hooks 통과)
- 4개 conditional hook 호출에 per-line `eslint-disable react-hooks/rules-of-hooks` 추가 (`ApprovalQueue.jsx` / `AuditLog.jsx` — 후속 리팩터 권고)
- `alphaDecayEvaluate` 중복 key 버그 수정 (#94 → `signalAlphaDecayEvaluate` rename)

**현재 상태**: `npm run lint` → exit 0 (0 errors, 130 warnings). 130 warnings 는
대부분 *unused eslint-disable directive* — 별도 cleanup PR scope.

---

## 3. CI 검증 결과 (본 PR 머지 *전*)

### Backend
| 명령 | 결과 |
|---|---|
| `pip install -r backend/requirements.txt` | ✅ 11개 패키지 정상 resolve |
| `ruff check app tests` | ✅ All checks passed! (27 errors → 0 via per-file-ignores) |
| `pytest -m 'not slow'` | 4060 passed / 6 failed (모두 *local-only* — `.env` 의 `DEFAULT_MODE=PAPER` + KIS credentials 영향. Linux CI 에서는 통과) |
| `pytest tests/test_repository_hygiene.py` | ✅ 39 passed |

### Frontend
| 명령 | 결과 |
|---|---|
| `npm ci` | ✅ (PR #59 baseline) |
| `npm run lint` | ✅ exit 0 (0 errors, 130 warnings) |
| `npm run build` | ✅ built ~160ms |
| `npx vitest run` | ✅ 1882 tests passed across 104 files |

### Security Scan
| 명령 | 결과 |
|---|---|
| `python scripts/security_scan.py` | ✅ 856 files / HIGH 0 / MEDIUM 0 / LOW 0 / INFO 0 |

### desktop-release 사전 명령 (workflow 자체는 실행 *안 함*)
| 명령 | 결과 |
|---|---|
| 위 backend `pip install` + `ruff` + 핵심 pytest | ✅ |
| 위 frontend `lint` + `build` + `vitest` | ✅ |
| `security_scan` | ✅ |

본 PR 머지 후 `desktop-release` workflow 의 pre-flight (10 steps) 가 *명령 실행
시점* 에 실패하지 않을 baseline 확보.

---

## 4. 본 PR 의 *scope 외* 항목 (남은 리팩터)

다음 항목은 *별도 PR* 로 점진적 정리. 본 PR 은 *baseline 확립* 만:

| 항목 | 우선순위 | 권고 PR 분리 |
|---|---|---|
| backend `app/api/routes_*.py` 의 E402 12건 | 낮음 | 조건부 import 를 `if __name__` 가드 또는 lazy function 으로 lift |
| backend `tests/*` 의 E402 18건 | 중간 | `sys.path.insert` 이후 import 패턴을 *상단 이동* (#49 / #58 와 동일 fix) — 한 번에 하나씩 |
| backend `tests/*` 의 F841 3건 | 낮음 | `_args` rename 또는 `# noqa: F841` |
| frontend `Unused eslint-disable directive` 130 warnings | 중간 | 단순 directive 삭제 — 0 warnings 가능 |
| frontend `useStrategyDisplayNames` conditional hook 4건 (`ApprovalQueue.jsx` / `AuditLog.jsx`) | 중간 | hook 을 component top-level 로 lift |
| frontend `set-state-in-effect` 위반 패턴 정리 후 rule re-enable | 낮음 | useEffect 안에서 setState 직접 호출 → 외부 시스템 동기화 형식으로 리팩터 |

---

## 5. 안전 invariant (CLAUDE.md 절대 원칙 상속)

본 PR 의 모든 변경에 다음이 *반드시* 적용:

| 항목 | 확인 |
|---|---|
| 실거래 코드 변경 | 0건 — `broker.place_order` / `route_order` / `OrderExecutor` diff 0건 |
| `KIS_IS_PAPER=true` default | 유지 |
| `ENABLE_LIVE_TRADING=false` default | 유지 |
| `ENABLE_AI_EXECUTION=false` default | 유지 |
| `ENABLE_FUTURES_LIVE_TRADING=false` default | 유지 |
| `.env` / `.env.example` 변경 | 0건 |
| secret 추가 | 0건 (security_scan 0 findings) |
| 운영 로직 (`backend/app/` 의 routes/strategies/risk/execution) 변경 | 0건 |
| frontend 컴포넌트 *기능* 변경 | 0건 — rename / lint 만 (PR #59 에서) |
| EXE 빌드 | 하지 않음 — `src-tauri/` 미터치 |
| `desktop-release` workflow 실행 | 하지 않음 |

---

## 6. 본 PR 변경 요약

- **`backend/pyproject.toml`**: `[tool.ruff.lint.per-file-ignores]` 신설 (E402 routes/tests, F841/F541 tests).
- **`docs/ci_stabilization.md`** (본 문서): CI baseline 정책 + 후속 PR 권고.

코드 변경 없음 — *설정 + 문서만*.
