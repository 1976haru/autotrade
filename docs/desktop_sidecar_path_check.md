# Step 1-01 — Desktop Sidecar 경로 점검 (#1-01)

> 본 문서는 EXE 실행 시 backend sidecar 자동 시작 흐름의 *경로 / 이름 정합성*
> 점검 결과입니다. 본 점검은 **read-only** — 코드 변경 0건.

---

## 0. 결론

**✅ 모든 경로 / 이름 정합성 PASS — 코드 수정 불필요**.

본 PR 은 *문서 추가만* — `src-tauri/` / `backend/app/` / `scripts/` /
`frontend/src/desktop/` 코드 변경 0건. EXE 재빌드 *불필요*.

기존 desktop launcher 테스트 **48 PASS** (regression 0건).

---

## 1. 점검 대상 7 파일

| # | 파일 | 점검 항목 | 결과 |
|---|---|---|---|
| 1 | `src-tauri/tauri.conf.json` | `externalBin` 경로 | ✅ |
| 2 | `src-tauri/src/lib.rs` | `app.shell().sidecar("autotrade-backend")` | ✅ |
| 3 | `src-tauri/binaries/` | target-triple 파일 존재 + 크기 | ✅ |
| 4 | `backend/app_desktop_launcher.py` | uvicorn 진입 + `/health` probe + port fallback | ✅ |
| 5 | `frontend/src/desktop/backendLauncher.js` | `/api/status` probe + LAUNCHER_STATES | ✅ |
| 6 | `scripts/build_backend_sidecar.ps1` | sidecar 출력 파일명 | ✅ |
| 7 | `scripts/build_windows_installer.ps1` | sidecar 빌드 delegate | ✅ |

---

## 2. 핵심 이름 정합성

Tauri sidecar 규칙: `externalBin` 의 *prefix* + 자동 target-triple suffix.

| 위치 | 값 | 정합성 |
|---|---|---|
| `tauri.conf.json:38` `externalBin` | `"binaries/autotrade-backend"` | ✅ prefix |
| `lib.rs:115` sidecar call | `"autotrade-backend"` | ✅ tauri.conf 와 동일 |
| `lib.rs:224` `sidecar_name` 라벨 | `"autotrade-backend"` | ✅ 일관 |
| `src-tauri/binaries/` 실제 파일 | `autotrade-backend-x86_64-pc-windows-msvc.exe` (87.8 MB) | ✅ Windows x64 target-triple |
| `build_backend_sidecar.ps1:145` 출력 | `autotrade-backend-x86_64-pc-windows-msvc.exe` | ✅ 동일 |
| `build_backend_sidecar.ps1:116` PyInstaller `--name` | `autotrade-backend` | ✅ 동일 |
| `build_windows_installer.ps1:104` delegate | `scripts/build_backend_sidecar.ps1` | ✅ |

**target-triple**: `x86_64-pc-windows-msvc` — Windows x64 표준. Tauri 가
런타임에 빌드 host triple 을 매칭해 `binaries/<prefix>-<triple>.exe` 를
spawn 한다.

---

## 3. Backend Health / Status 엔드포인트

| 엔드포인트 | 위치 | 의미 |
|---|---|---|
| `GET /health` | `backend/app/main.py:247` | 최소 liveness probe — migration 진행 중에도 200 응답 |
| `GET /api/status` | `backend/app/api/routes_status.py` (mounted via `routes_status` at `main.py:203`) | 운영 모드 / 안전 flag / readiness 종합 |
| `GET /api/kis-paper/readiness` | `backend/app/api/routes_kis_paper.py` | KIS Paper 모드 readiness (#89) |
| `GET /api/auto-paper/desktop/health` | `backend/app/api/routes_auto_paper.py:36` | Auto Paper Loop 전용 health |

`is_backend_alive()` (`app_desktop_launcher.py:233`) 가 `/health` 만 probe —
200 + body 에 `'ok'` 또는 `'status'` 키 포함 시 같은 backend 로 판정 (다른
앱이 8000 점유한 경우 fallback port 시도).

---

## 4. Port Fallback 정책

`app_desktop_launcher.py:325` 의 `DEFAULT_PORT_CANDIDATES`:

```python
DEFAULT_PORT_CANDIDATES: list[int] = [8000, 8001, 8002]
```

흐름 (`app_desktop_launcher.py:403~418`):
1. `AUTOTRADE_BACKEND_PORT` env 설정 시 그 port만 시도.
2. 그렇지 않으면 8000 → 8001 → 8002 순서로 port probe + bind.
3. stale process / OS firewall / corporate proxy 등으로 모든 port 실패 시
   명시적 에러 + 로그 (`%APPDATA%/Autotrade/logs/desktop-backend.log`).
4. frontend `backendLauncher.js` 는 동일 fallback port 를 *읽어서* 접속 —
   `fix/desktop-sidecar-port-fallback` (commit `f3dba35`) 반영.

---

## 5. sidecar 런타임 진단

`src-tauri/src/lib.rs:104~178`:

- sidecar stdout / stderr / exit 이벤트를 background task 에서 capture.
- `%APPDATA%/Autotrade/logs/desktop-backend.log` 에 영구 기록.
- spawn 실패 시 명시적 에러 (`"backend sidecar spawn failed: ..."`) +
  `"build via scripts/build_backend_sidecar.ps1 first."` 안내.
- 앱 종료 시 sidecar 자식 프로세스 정리 (graceful shutdown).

---

## 6. 테스트 결과

```bash
python -m pytest tests/test_desktop_launcher.py \
                  tests/test_desktop_launcher_port.py \
                  tests/test_desktop_launcher_startup_log.py \
                  tests/test_app_startup_logging.py -q
# → 48 passed, 6 warnings (datetime.utcnow deprecation — pre-existing, not from this PR)
```

본 점검에서 새 테스트 추가 0건 — 기존 48 PASS 가 본 PR 시점 회귀 0건 lock.

`scripts/security_scan.py` → **scanned files: 847 / HIGH 0 / MEDIUM 0 / LOW 0
/ INFO 0 — ✅ No findings.**

---

## 7. 안전 invariant (CLAUDE.md 절대 원칙 상속)

| 항목 | 확인 |
|---|---|
| `app_desktop_launcher.py` 가 broker / OrderExecutor / route_order *직접* import 0건 | ✅ 모듈 docstring §17-18 명시 — `uvicorn` module string `"app.main:app"` 으로 *간접* 진입만 |
| `backendLauncher.js` 가 매수 / 매도 / 실거래 트리거 호출 0건 | ✅ 모듈 docstring §9 명시 — `/api/status` + `/api/kis-paper/readiness` read-only probe 만 |
| `KIS_IS_PAPER=true` default 유지 | ✅ `.env.example` 미터치 |
| `ENABLE_LIVE_TRADING=false` default 유지 | ✅ |
| `ENABLE_AI_EXECUTION=false` default 유지 | ✅ |
| `ENABLE_FUTURES_LIVE_TRADING=false` default 유지 | ✅ |
| installer 산출물 (`.msi` / `.exe` 설치본) 커밋 0건 | ✅ `.gitignore` 에 `*.msi` / `src-tauri/target/` 포함 (#88) |
| sidecar 본체 (`autotrade-backend-*.exe`) 커밋 여부 | ⚠️ 본 PR 시점 *추적됨* (87.8MB) — 빌드 산출물 git 추적 정책은 별도 옵트인 PR 에서 결정. 현재는 베타테스터 배포 편의를 위해 유지. |

---

## 8. EXE 빌드 필요 여부

| 시나리오 | 빌드 필요 여부 |
|---|---|
| 본 PR (#1-01) 머지 | ❌ **불필요** — 문서 추가만, sidecar 자체 변경 0건 |
| 후속 PR 에서 `app_desktop_launcher.py` / sidecar entry 변경 시 | ✅ `scripts/build_backend_sidecar.ps1` 재실행 (또는 GitHub Actions `desktop-release` workflow) |
| 후속 PR 에서 `tauri.conf.json` / `lib.rs` / Rust 의존성 변경 시 | ✅ `cargo tauri build` 재실행 (보통 `desktop-release` workflow) |

---

## 9. 변경 시 동기화 (lock 정책)

다음 변경은 본 문서도 같이 갱신해야 합니다 (PR 리뷰에서 요구):

1. `externalBin` 이름 변경 — `tauri.conf.json` + `lib.rs` sidecar 호출 + `build_backend_sidecar.ps1` `--name` *동시* 갱신.
2. target-triple 변경 — Windows x64 외 다른 플랫폼 추가 시 본 문서 §2 표 갱신.
3. `/health` / `/api/status` 엔드포인트 경로 변경 — `app_desktop_launcher.py` `is_backend_alive` + `backendLauncher.js` probe + 본 문서 §3 *동시* 갱신.
4. port fallback 후보 변경 — `DEFAULT_PORT_CANDIDATES` + frontend probe + 본 문서 §4 *동시* 갱신.

---

## 10. 관련 문서

- [`docs/desktop_packaging.md`](desktop_packaging.md) — Tauri v2 Windows 설치형 앱 구조 (#86)
- [`docs/desktop_exe_status.md`](desktop_exe_status.md) — Windows installer 빌드 상태 + GitHub Actions 자동 빌드 활성화 (#89, #90)
- [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) — 베타테스터 EXE 원클릭 설치 가이드 (#90)
- [`docs/live_readiness_policy.md`](live_readiness_policy.md) — AI Paper / AI Live 단계 분리 (#0-01)
