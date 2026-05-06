# CI Recovery Report (157)

**시점**: 2026-05-06
**브랜치**: `feature/157-ci-recovery`
**목표**: Backend CI / Frontend CI green, stress test를 nightly로 분리.

## 실패 원인 분석

### 1. Frontend lint — 8 errors (블로킹)

`npm run lint` (eslint 10 + react-hooks plugin)에서 8개 에러:

| 위치 | 룰 | 원인 |
|---|---|---|
| `useApprovals.js:60` | `react-hooks/purity` | `useRef(Date.now())` — render 시 impure 호출 |
| `useAuditLogs.js:133` | `react-hooks/purity` | 동일 |
| `Approvals.jsx:638` | `react-hooks/purity` | render 중 `Date.now()` |
| `AuditLog.jsx:448` | `react-hooks/purity` | 동일 |
| `AuditLog.jsx:978` | `react-hooks/purity` | 동일 |
| `Dashboard.jsx:42` | `react-hooks/purity` | default param `now = Date.now()` |
| `Approvals.jsx:699` | `react-hooks/set-state-in-effect` | clamp 패턴 (의도된 동작) |
| `Approvals.jsx:710` | `react-hooks/set-state-in-effect` | 동일 |

**모두 사전 존재 — 156 PR이 새로 도입한 lint regression은 0건.**

### 2. Backend stress 테스트의 timing-sensitive assertion

`tests/test_stress.py::test_stress_create_pending_approvals_at_volume` 등에서:

```python
elapsed = (time.time_ns() - t0) / 1e9
assert elapsed < LARGE_N * 0.03  # 30ms per submit
```

CI 러너가 cold start이거나 느릴 때 100건 × 30ms 임계를 넘기는 flake 가능.
`test_stress.py` 15 시나리오 모두 timing 또는 대량 데이터에 민감.

### 3. Frontend `Approvals.stress.test.jsx`

500-700 row 렌더링을 jsdom에서 측정 — 일반 CI에서 비결정적 wall time.

## 수정 내역

### Frontend lint 수정 (8 errors → 0 errors)

- `useApprovals.js`: `useRef(Date.now())` → `useRef(null)` + `useEffect`에서 mount 시점 lazy init.
- `useAuditLogs.js::useAdaptivePollingByTopId`: 동일 패턴.
- `Approvals.jsx::HistoryFilter`: render 시 `Date.now()` snapshot에 `eslint-disable-next-line react-hooks/purity` (time-bucket 필터 자체가 현재 시각 의존).
- `AuditLog.jsx`: 두 군데 모두 동일 패턴.
- `Dashboard.jsx::EmergencyStopStuckBanner`: default param `now = Date.now()`은 elapsed-time 표시 본질이라 `eslint-disable-next-line` 적용.
- `Approvals.jsx::useEffect` clamp 두 군데: `react-hooks/set-state-in-effect`에 disable + 사유 주석. list size 변화에만 트리거 + 값 동일성 가드로 cascade 0.

**결과**: `npm run lint` → 0 errors / 55 warnings. 모든 833 테스트 통과 유지.

### Backend stress 분리

`backend/pyproject.toml`:
```toml
[tool.pytest.ini_options]
markers = [
    "slow: stress / volume / timing-sensitive 테스트. nightly CI / manual 전용.",
]
addopts = "-m 'not slow'"
```

`backend/tests/test_stress.py`:
```python
import pytest
pytestmark = pytest.mark.slow
```

**결과**:
- `pytest -q` (default) → 616 passed, 15 deselected (stress 자동 제외).
- `pytest -m slow -v` → 15 passed (nightly에서 실행).

### Workflow 분리

| 파일 | 트리거 | 내용 |
|---|---|---|
| `.github/workflows/backend-ci.yml` | push/pr to main, develop, feature/** | `ruff check` + `pytest -q` (slow 제외). 10분 timeout. |
| `.github/workflows/backend-ci-nightly.yml` (신설) | `cron: '0 19 * * *'` (KST 04:00) + `workflow_dispatch` | `pytest -m slow -v`. 30분 timeout. |
| `.github/workflows/frontend-ci.yml` | push/pr | `npm run lint` + `npx vitest run --exclude '**/*.stress.test.jsx'` + `npm run build`. 10분 timeout. |
| `.github/workflows/frontend-ci-nightly.yml` (신설) | cron + dispatch | `npx vitest run` against `Approvals.stress.test.jsx`. 30분 timeout. |

## 검증 결과 (로컬)

| 검증 | 결과 |
|---|---|
| `cd backend && python -m pytest -q` | **616 passed, 15 deselected** in 6.81s |
| `cd backend && python -m pytest -m slow -v` | **15 passed** in 3.59s |
| `cd backend && python -m ruff check app tests` | **All checks passed** |
| `cd frontend && npm run lint` | **0 errors / 55 warnings** |
| `cd frontend && npx vitest run --exclude '**/*.stress.test.jsx'` | **831 passed (22 files)** |
| `cd frontend && npx vitest run` (전체) | **833 passed (23 files)** |
| `cd frontend && npm run build` | **vite built in 110ms**, 342kB → 98kB gzipped |

## main 영향 평가

- **feature 브랜치 CI 실패가 main에 영향 주는지**: ✅ **No**.
  - `branches: [main, develop, 'feature/**']`로 워크플로우가 트리거되지만 main 브랜치 보호는 PR merge 시점의 status check만 본다.
  - 현재 main은 사용자 직접 merge로 진행된 상태 (PR/status check 없는 fast-forward merge). main의 CI는 push 후 따로 돈다.
  - 본 PR은 main의 CI가 green이 되도록 lint + stress 분리를 수행. 머지 후 main의 push CI run이 green이 됨이 기대 동작.

## 미수행 (사용자 명시 보호)

- ❌ `.env`, API key, account number, App Secret 변경 0건.
- ❌ 실거래 활성화 (LIVE_TRADING / LIVE_AI / FUTURES_LIVE) 변경 0건.
- ❌ 파일 대량 삭제 0건.
- ❌ `git reset --hard`, force push 0건.

## 다음 단계

1. 본 PR 머지 후 main의 새 CI run이 green인지 확인 (push trigger).
2. 다음날 KST 04:00에 nightly stress workflow가 실제로 트리거되는지 확인.
3. CI 복구 후 사용자 요청대로 지능형 에이전트 MUST 기능 재개.

## 관련 문서

- [`docs/stress_test_report.md`](stress_test_report.md) — 분리된 stress 시나리오 정의
- [`docs/backlog.md`](backlog.md) 16번 항목 — lint disable 적용 사유 / 향후 정식 fix
- [`CLAUDE.md`](../CLAUDE.md) — 작업 원칙
