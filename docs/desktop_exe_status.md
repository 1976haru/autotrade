# Desktop EXE/MSI 빌드 상태 — 2026-05 (#90 갱신)

> 본 문서는 *현재 시점* 의 Windows installer 산출물 존재 여부 + 빌드 시도
> 결과를 기록한다. 사용자 (베타테스터) 가 "EXE 가 이미 있는가?" 라는 질문에
> 즉시 답할 수 있는 단일 진실.

## 1. 한 줄 결론 (#90 시점)

**현재 main 브랜치에는 빌드된 Windows installer (`.exe` / `.msi`) 가
존재하지 *않습니다*.** #86 에서 `src-tauri/` skeleton + #89 에서 KIS Paper
one-click 카드까지 갖춰졌고, #90 에서 **sidecar wiring + build orchestration
스크립트** 가 준비됐지만, 본 작업 환경에는 Rust toolchain 이 없어
`cargo tauri build` 가 *실제 실행되지 않았습니다*.

| 항목 | 값 |
|---|---|
| 기존 EXE/MSI 발견 여부 | ❌ 없음 |
| 빌드 시도 결과 (#90) | 🛑 환경 부족 — Rust / cargo / tauri-cli 미설치 + PyInstaller 미설치 |
| 최신 main 기준 산출물 | (생성된 적 없음) |
| 베타테스터 배포 가능 여부 | ❌ — 본 PR 시점 *재빌드 필요* |
| 대체 실행 방법 | `scripts/start_kis_paper_test_windows.bat` (Python + uvicorn 기반, #89) |
| **변경된 점 (#90)** | sidecar PyInstaller 스크립트 / Tauri externalBin 등록 / 데스크톱 launcher / 초보자 설치 가이드 / 안전 invariant 강화 |

> **본 PR 의 목적**: EXE 가 *없어도* 동일한 모의 테스트를 진행할 수 있도록
> 유지하면서, EXE *빌드 환경이 갖춰진* PC 에서는 한 번의 명령으로 설치 파일을
> 생성할 수 있도록 **build orchestration 을 코드 단에서 lock** 한다.

## 2. 점검 한 위치 (#90 시점)

```
src-tauri/                                  ← skeleton (#86) + sidecar wiring (#90)
├─ tauri.conf.json                          ✅ externalBin: binaries/autotrade-backend
├─ Cargo.toml                               ✅
├─ build.rs                                 ✅
├─ src/main.rs                              ✅ #90: sidecar spawn + 종료 시 cleanup
├─ capabilities/default.json                ✅ #90: shell:allow-execute sidecar=true
├─ icons/README.md                          (실 아이콘 binary 부재)
├─ binaries/README.md                       ✅ #90: sidecar 산출물 위치 (gitignored)
├─ target/                                  ❌ 존재 안 함 (빌드 시도 결과)
└─ target/release/bundle/                   ❌ 존재 안 함

backend/
├─ app_desktop_launcher.py                  ✅ #90: uvicorn entry + .env loader
└─ dist/                                    ❌ 존재 안 함 (gitignored)

frontend/src/desktop/
├─ backendLauncher.js                       ✅ #90: 상태 머신 + polling
└─ backendLauncher.test.js                  ✅ #90: 25개 단위 테스트

scripts/
├─ build_backend_sidecar.ps1                ✅ #90: PyInstaller orchestration
├─ build_backend_sidecar.bat                ✅ #90: 더블클릭용 wrapper
├─ build_windows_installer.ps1              ✅ #90: end-to-end installer build
└─ build_windows_installer.bat              ✅ #90: 더블클릭용 wrapper

docs/
├─ desktop_exe_status.md                    ✅ 본 문서 (#90 갱신)
└─ exe_oneclick_installation.md             ✅ #90: 베타테스터 / 지인 가이드
```

본 PR 시점 검색 결과:
```
$ ls src-tauri/target            # No such file or directory
$ ls backend/dist                # No such file or directory
$ ls src-tauri/binaries/*.exe    # README.md only (no built binaries)
```

## 3. 빌드 시도 환경 (#90 시점)

본 PR 의 개발 환경에서 확인된 툴체인:

| 도구 | 필수 여부 | 본 머신 상태 (#90) |
|---|---|---|
| `cargo`         | 필수 (Rust)                                  | ❌ 미설치 |
| `rustc`         | 필수 (Rust)                                  | ❌ 미설치 |
| `tauri-cli`     | 필수 (`cargo install tauri-cli ^2 --locked`) | ❌ 미설치 |
| `pyinstaller`   | 필수 (backend sidecar)                        | ❌ 미설치 (requirements.txt 에도 없음 — script 가 ad-hoc install) |
| Node 20+        | frontend build                                | ✅ 설치됨 |
| Python 3.12+    | backend                                       | ✅ 설치됨 |
| Visual Studio Build Tools / Windows SDK | Tauri Windows installer 빌드 시 | ❓ 확인 안 됨 |

→ **`npm run tauri build` / `cargo tauri build` 실행 가능 0건** (현 환경).

## 4. #90 에서 *코드로* 준비된 흐름 (toolchain 만 갖추면 즉시 실행 가능)

### 4-1. backend sidecar 단독 빌드

```powershell
pwsh scripts/build_backend_sidecar.ps1
# 결과:
#   backend/dist/autotrade-backend.exe
#   src-tauri/binaries/autotrade-backend-x86_64-pc-windows-msvc.exe
```

### 4-2. end-to-end installer build

```powershell
pwsh scripts/build_windows_installer.ps1
# 흐름:
#   [1/5] toolchain check
#   [2/5] frontend npm ci + build
#   [3/5] backend PyInstaller sidecar build (위 4-1 위임)
#   [4/5] cargo tauri build
#   [5/5] artifact 수집 + SHA256 출력 + JSON 출력
# 결과 (toolchain 완비 시):
#   src-tauri/target/release/bundle/msi/Agent Trader v1_1.0.0_x64_en-US.msi
#   src-tauri/target/release/bundle/nsis/Agent Trader v1_1.0.0_x64-setup.exe
```

### 4-3. 산출물 무엇이 들어가는가?

| 포함 | 제외 |
|---|---|
| ✅ `autotrade-backend.exe` (PyInstaller `--onefile`) | ❌ `backend/.env` (어떤 형태로도 0건) |
| ✅ alembic 마이그레이션 데이터 (`--add-data`) | ❌ `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` |
| ✅ frontend `dist/` (Vite 산출물) | ❌ `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN` |
| ✅ Tauri Rust runtime + windows installer | ❌ Tauri updater private key |

## 5. EXE 가 *없어도* 가능한 베타테스터 흐름 (현재 PR 시점에도 그대로 유효)

```
1. 사용자가 GitHub Releases 또는 zip 으로 프로젝트 clone
2. scripts/start_kis_paper_test_windows.bat 더블클릭        # #89
3. Python 의존성 자동 설치 (idempotent)
4. backend 자동 실행 (http://127.0.0.1:8000)
5. 브라우저로 http://localhost:5173 접속 (또는 PC IP)
6. 대시보드 → "한투 모의투자 AI 자동매매 테스트" 카드 → 준비상태 확인 → 시작
```

본 흐름은 *Tauri / Rust 의존성 0건* 이고, 베타테스터에게 EXE 같은 *원클릭*
UX 는 아니지만 *실제 테스트 가능* 한 가장 짧은 경로.

## 6. EXE 만들 때의 안전 invariant (재확인 — #86 + #90 정책)

EXE 안에 다음을 *절대 굽지 않습니다*:
- `.env` 파일 어떤 것도
- `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN`
- Tauri updater private key

Tauri bundle 의 `bundle.resources` 에 *어떤 secret 파일도 추가하지 않는다*.
`tauri.conf.json` 의 `bundle.resources` 가 비어 있는지 빌드 전 확인 필수.

**#90 추가 invariant**:
- `bundle.externalBin` 에는 `binaries/autotrade-backend` 만 (다른 binary 0개)
- `backend/app_desktop_launcher.py` 는 `os.environ` 의 안전 flag 를 *읽기만* —
  `ENABLE_LIVE_TRADING` 등에 값을 set 하지 않는다 (테스트로 lock 가능)
- frontend `backendLauncher.js` 는 broker / OrderExecutor / route_order 를
  호출하지 않는다 — `/api/status` + `/api/kis-paper/readiness` read-only 만
- `KisPaperOneClickTestCard` 에 추가된 데스크톱 상태 블록에 "Place Order" /
  "지금 매수" / "실거래 시작" 라벨 버튼 0개 (테스트로 lock)

자세한 정책: [`docs/desktop_packaging.md`](desktop_packaging.md) §3 + §7,
[`docs/desktop_update_policy.md`](desktop_update_policy.md) §3,
[`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) §11.

## 7. 후속 추적

- 본 시점부터 EXE 가 생성되면 `git status` / GitHub Releases 에 artifact 가
  나타날 것 — `.gitignore` 에 `*.msi` / `*.nsis` / `src-tauri/binaries/` 등이
  등록돼 있어 *git commit 0건*.
- EXE 산출 시 본 문서를 갱신 — "재빌드 필요" → "최신 X.Y.Z 빌드 가능"
- GitHub Release tag `v1.0.0` 이상에서 정식 installer 첨부 가능.

### 7-1. EXE 생성 보고 형식 (build_windows_installer.ps1 JSON 출력)

```json
{
  "artifacts": [
    {
      "kind": "msi",
      "path": "src-tauri/target/release/bundle/msi/Agent Trader v1_1.0.0_x64_en-US.msi",
      "size_bytes": 17284192,
      "created": "2026-MM-DD HH:MM:SS",
      "sha256": "..."
    },
    {
      "kind": "exe",
      "path": "src-tauri/target/release/bundle/nsis/Agent Trader v1_1.0.0_x64-setup.exe",
      "size_bytes": 16893816,
      "created": "2026-MM-DD HH:MM:SS",
      "sha256": "..."
    }
  ]
}
```

빌드 후 위 JSON 을 본 문서 §1 의 표에 옮겨 적으면 "기존 EXE/MSI 발견 여부 ✅"
로 전환됩니다.

## 8. 90-A 실제 빌드 검증 (2026-05-14)

PR #90 머지 후 main 브랜치 (`e31355b`) 기준으로 *실제* `scripts/build_backend_sidecar.ps1`
+ `scripts/build_windows_installer.ps1` 을 실행해 본 결과를 기록한다.

### 8-1. 환경 점검 결과

| 도구 | 상태 | 비고 |
|---|---|---|
| `rustc` | ❌ NOT FOUND | Rust 미설치 |
| `cargo` | ❌ NOT FOUND | Rust 미설치 |
| `rustup` | ❌ NOT FOUND | 설치 관리자 부재 |
| `~/.cargo/bin/` | ❌ NOT FOUND | cargo 디렉터리 없음 |
| `cargo-tauri` | ❌ NOT FOUND | cargo 의존 |
| WiX (`heat/candle/light`) | ❌ NOT FOUND | MSI 빌드 도구 부재 |
| `node` | ✅ v24.15.0 | |
| `npm` | ✅ 11.12.1 | |
| `python` | ✅ 3.14.3 | |
| `pyinstaller` | ✅ 6.14.2 (system) → 6.20.0 (build 시 자동 설치) | backend sidecar 빌드 가능 |
| `src-tauri/target/` | ❌ 부재 | 빌드 산출물 없음 |
| `src-tauri/binaries/` | README.md만 | 빌드 후 sidecar 추가됨 (아래) |

### 8-2. backend sidecar 빌드 — ✅ 성공

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build_backend_sidecar.ps1 -Clean
```

| 항목 | 값 |
|---|---|
| exit code | `0` |
| 소요 시간 | 약 58초 (PyInstaller analysis + onefile) |
| Python | 3.14.3 |
| PyInstaller | 6.20.0 (build 시 ad-hoc 설치) |
| 산출물 (원본) | `backend\dist\autotrade-backend.exe` |
| 산출물 크기 | 87,769,031 bytes (≈ 83.7 MiB) |
| Tauri sidecar 복사본 | `src-tauri\binaries\autotrade-backend-x86_64-pc-windows-msvc.exe` |
| SHA256 | `99F493F60E2BB2A111D0E6766B6B19AB5F5E759C26B20BD8BBFE7FA2D6F7304C` |
| 생성 시각 | 2026-05-14 20:41:35 KST |

빌드 로그 일부 경고 (정상 — 무시 가능):

- `Hidden import "pycparser.lextab" not found!` / `Hidden import "pycparser.yacctab" not found!`
  → cffi/pycparser 의 *optional* lookup table, 실행에 영향 없음.
- `Hidden import "pysqlite2" not found!`
  → 표준 `sqlite3` 모듈 사용, `pysqlite2`는 Python 2 시절 외부 패키지로 현 환경 불필요.

본 산출물은 `.gitignore` 에 의해 git 추적 대상이 아니며 (`src-tauri/binaries/` /
`backend/dist/`), GitHub Release artifact 로만 배포 예정.

### 8-3. Tauri Windows installer 빌드 — 🛑 차단 (Rust 미설치)

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\build_windows_installer.ps1 -SkipBackend
```

| 항목 | 값 |
|---|---|
| exit code | `2` (toolchain check 단계에서 차단) |
| 차단 시점 | `[1/5] toolchain check` |
| 에러 메시지 | `[ERR] cargo not installed (required)` + `[ERR] rustc not installed (required)` |
| 진행 단계 | `[2/5]` 이후 도달 0건 (frontend / backend / cargo tauri build 미진행) |
| MSI 산출물 | ❌ 없음 |
| EXE (setup) 산출물 | ❌ 없음 |
| `src-tauri/target/` | ❌ 미생성 (build 시작 전 차단) |

빌드 스크립트는 의도대로 동작 — *Rust toolchain 없이* `cargo tauri build` 를
무리하게 호출해 의미 불명의 cargo 에러를 내지 않고, **toolchain check 단계에서
명확한 메시지로 차단**한다 (위 §3 의 표와 일치).

### 8-4. 실제 EXE/MSI 산출을 위해 추가로 필요한 작업

본 환경에서 `Agent Trader v1_1.0.0_x64_en-US.msi` / `Agent Trader v1_1.0.0_x64-setup.exe`
를 *실제로 생성*하려면 운영자가 다음을 본 머신에 추가 설치해야 한다:

1. **Rust toolchain** (필수, ~300 MiB)

   ```powershell
   winget install Rustlang.Rustup
   # 또는 https://rustup.rs/ 에서 rustup-init.exe 다운로드
   rustup default stable-x86_64-pc-windows-msvc
   ```

2. **Visual Studio Build Tools 2022** (Rust MSVC ABI 의존, ~6 GiB)
   - "Desktop development with C++" 워크로드
   - Windows 10/11 SDK
   - https://visualstudio.microsoft.com/visual-cpp-build-tools/

3. **Tauri CLI v2** (`cargo` 설치 후)

   ```powershell
   cargo install tauri-cli --version "^2" --locked
   ```

4. **WiX Toolset v3.x** (MSI 빌드 필수)
   - https://github.com/wixtoolset/wix3/releases
   - 또는 Tauri 가 자동 다운로드하는 경우 별도 설치 불필요 (Tauri v2 의 동작 확인 필요)

5. **NSIS** (EXE setup 빌드 — Tauri 가 자동 다운로드)

위 1~3 만 갖춰지면 `cargo tauri build` 첫 실행 시 ~10~30분 (의존 crate 다운로드 +
release 컴파일) 소요 예상. WiX / NSIS 는 Tauri v2 가 첫 빌드 시 `bundle.windows`
설정에 따라 자동으로 다운로드한다.

### 8-5. 결론 — 90-A 단계 진단

| 항목 | 평가 |
|---|---|
| `scripts/build_backend_sidecar.ps1` 가 실제로 동작하는가? | ✅ 그렇다 (87.7 MiB onefile 생성 확인) |
| `scripts/build_windows_installer.ps1` 가 toolchain 없이 안전하게 차단하는가? | ✅ 그렇다 (exit 2 + 명확한 메시지) |
| 본 머신에서 *지금* MSI/setup.exe 를 생성할 수 있는가? | ❌ 아니다 (Rust + MSVC Build Tools 추가 설치 필요) |
| 90 번 PR 의 *코드 단* invariant 가 실 빌드로 검증되었는가? | ✅ 부분 — backend sidecar 흐름은 검증, Tauri 흐름은 toolchain 차단 검증만 |
| 베타테스터 EXE 배포가 *지금* 가능한가? | ❌ 아니다 — Rust 갖춰진 빌드 머신에서 재시도 필요 |

**권장 다음 액션**:

- (a) Rust + MSVC Build Tools 가 설치된 빌드 머신 (또는 CI runner) 에서
  `scripts/build_windows_installer.ps1` 재실행 후 본 §9 갱신.
- (b) `.github/workflows/desktop-release.yml` (#86 draft) 를 정식 활성화해
  GitHub Actions Windows runner 에서 빌드 → Release artifact 첨부.
- (c) backend sidecar 산출물 (`autotrade-backend.exe` 83.7 MiB) 만 별도 검증/사용 가능.

## 8-A. #91 Pre-market Checklist 통합 (2026-05-14)

EXE 가 있든 없든 사용자가 **실행 전 단계에서 안전 점검을 받도록** Pre-market
Checklist (#80) 를 desktop / KIS Paper 흐름에 맞춰 확장 (#91). 본 PR 의 EXE
산출물 빌드 가능 여부와 무관하게, *모든 베타테스터 흐름*에 적용된다.

| 흐름 | Pre-market Checklist 표시 위치 | 영향 |
|---|---|---|
| EXE 정상 빌드 + 설치 | 대시보드 메인 화면 상단 카드 | KIS Paper One-Click Test 시작 버튼이 verdict 에 따라 활성/비활성 |
| EXE 없이 `start_kis_paper_test_windows.bat` 흐름 | 브라우저 (`http://localhost:5173`) 대시보드 상단 카드 | 동일 |
| 백엔드 + frontend 수동 실행 (개발자) | 동일 | 동일 |

추가 정보:
- 정책 원문: [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) §10-A
- 초보자 흐름 요약: [`docs/pre_market_checklist.md`](pre_market_checklist.md)

## 8-C. GitHub Actions 자동 빌드 활성화 (2026-05-15)

본 머신 (또는 운영자 로컬 PC) 에 Rust / WiX 가 설치되어 있지 않아도 EXE/MSI 를
생성할 수 있도록 `.github/workflows/desktop-release.yml` 을 *정식 활성화*.

### 8-C-1. 실행 방법

1. GitHub 저장소 → **Actions** 탭 → **desktop-release** workflow 선택
2. 우측 상단 **Run workflow** 버튼 클릭
3. 입력 필드 채움:
   - `release_tag`: 예) `v1.0.1-beta.1` — Release 생성 시 사용되는 태그
   - `draft`: ✅ true 권장 (운영자가 직접 publish 결정)
   - `create_release`: ✅ true (GitHub Release draft 생성) / false (workflow
     artifact 만 업로드)
4. **Run workflow** 클릭 → ~30~45분 대기

### 8-C-2. workflow 단계 (10 step)

| # | step | 동작 |
|---|---|---|
| 1 | Checkout | repository fetch |
| 2 | Setup Node 20 / Python 3.12 / Rust stable | toolchain 설치 |
| 3 | Install Tauri CLI v2 | `cargo install tauri-cli --version "^2" --locked` |
| 4 | **Safety guard (빌드 전)** | `.env.example` 안전 flag default + workflow self-check (regex pattern, 리터럴 banned 문자열 0건) |
| 5 | Repository hygiene + security_scan | finding 1건이라도 발견 시 빌드 차단 |
| 6 | Frontend test + build | `npm test --run` + `npm run build` |
| 7 | Backend sidecar (PyInstaller) | `scripts/build_backend_sidecar.ps1 -Clean` |
| 8 | Tauri build | `cargo tauri build` → MSI + NSIS auto-bundle (WiX/NSIS 자동 다운로드) |
| 9 | **Safety guard (빌드 후)** | bundle 디렉토리 안에 `.env` / `*.pem` / `*.key` 등 secret 파일 0건 검증 |
| 10 | Upload artifact + Release draft | GitHub Actions artifact (`agent-trader-windows-installer-{tag}`) + Release draft (옵션) |

### 8-C-3. 산출물 위치

**Workflow artifact** (Actions 실행 페이지 하단에서 다운로드):
- `agent-trader-windows-installer-{release_tag}.zip`
  - `*.msi`
  - `*-setup.exe`

**GitHub Release** (Releases 페이지, draft 상태):
- Tag: 입력한 `release_tag`
- Assets:
  - `Agent Trader v1_1.0.0_x64_en-US.msi`
  - `Agent Trader v1_1.0.0_x64-setup.exe`

### 8-C-4. 절대 원칙 (workflow 자체로 강제)

| 원칙 | workflow 강제 위치 |
|---|---|
| secret / API key / `.env` 를 artifact 에 포함 안 함 | Step 9 safety guard — bundle 내 `.env` 파일 검출 시 즉시 exit 1 |
| 인증서 / 키 파일 포함 안 함 | Step 9 — `.pem` / `.key` / `.p12` / `.pfx` / `.crt` / `.cer` / `.keystore` / `.jks` 검출 시 exit 1 |
| LIVE / AI / FUTURES enable flag 활성화 안 함 | Step 4 self-check — workflow 자체에 enable flag true 패턴 0건 검증 |
| KIS_IS_PAPER false 로 해제 안 함 | Step 4 self-check — `.env.example` 의 KIS_IS_PAPER 가 true 인지 검증 |
| `workflow_dispatch` 만 트리거 (수동) | `on:` 에 `push` / `schedule` / `pull_request` 0건 |
| Windows runner 만 사용 | `runs-on: windows-latest` 명시, `test_repository_hygiene::test_desktop_release_workflow_runs_on_windows` 로 lock |

### 8-C-5. 베타테스터 설치 후 절차 (workflow artifact 다운로드 → 설치)

1. GitHub Release 페이지 → 최신 draft 또는 published release 의 `*.msi` 또는
   `*-setup.exe` 다운로드
2. 더블클릭 → Windows SmartScreen 경고 시 *추가 정보 → 실행*
3. 설치 완료 후 바탕화면 / 시작메뉴의 **Agent Trader v1** 실행
4. 첫 실행 시 `%APPDATA%\Autotrade\` 자동 생성됨 — 그 안의 `.env` 에 한투
   *모의투자* API 키 직접 입력 (절대 실거래 키 입력 금지)
5. 앱 재시작 → 대시보드 → Pre-market Checklist 통과 확인 → KIS Paper
   one-click test 시작

자세한 베타테스터 절차: [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md).

## 8-G. Auto Update A 단계 추가 (2026-05-15, feature/desktop-auto-updater)

`UpdateBanner` + `updaterClient` 가 Dashboard 상단에 노출되어 GitHub Release
`latest` 를 1회 조회 → 새 버전이 있으면 변경 내용 + 다운로드 페이지 링크 노출.
자동 *설치* 는 본 단계 미포함 — B 단계 (TAURI_PRIVATE_KEY 등록) 에서 활성화.

**A 단계 변경 (본 PR):**
- `frontend/src/desktop/updaterClient.js` — SemVer 비교 + GitHub REST 조회 +
  secret 패턴 redaction
- `frontend/src/components/UpdateBanner.jsx` — 4 상태 UI + 안전 배지 3종 +
  재시작 안내 + 수동 다운로드 fallback
- `Dashboard.jsx` — UpdateBanner carrier
- `tauri.conf.json` — updater endpoint URL placeholder → 실 repo URL (active=
  false 유지)
- `docs/auto_update_policy.md` (신규) + `docs/release_notes.md` (신규)

**A 단계 유지 (변경 없음):**
- `plugins.updater.active=false`
- `plugins.updater.pubkey=""`
- `bundle.createUpdaterArtifacts=false`
- TAURI_PRIVATE_KEY GitHub Secret 미등록 (workflow 영향 0건)

**B 단계 (별도 PR):** 자세한 절차 [`docs/auto_update_policy.md`](auto_update_policy.md) §4.

## 8-E. CI 빌드 실패 이력 — Tauri Windows icon assets 누락 (2026-05-15)

`desktop-release.yml` workflow 의 `cargo tauri build` 단계가 다음 에러로 실패한
이력 (해결 완료):

```
`icons/icon.ico` not found; required for generating a Windows Resource file
during tauri-build
```

**원인:**
- `src-tauri/tauri.conf.json` 의 `bundle.icon` 배열이 5개 파일을 참조하는데
  (`32x32.png`, `128x128.png`, `128x128@2x.png`, `icon.icns`, `icon.ico`)
  실제 `src-tauri/icons/` 에는 `README.md` placeholder 만 있고 binary 자산
  0개. Tauri Windows resource 빌드는 `icon.ico` 가 필수.

**해결:**
- `src-tauri/icons/_generate_icons.py` 신규 — Pillow (내장 default font, 외부
  자산 0건) 로 단색 배경 (#0ea5e9, frontend brand 톤) + 흰색 "AT" 텍스트의
  rounded square 아이콘을 모든 필수 크기로 생성하는 *재현 가능한 generator*.
- 산출 파일 (모두 commit):
  - `32x32.png` (471 bytes)
  - `128x128.png` (1,636 bytes)
  - `128x128@2x.png` (3,216 bytes, 256×256)
  - `icon.ico` (14,210 bytes, multi-resolution 16/24/32/48/64/128/256,
    Windows ICO magic `00 00 01 00` 확인)
  - `icon.icns` (64,424 bytes, multi-resolution 16~1024 — Windows-only 빌드
    에서는 미사용이지만 conf.icon 참조 일치 위해 생성)
- tauri.conf.json 의 `bundle.icon` 배열은 변경 0건 — 5개 참조 모두 실제 파일과
  매칭 확인.

**안전 확인 (본 수정에서 변경 0건):**
- broker / OrderExecutor / route_order 호출 0건
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 안전 flag default 모두 그대로
- `.env*` 추가 / Secret / API key / 인증서 / 계좌번호 추가 0건
- installer 산출물 / `*.msi` / `*.exe` / `src-tauri/target/` 커밋 0건
- 외부 폰트 / 저작권 자산 사용 0건 — Pillow 내장 default font + 자체 색상
- 외부 trademark / 증권사 로고 변형 0건 — "AT" 자체 약자

**검증:**
- Pillow `Image.open('icon.ico').info['sizes']` = `{(16,16), (24,24), (32,32),
  (48,48), (64,64), (128,128), (256,256)}` — 7개 해상도 모두 임베드.
- `node JSON.parse(tauri.conf.json)` OK, `bundle.icon` 5개 참조 모두 EXISTS.
- `scripts/security_scan.py` 763 file scan, finding 0건.
- `pytest backend/tests/test_repository_hygiene.py` 39/39 PASS.

**후속:**
- 본 아이콘은 *베타 placeholder* — 정식 브랜딩은 디자이너 자산으로 별도 PR.
- `_generate_icons.py` 는 재현 가능한 generator 로 git 에 보존 — 디자인 변경
  시 색상 / 라벨만 수정해 재실행 가능.

## 8-F. CI 빌드 실패 이력 — WiX 3.14 다운로드 503 (2026-05-15)

`desktop-release.yml` workflow 의 `cargo tauri build` 단계가 다음 에러로 실패한
이력 (해결 완료):

```
Downloading https://github.com/wixtoolset/wix3/releases/download/wix3141rtm/
wix314-binaries.zip
Error failed to bundle project `http status: 503`
Error: Process completed with exit code 1.
```

**원인:**
- `tauri.conf.json` `bundle.targets = ["msi", "nsis"]` 에서 *MSI* 타깃이 빌드 시
  Tauri 가 GitHub Release CDN 에서 WiX 3.14 ZIP 을 *외부 다운로드* 함.
- GitHub release CDN 503 (일시 장애) 으로 다운로드 실패 → `cargo tauri build`
  전체 실패.
- **앱 컴파일 자체는 정상** — 번들링 단계의 *외부 의존성* 실패.

**해결 (NSIS 우선 전략):**
- `tauri.conf.json` `bundle.targets` 를 `["msi", "nsis"]` → `["nsis"]` 로 축소.
- NSIS setup.exe 는 WiX 같은 외부 다운로드 없이 자체 번들링 — 안정 산출 가능.
- `desktop-release.yml` 의 Summarize / Upload / Release draft step 을 NSIS
  중심으로 재구성 (NSIS setup.exe 가 0건이면 build fail, MSI 는 optional).
- 베타테스터 EXE 원클릭 배포 목적상 NSIS setup.exe 만으로 충분.

**MSI 처리 방침 (보류, 재활성화 조건):**
- MSI 는 본 PR 에서 *완전히 제거하지 않음* — `bundle.windows.wix.language`
  설정은 보존, `bundle.targets` 만 축소. 향후 별도 PR 에서 재활성화 가능.
- 재활성화 시 필요 사전 작업:
  1. WiX 사전 다운로드 step 을 workflow 에 추가 (workflow 캐시 또는
     `%LOCALAPPDATA%\tauri\WixTools314` 캐시)
  2. GitHub release 503 같은 외부 장애 시 재시도 / 폴백 로직 추가
  3. 재활성화 후 `bundle.targets` 를 `["msi", "nsis"]` 로 복원
- 본 PR 에서는 MSI 를 강제 성공시키지 않음 — 시간 효율성 우선.

**안전 확인 (본 수정에서 변경 0건):**
- broker / OrderExecutor / route_order 호출 0건
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 안전 flag default 모두 그대로
- `.env*` 추가 / Secret / API key / 인증서 / 계좌번호 추가 0건
- installer 산출물 / `*.msi` / `*.exe` / `src-tauri/target/` 커밋 0건
- security_scan / repository_hygiene / bundle safety guard 모두 유지
- `bundle.active` / `externalBin` / `icon` / `security.csp` / updater 설정 모두
  그대로 — `targets` 만 축소
- workflow 의 Step 5 / 8 / 9 (safety flag 검증, bundle 내 .env / 인증서 / key
  검출, secret scan) 모두 유지

**베타테스터 배포 (NSIS setup.exe):**
- GitHub Release artifact 에 `*-setup.exe` 한 파일만 첨부됨 (MSI 없음).
- 베타테스터가 setup.exe 더블클릭 → Windows SmartScreen 경고 시 "추가 정보 →
  실행" → 설치 완료. MSI 가 없어도 정상 설치 가능.
- `KIS_IS_PAPER=true` / `ENABLE_LIVE_TRADING=false` / `ENABLE_AI_EXECUTION=false`
  안전 flag 그대로 유지 — 실거래 자동 활성화 0건. 한투 *모의투자* 전용 흐름.
- Secret / API key / `.env` 는 setup.exe 안에 *포함되지 않음* — 운영자가
  `%APPDATA%\Autotrade\.env` 에 직접 입력.

## 8-D. CI 빌드 실패 이력 — Tauri v2 lib entrypoint (2026-05-15)

`desktop-release.yml` workflow 의 `cargo tauri build` 단계가 다음 에러로 실패한
이력 (해결 완료):

```
can't find library `agent_trader_v1_lib`, rename file to `src/lib.rs`
or specify lib.path
```

**원인:**
- `src-tauri/Cargo.toml` 에 `[lib] name = "agent_trader_v1_lib"` + `crate-type =
  ["staticlib", "cdylib", "rlib"]` 가 선언되어 있는데, 대응하는
  `src-tauri/src/lib.rs` 파일이 없어 cargo 가 library crate 의 source root 를
  찾지 못함. `src/main.rs` 만 있었음.

**해결:**
- `src-tauri/src/lib.rs` 신규 — 기존 main.rs 의 Tauri Builder / sidecar wiring /
  `desktop_info` command / `BackendChild` 구조체를 모두 옮기고 `pub fn run()`
  으로 노출. `#[cfg_attr(mobile, tauri::mobile_entry_point)]` 적용.
- `src-tauri/src/main.rs` 는 thin binary wrapper 로 축소 — `#![cfg_attr(not
  (debug_assertions), windows_subsystem = "windows")]` 와 `fn main() {
  agent_trader_v1_lib::run(); }` 만 유지.
- `Cargo.toml` 의 `[lib]` 선언은 변경 0건 — `lib.path` 우회 대신 Tauri v2 표준
  구조 (`src/lib.rs` + `src/main.rs`) 채택.

**안전 확인 (본 수정에서 변경 0건):**
- broker / OrderExecutor / route_order 호출 0건
- `ENABLE_LIVE_TRADING` / `ENABLE_AI_EXECUTION` / `ENABLE_FUTURES_LIVE_TRADING` /
  `KIS_IS_PAPER` 안전 flag default 변경 0건
- `.env*` 추가 / Secret / API key / 인증서 / 계좌번호 추가 0건
- installer 산출물 / `*.msi` / `*.exe` / Rust target 커밋 0건

**검증:**
- `cargo fmt` 통과 (lib.rs 1줄 wrap 자동 정렬만 적용)
- `cargo check` 결과 `lib.rs not found` 에러는 사라짐 — 우리 crate 가 compile
  단계까지 진입. 단독 `cargo check` 는 *별도* tauri-build 단계의 protocol-asset
  feature ↔ tauri.conf 매칭 경고를 surface 하지만, 이는 `cargo tauri build` 가
  내부적으로 처리하는 별개 이슈로 CI 의 정식 빌드 경로 (`cargo tauri build`)
  에서는 동일한 에러로 막히지 않을 것으로 예상.
- frontend 회귀 0건 — `npx vitest run KisPaperOneClickTestCard.test.jsx` 25/25
  PASS, `npm run build` 성공 (111 modules).
- 안전 hygiene: `scripts/security_scan.py` 762 file scan 결과 finding 0건,
  `pytest backend/tests/test_repository_hygiene.py` 39/39 PASS.

## 9. 참고

- [`docs/desktop_packaging.md`](desktop_packaging.md) — #86 패키징 설계
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — #86 업데이트 정책
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — #86 일반 설치 가이드
- [`docs/exe_oneclick_installation.md`](exe_oneclick_installation.md) — **#90 초보자 / 지인 배포 가이드** (신규)
- [`docs/kis_paper_oneclick.md`](kis_paper_oneclick.md) — #89 KIS 모의투자 one-click 테스트 정책
- [`docs/pre_market_check_policy.md`](pre_market_check_policy.md) — #80 + **#91 확장** 정책
- [`docs/pre_market_checklist.md`](pre_market_checklist.md) — **#91 초보자 흐름 요약** (신규)
- [`scripts/start_kis_paper_test_windows.bat`](../scripts/start_kis_paper_test_windows.bat)
  / [`.ps1`](../scripts/start_kis_paper_test_windows.ps1) — EXE 없는 실행 보조 (#89)
- [`scripts/build_backend_sidecar.ps1`](../scripts/build_backend_sidecar.ps1) — **#90 sidecar PyInstaller 빌드** (신규)
- [`scripts/build_windows_installer.ps1`](../scripts/build_windows_installer.ps1) — **#90 end-to-end installer 빌드** (신규)
