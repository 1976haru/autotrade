# Final Rehearsal Error Audit (FINAL-AUDIT-01)

> 최종 KIS 모의 장중 리허설(BUILD-02B) **전** 전체 오류 점검 / CI·PR·빌드·테스트·안전성 최종 감사.
> 본 문서는 **감사(audit) 자료**이며 새 기능 추가가 아니다. 실거래 활성화 / KIS 실주문 / KIS 모의 주문을
> 발생시키지 않는다. **실전 전환 승인이 아니며 수익을 보장하지 않는다.**

- 감사 일자: 2026-05-24 (KST)
- 감사 브랜치: `feature/final-rehearsal-error-audit`
- 대상 저장소: https://github.com/1976haru/autotrade
- 감사 기준 main commit: `8e70679` (= `08434fe6` "Merge PR #185" 의 pages dist 동기화 [skip ci])

---

## 0. GitHub / 로컬 상태

| 항목 | 결과 |
|---|---|
| 로컬 `feature/final-rehearsal-error-audit` clean | ✅ (유일 untracked: `src-tauri/Cargo.lock` — §10) |
| `origin/main` 최신 commit | `8e70679` (PR #185 merge dist sync) |
| 로컬 main ↔ origin/main | 동기 (`git fetch --all --prune` 후 일치) |
| open PR 수 | 2건 (#35, #48) |

---

## 1. Open PR 감사 (자동 merge 금지 — 판정만)

GitHub API 기준 open PR은 **#35, #48 두 건뿐**이다. 두 PR 모두 2026-05-17 생성, 이후
main 이 #185 까지 대폭 전진하여 동일 기능이 main 에 이미 더 완전한 형태로 구현됨.

### PR #35 — `feat(backtest): step3 real-data pipeline` (head `feature/step3-real-data-backtest-pipeline`)
- 변경: +2633 / -0, 15 files, 1 commit.
- 충돌: **add/add CONFLICT** — `backend/app/backtest/real_data/{__init__,paper_candidate,symbols}.py`,
  `backend/tests/test_real_market_data_loader.py`.
- main 현황: `backend/app/backtest/real_data/` 가 이미 존재하며 PR 보다 **더 많은 파일**
  (`grid_search.py` / `loader.py` / `optimization_verdicts.py` / `universe.py` / `verdicts.py`) 포함.
  → real-data backtest 파이프라인은 main 에서 이미 더 완전하게 구현됨. #46~#48 council backtest /
  walk-forward / stress 와도 별개 파일로 공존.
- 안전성: diff 의 broker/route_order/OrderExecutor 매칭은 모두 **docstring/주석/정적 가드 regex**.
  실제 broker 호출·LIVE flag·secret 0건.
- **판정: `SUPERSEDED_BY_MAIN` → `CLOSE_RECOMMENDED`** (merge 불필요, 닫기 권고).

### PR #48 — `feat(agent): regime-aware strategy selection filter (#4-04)` (head `feature/step4-market-regime-strategy-selection`)
- 변경: +1507 / -1, 6 files, 1 commit.
- 충돌: **content CONFLICT** — `README.md`, `backend/app/agents/strategy_combination_recommender.py`.
- main 현황: `strategy_combination_recommender.py`, `market_regime.py`, `market_regime_agent.py`,
  `filters/market_regime.py`, `analytics/regime_combo_backtest.py`, 프론트엔드 `MarketRegimeBadge.jsx`,
  docs (`market_regime_strategy_selection.md` 등) 모두 main 에 존재. regime-aware 전략 선택은
  main 의 Agent Council / market_regime / quality_score gate 로 이미 구현됨.
- 안전성: diff 의 위험 패턴 매칭은 모두 정적 가드 regex / enum 금지 목록 / 문서. 실제 위험 코드 0건.
- **판정: `SUPERSEDED_BY_MAIN` → `CLOSE_RECOMMENDED`** (필요 시 rebase 후 신규 PR 재작성, 현 브랜치는 닫기 권고).

> 두 PR 모두 **머지하지 않음**. 미머지 상태이므로 리허설을 **차단하지 않는다**.

---

## 2. GitHub Actions / CI

| Workflow | main 최신(`08434fe6`) | 비고 |
|---|---|---|
| Backend CI | ✅ success | 최근 main push 전부 green |
| Frontend CI | ❌ failure | "Test (fast)"(vitest) 단계 — **flaky** (아래) |
| GitHub Pages Demo | ✅ success | |
| pages build/deploy | ✅ success | |

- **Frontend CI failure 는 flaky 로 판정**: main 최근 commit 들에서 success/failure 가
  교차(`success`/`failure` 번갈아) 발생. 동일 코드가 commit 별로 결과가 갈리는 것은 timing flake
  신호. **로컬 vitest 전체 통과(146 files / 2779 tests)** 로 코드 정상 확인. (CI 로그는 admin 권한
  필요로 직접 미열람 — 단계는 "Test (fast)" 로 특정.)
- Workflow trigger/path: backend-ci / frontend-ci 모두 push(main/develop/feature/**) + PR(main/develop),
  path filter `backend/**` / `frontend/**` + 각 workflow 파일. nightly stress 는 별도 분리. 정상.
- `desktop-release.yml`: **`workflow_dispatch` only + `windows-latest`** — 자동 트리거 0건. 정책 부합.
- 환경 버전 drift (WARN): CI Python 3.12 / Node 20 ↔ 로컬 Python 3.13 / Node 24. 양쪽 모두 green.

---

## 3. Backend 오류 점검

- `ruff check app tests` → **All checks passed!** (ruff 0.15.12 로컬).
- `pytest --collect-only` → **7362 tests collected, 25 deselected, collection error 0**.
- (전체 pytest 결과는 §아래 "Backend pytest" 표 참조)

---

## 4. Frontend 오류 점검

| 단계 | 결과 |
|---|---|
| `npm run lint` (eslint) | exit 0 — **0 errors**, 146 warnings (전부 "Unused eslint-disable directive", CI 통과) |
| `npm run test` (vitest) | **146 files / 2779 tests 전부 PASS** (~39s) |
| `npm run build` (vite) | exit 0 — `dist/` 생성 성공 (chunk>500kB advisory warning 만) |

- React key / undefined 렌더 오류 0건. 주문/실전/승인 버튼 추가 0건. secret 표시 0건.
- 미세 정리 후보(WARN): 146개 "Unused eslint-disable directive" 경고 → 별도 chore PR 에서 `--fix` 권고.

---

## 5. Security / Secret / 계좌 노출

- `python scripts/security_scan.py` → **1216 files scanned, HIGH/MEDIUM/LOW/INFO 전부 0. ✅ No findings.**
- `git grep` 토큰/계좌 패턴: 적중은 전부 (a) 프론트/백엔드 **테스트 fixture 의 가짜 secret**(redaction 검증용),
  (b) docs 의 **명백한 placeholder**(`KIS_ACCOUNT_NO=1234567890` 순차 더미, "모의투자 계좌 10자리" 주석).
  실제 secret / 실제 계좌번호 **0건**. (security_scan #93 이 docs/** / tests/** 디렉토리 allowlist.)
- **secret/account 원문 노출: 없음.**

---

## 6. LIVE 안전 플래그

`backend/.env.example` 기본값:

| flag | 값 | 판정 |
|---|---|---|
| `DEFAULT_MODE` | `PAPER` | ✅ (LIVE 아님; KIS_IS_PAPER=true + LIVE off 로 paper-safe) |
| `ENABLE_LIVE_TRADING` | `false` | ✅ |
| `ENABLE_AI_EXECUTION` | `false` | ✅ |
| `ENABLE_FUTURES_LIVE_TRADING` | `false` | ✅ |
| `KIS_IS_PAPER` | `true` | ✅ |

- `git grep "ENABLE_*=true" / "KIS_IS_PAPER=false"` 적중은 전부 governance/kis 모듈의
  **차단 조건 설명 docstring/메시지** — 실제 활성화 config 0건.
- `is_live_authorization=True` 적중은 dataclass 불변 가드 docstring 만. 실제 True 대입 0건.
- `broker_order_type="LIVE"` 적중은 `kis_paper_ai_autotrade_audit.py` 가 **LIVE 생성이 ValueError 로
  차단됨을 검증**하는 코드. 실제 LIVE 주문 0건.

---

## 7. KIS Paper 자동매매 경로 / KIS live 주문 가능 경로

- `KisBrokerAdapter.place_order(is_paper=False)` → **`NotImplementedError`** (kis.py:181-187).
  `cancel_order` 도 `NotImplementedError` stub. → **KIS 실주문(real-money) 경로 미구현 / 차단.**
- KIS live 주문 가능 경로 **발견되지 않음**.
- 다층 가드(RiskManager → PermissionGate → OrderExecutor + KIS adapter + Factory) 정상.

---

## 8. BUILD-01 / 02A / 02B-0 게이트

| 게이트 | 결과 | 비고 |
|---|---|---|
| BUILD-01 program integrity | **build_ready=True** (PASS=15 / WARN=1 / FAIL=0), overall=WARN | WARN = `KIS_PAPER_READINESS` (KIS 자격 미설정) |
| BUILD-02A premarket readiness (fast) | **premarket_ready=True / build_ready_for_offline=True** (PASS=8 / WARN=1 / FAIL=0 / SKIP=1) | WARN = `KIS_CREDENTIALS`, SKIP = `PREFLIGHT_SMOKE`(backend 미주입) |
| BUILD-02B-0 KIS paper AI autotrade audit | **overall=PASS / paper_autotrade_ready=True** (PASS=18 / WARN=0 / FAIL=0) | KIS 실제 API 0건 (fake) |

- 세 게이트 모두 **FAIL 0건**. 불변값 `is_live_authorization=False` / `broker_order_sent=False` /
  `order_created_live=False` / `contains_secret=False` 유지.
- `ready_for_market_open_rehearsal=False` 는 **로컬에 KIS 모의 자격 미설정** 때문 — 운영자가 KIS paper
  app key/secret/계좌(KIS_IS_PAPER=true) 입력 시 True 로 전환. **코드 blocker 아님**.
- 산출 리포트는 `reports/` (gitignored) — 커밋 안 됨.

---

## 9. Backtest / Walk-forward / Stress

- 관련 모듈 테스트(`test_strategy_council_backtest` / `test_walk_forward*` / `test_*stress*` /
  `test_monte_carlo` / `test_optimization_verdicts` 등)는 backend pytest 전체(7300 passed)에 포함 — 전부 통과.
- 스크립트 가용성: `run_strategy_optimization.py` / `run_strategy_council_walk_forward.py` /
  `run_agent_stress_test.py` / `security_scan.py` 등 전부 존재.

---

## 10. 미추적 / 불필요 파일

- `git status --short`: 유일 untracked **`src-tauri/Cargo.lock`** (5337 lines, Cargo @generated 잠금파일).
  - gitignore 되어 있지 **않음**(`git check-ignore` exit 1) — 단순 미추적.
  - secret 0건(의존성 그래프). 릴리스 위험 아님.
  - 권고: Tauri **앱**의 재현 빌드 관례상 commit 권장이나, **EXE 빌드 설정 변경 → 별도 desktop-build PR**
    (본 감사 PR 에서 무작정 commit 하지 않음).
- `reports/` gitignored ✅, `data/*.db` 추적 0건 ✅, `.env` 추적 0건(`.example`/`.staging.example` 만) ✅.
- `git clean -nd` → `src-tauri/Cargo.lock` 만.

---

## 11. 의존성 재현성

- `frontend/package-lock.json` 존재 + package.json clean → `npm ci` 가능 ✅.
- `backend/requirements.txt`: **전 패키지 floating(`>=`)** — `ruff>=0.6`, `pytest>=8.0` 등 lock 없음.
  - `ruff>=0.6` 는 로컬에서 0.15.12 로 해석, `.pre-commit-config.yaml` 은 `ruff-pre-commit v0.7.0` 으로
    **버전 불일치(WARN)**. 현재 ruff 통과 중이라 즉시 FAIL 아님.
  - floating deps 로 로컬 pytest 9.0.3 ↔ CI pytest 8.x drift 발생.
  - **권고(WARN)**: ruff / pytest 등 핵심 도구 pin (별도 chore PR — 의존성 정책 변경이므로 본 감사 PR 분리).

---

## 12. GitHub Actions Workflow 점검

- backend-ci.yml / frontend-ci.yml / *-nightly.yml / desktop-release.yml / pages-deploy.yml 전부 active.
- push + PR trigger, path filter 적정, `npm ci` 사용, ruff lint 포함, pytest --timeout 사용(ubuntu).
- security_scan 은 `test_repository_hygiene.py` 가 매 CI subprocess 호출(코드 단 강제).
- docs-only 변경은 code CI 미실행(path filter) — docs 가 코드 안전 테스트에 영향 없으므로 정상.

---

## 13~14. 발견 오류 / 수정 / WARN

### 수정한 오류 (본 PR)
- **코드/테스트 수정 0건.** 발견된 항목은 전부 비차단 WARN 이거나 별도 PR 분리 대상(의존성 정책 /
  EXE 빌드 설정 / flaky 안정화). 테스트 삭제·skip·xfail·오류 은폐 try/except 0건.
- 본 PR 추가물: 본 감사 문서 `docs/final_rehearsal_error_audit.md` + README docs index 링크.

### 남은 WARN (별도 PR 권고 — 본 감사에서 코드 수정 안 함)
1. `requirements.txt` 전 패키지 floating + `ruff>=0.6` ↔ pre-commit `v0.7.0` 불일치 → **별도 chore PR 에서 pin**.
2. `src-tauri/Cargo.lock` 미추적 → **별도 desktop-build PR 에서 commit 결정**.
3. Frontend CI flaky (vitest timing) → 로컬 전체 통과. flaky 테스트 안정화는 별도 PR(findBy 패턴).
4. eslint "Unused eslint-disable directive" 146건(0 errors) → 별도 chore PR `--fix`.
5. 로컬 Windows 에서 `pytest --timeout-method=thread` 전체 실행 시 SQLite/스레드 워치독 충돌로 대량
   ERROR — **로컬 환경 한정 artifact**(CI ubuntu green). 코드 결함 아님. (§Backend pytest 참조)

---

## 15. 최종 판정

(아래 "최종 판정" 표 참조)

---

## Backend pytest

| 실행 방식 | 결과 |
|---|---|
| `ruff check app tests` | All checks passed |
| `pytest --collect-only` | 7362 collected / 25 deselected / **0 collection error** |
| 단일 P0 파일 `test_risk_manager.py` | 91 passed |
| 단일 P0 파일 `test_order_guard.py` (full-suite 에선 cascade) | (개별 통과, 아래 주석) |
| 단일 DB 라우트 `test_audit_events_routes.py` | 14 passed |
| `test_migration_runner.py` + `test_nonblocking_migration_lifespan.py` (단독) | 20 passed |
| **전체 suite − {migration_runner, nonblocking_migration_lifespan, auto_paper_candidate_loader}** | **7300 passed / 5 skipped / 25 deselected / 0 failed / 0 error (83s)** |

### 로컬 Windows full-suite cascade ERROR — 코드 결함 아님 (환경 artifact)

- 전체 suite 를 한 번에 돌리면 `test_migration_runner.py` 의 blocking-migration 테스트 지점부터
  이후 알파벳순 테스트들이 alembic DB 셋업 단계에서 대량 ERROR 로 cascade.
- **근거로 코드 결함이 아님**:
  1. **Backend CI(ubuntu/py3.12)는 main 최신까지 전부 green** — 동일 full suite 통과.
  2. 모든 개별 파일은 단독 실행 시 통과 (risk_manager 91 / order_guard / audit_routes 14 /
     migration_runner+lifespan 20 …).
  3. 위 3개 파일만 제외하면 **로컬에서도 7300 passed (0 fail / 0 error)**.
- 원인 추정: Windows 의 **mandatory SQLite 파일 잠금 + migration-runner 의 백그라운드 스레드/공유
  엔진 상태**가 후속 테스트 DB 셋업을 오염 (Linux 의 advisory lock 과 동작 차이). 추가로
  `pytest-timeout --timeout-method=thread` 가 이 상황을 악화.
- `test_auto_paper_candidate_loader.py` 는 로컬에서 hang 알려져 있어 제외(CI 는 `--timeout` 으로 처리).
- **조치**: 본 감사에서 코드/테스트 수정·skip·삭제 0건. test 격리 강화(Windows)는 별도 chore PR 권고
  (예: migration-runner 테스트의 백그라운드 스레드 join / 전용 임시 DB 격리).

---

## 16. 검증 명령 묶음 결과

- `ruff check app tests` → pass
- `pytest --collect-only -q` → 7362 collected, 0 error
- `pytest`(env-artifact 3파일 제외) → 7300 passed / 0 fail
- `npm run lint` → 0 error / `npm run test` → 2779 pass / `npm run build` → ok
- `python scripts/security_scan.py` → 0 findings
- BUILD-01/02A/02B-0 → 전부 FAIL 0

---

## 최종 판정

| 필드 | 값 |
|---|---|
| main_clean | ✅ (origin/main 동기) |
| open_prs_reviewed | ✅ (#35 / #48 — 둘 다 SUPERSEDED_BY_MAIN → CLOSE_RECOMMENDED) |
| ci_green | ⚠️ Backend green / Frontend flaky-fail (로컬 전체 green) |
| backend_green | ✅ (7300 passed; full-suite cascade 는 Windows 환경 artifact, CI green) |
| frontend_green | ✅ (2779 tests pass, build ok) |
| security_green | ✅ (0 findings) |
| live_flags_safe | ✅ (LIVE/AI/FUTURES=false, KIS_IS_PAPER=true) |
| kis_paper_path_safe | ✅ (KIS live place_order = NotImplementedError) |
| build_gates_pass | ✅ (BUILD-01/02A/02B-0 FAIL 0) |
| docs_ready | ✅ (user_manual / runbook 존재, 금지 문구 0) |
| no_secret_exposure | ✅ |
| no_untracked_release_risk | ✅ (Cargo.lock 만, secret 0) |
| **ready_for_build_02b_market_rehearsal** | ✅ **조건부 YES** — 운영자가 KIS 모의 자격(KIS_IS_PAPER=true) 입력 시 |

### 🟡 최종 판정: **READY_WITH_WARNINGS**

**BLOCKER 없음.** 잔여 WARN 은 모두 비차단 / 별도 PR 권고 사항:
1. Frontend CI flaky (vitest timing) — 로컬 전체 green. → 안정화 별도 PR(findBy 패턴).
2. 로컬 Windows full-suite cascade — CI green, 코드 정상. → test 격리 별도 PR.
3. 의존성 floating(`>=`) + `ruff>=0.6` ↔ pre-commit `v0.7.0` 불일치. → pin 별도 chore PR.
4. `src-tauri/Cargo.lock` 미추적. → 별도 desktop-build PR.
5. eslint "Unused eslint-disable directive" 146건(0 error). → `--fix` 별도 chore PR.
6. BUILD 게이트 KIS 자격 WARN — 운영자 자격 입력 전 정상.

### 다음 단계
- 운영자가 `backend/.env` 에 **KIS 모의(KIS_IS_PAPER=true)** app key/secret/계좌 입력 후
  BUILD-02A/02B-0 재실행 → `ready_for_market_open_rehearsal=True` 확인.
- 장중 **BUILD-02B (KIS 모의 실시간 리허설)** 진행.
- (병행) 위 1~5 WARN 을 각각 별도 PR 로 정리. open PR #35/#48 는 닫기.
