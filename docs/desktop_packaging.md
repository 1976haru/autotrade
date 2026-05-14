# Agent Trader v1 — Windows 설치형 앱 패키징 설계

> 베타테스터가 *PowerShell / uvicorn / npm 명령어를 직접 입력하지 않고*
> `AgentTrader-v1-Setup.exe` 를 더블클릭해 설치하고, 바탕화면 / 시작 메뉴
> 아이콘으로 실행 가능하게 만드는 것이 본 문서의 목표다.
>
> **본 PR 시점 범위**: skeleton(`src-tauri/`) + 문서 + 프런트엔드 update UI.
> 실제 Rust 툴체인 빌드 / 서명 키 / GitHub Actions 자동 릴리스는 후속 PR.

## 1. 결정 — Tauri v2 채택

| 후보 | 채택 여부 | 이유 |
|---|---|---|
| **Tauri v2** | ✅ | 작은 번들 / Rust 안전성 / Windows installer 1급 지원 / 자체 updater(서명 검증) 내장 |
| Electron | ❌ | 번들 크기 100MB+ / 보안 표면 큼 / native 권한 모델이 broker 호출 차단을 보장하기 어려움 |
| 단일 PyInstaller 풀어진 batch + 브라우저 | ❌ | 베타테스터가 여전히 *terminal / browser 띄우기* 를 수동으로 해야 함 |
| Microsoft Edge WebView2 단독 | ❌ | 자체 updater / installer 표준 없음 |

Tauri v2 의 결정적 장점:
- **Windows installer** (msi/nsis) 가 1급 — 시작 메뉴 / 바탕화면 아이콘 자동 생성
- **자동 업데이트** 가 코드 서명 키 기반 — 서명 없는 update artifact 자동 거부
- **CSP / capabilities 시스템** 으로 frontend 가 임의 외부 URL fetch / 임의 셸
  명령 실행을 *기본 차단* — CLAUDE.md 의 "AI 가 broker 직접 호출 금지" 와
  자연스럽게 정렬

## 2. 디렉터리 구조 (본 PR 시점)

```
autotrade/
├─ src-tauri/                  ← 신규 (본 PR)
│  ├─ tauri.conf.json          # app 메타 + bundle target + updater placeholder
│  ├─ Cargo.toml               # Tauri v2 + plugin-updater / plugin-shell / plugin-process
│  ├─ build.rs                 # tauri-build invoker
│  ├─ src/main.rs              # tauri::Builder::default() + invoke_handler stub
│  ├─ capabilities/default.json# permission set (broker 직접 호출 차단)
│  ├─ icons/README.md          # 아이콘 파일 placeholder (실 binary 후속 PR)
│  └─ .gitignore               # target/, gen/, *.exe, *.key
├─ frontend/                   ← 기존 (수정 없음 — vite build 결과를 Tauri 가 번들)
└─ backend/                    ← 기존 (별도 프로세스로 실행)
```

## 3. Backend 자동 실행 — MVP A 안 채택 (Tauri sidecar)

| 후보 | 채택 | 메모 |
|---|---|---|
| **A. Tauri 가 backend 프로세스를 spawn / kill** | ✅ MVP | 베타테스터 추가 설정 0건 |
| B. backend 를 PyInstaller exe 로 묶어 sidecar 등록 | 후속 PR | Windows-only 배포 / 코드 서명 / virus scanner 우회 별도 필요 |
| C. backend Windows 서비스 / Docker | ❌ | 베타 단계엔 과한 복잡도 |

### 3.1 동작 시나리오 (목표)

1. 사용자가 바탕화면의 *Agent Trader v1* 아이콘 더블클릭
2. Tauri main 이 `127.0.0.1:8000/api/status` 를 health-check (2초 timeout, 5회)
3. backend 가 떠 있지 않으면 sidecar 로 `uvicorn app.main:app --host 127.0.0.1 --port 8000` 실행
   - **uvicorn / Python 이 PATH 에 있어야 한다 — 본 PR 단계의 한계**
   - 후속 PR(PyInstaller exe sidecar)로 의존성 제거 예정
4. backend `/api/status` 정상 응답 시 frontend UI 마운트
5. Tauri 종료 시 spawn 한 backend 프로세스도 함께 종료 (`on_window_event`
   → 자식 프로세스에 SIGTERM)

### 3.2 본 PR 의 한계

- `src-tauri/src/main.rs` 의 `setup` hook 은 sidecar spawn 코드를 *주석으로
  표시*만 함 — 실제 spawn 은 후속 PR (PyInstaller sidecar 도입 시 동시에).
- 베타테스터가 본 PR 의 결과물만으로 desktop installer 를 빌드하려면, 우선
  로컬에 Python + uvicorn + 종속 패키지가 있는 상태여야 한다. *진짜 zero-install*
  베타는 PyInstaller sidecar 가 들어간 후속 PR 이 합쳐진 시점부터.

### 3.3 Port 충돌 / 친절한 오류

- 8000 포트가 이미 사용 중이면 `EADDRINUSE` 가 backend 에서 발생.
- Tauri main 은 health-check 5회 실패 시 frontend 에 `desktop_info`
  invoke 응답으로 `backend_error: "port_busy"` carry → frontend `Settings`
  탭이 "8000 포트가 다른 프로그램에서 사용 중입니다 …" 라는 user-friendly
  banner 노출 (해당 UI 는 본 PR 의 `UpdateCheckerCard` 이후 별도 PR 에서
  구현).

### 3.4 backend 로그 위치

- Windows 표준: `%LOCALAPPDATA%\AgentTrader\logs\backend.log`
- Tauri main 이 spawn 시 `--log-file` 인자로 위 경로 지정 (후속 PR).
- `Settings` 탭의 "진단 정보 복사" 버튼이 본 경로의 *마지막 200줄* 만 클립
  보드로 복사 (Secret 마스킹 통과 후) — 본 PR 시점에는 docs only.

### 3.5 종료 시 동작 — 운영자 선택 (후속 PR UI)

- 기본: backend 도 함께 종료.
- `Settings → 데스크톱 옵션`: "창을 닫아도 tray 에 남기기" 토글 (후속 PR).

## 4. First-run Setup Wizard — skeleton + docs only (본 PR)

베타테스터에게 `.env` 직접 편집을 요구하지 않기 위한 wizard 가 필요. 하지만
*안전한 secret 저장소* (OS keychain / DPAPI / SQLCipher) 구현은 위험도가
크므로 본 PR 에서는 *.env 직접 입력 fallback* 을 유지하고, 설계만 정리한다.

자세한 설계: [`first_run_setup_wizard.md`](first_run_setup_wizard.md).

요점:
- ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING 는
  wizard 에서 *읽기 전용* — toggle 불가. 변경하려면 운영자가 `.env` 를
  직접 편집해야 한다 (CLAUDE.md 절대 원칙 1-2 + 안전 flag 표).
- KIS_IS_PAPER 는 기본 `true` 로 고정 노출, 운영자가 명시적으로 false 로
  변경하려면 별도 확인 모달 통과 필요.
- secret 입력 필드는 *frontend localStorage 에 절대 저장 금지* —
  `POST /api/desktop/config` (skeleton, 후속 PR) 가 OS secure store
  또는 backend `.env` 파일을 갱신.

## 5. Updater — placeholder only (본 PR)

- `tauri.conf.json` 의 `plugins.updater.active = false` (PR 시점).
- `pubkey` 빈 문자열 — `tauri signer generate` 로 생성한 키는 후속 PR 에서
  public 키만 채움. private 키는 `GitHub Secrets::TAURI_PRIVATE_KEY` 에만.
- endpoint 는 `https://github.com/__ORG__/__REPO__/releases/latest/download/latest.json`
  형식 placeholder.
- 자세한 정책: [`desktop_update_policy.md`](desktop_update_policy.md).

## 6. 빌드 가이드 (후속 PR 가 활성화될 때 사용)

```powershell
# 1) Rust 툴체인 (안 깔려 있을 때만)
winget install Rustlang.Rustup

# 2) Tauri CLI
cargo install tauri-cli@^2

# 3) 프런트엔드 의존성
npm --prefix frontend install

# 4) 데스크톱 빌드 (msi + nsis 동시)
cargo tauri build
#   결과:
#     src-tauri/target/release/bundle/msi/Agent Trader v1_1.0.0_x64_en-US.msi
#     src-tauri/target/release/bundle/nsis/Agent Trader v1_1.0.0_x64-setup.exe
```

**서명 / 자동 업데이트가 필요한 경우** GitHub Secrets 에 `TAURI_PRIVATE_KEY`
및 `TAURI_KEY_PASSWORD` 가 설정돼 있어야 GitHub Actions 가 서명된 update
artifact 를 생성한다.

## 7. 절대 원칙 매핑 (CLAUDE.md)

| 원칙 | 본 packaging 에서의 보장 |
|---|---|
| 1. AI 가 broker 주문 직접 호출 금지 | Tauri capability 가 외부 URL fetch / 임의 셸 명령 차단 — backend 만 broker 호출 |
| 2. 주문은 Risk → Permission → Executor 흐름 | desktop main 은 `/api/*` HTTP 만 — 직접 broker 호출 0건 |
| 3. 기본 SIMULATION / PAPER | installer 가 만든 기본 `.env` 는 `DEFAULT_MODE=SIMULATION`, `ENABLE_*=false` 그대로 |
| 4. API Key 등 frontend 저장 금지 | wizard 의 secret 필드는 frontend localStorage 0건 — backend `.env` 또는 OS keychain 만 |
| 5. broker / AI API 호출은 backend | desktop 은 *backend health-check* 만, 실 API 호출 0건 |
| 6. 선물은 별도 어댑터 | 본 packaging 은 주식 MVP 만 — 선물 UI 는 feature flag (#50) 그대로 false |

## 8. 다음 PR 의 작업 순서 (참고)

1. PyInstaller 로 backend exe 빌드 → `src-tauri/binaries/` 에 sidecar 등록
2. `setup` hook 에서 sidecar spawn + 종료 시 kill
3. `tauri signer generate` 로 update key 생성 → public key 만 `tauri.conf.json`
   에 commit, private key 는 GitHub Secrets
4. `.github/workflows/desktop-release.yml` (본 PR 의 *draft* skeleton) 을
   활성화 — tag push `v*` 시 Windows runner 에서 build → sign → release
5. First-run wizard 실 저장 흐름 (OS keychain via `tauri-plugin-stronghold` 또는
   backend `.env` patch endpoint)
6. UpdateCheckerCard 의 mock 상태를 Tauri updater 실 API 와 연결

## 9. 참고

- [`CLAUDE.md`](../CLAUDE.md) — 9개 절대 원칙
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — 업데이트 정책
- [`docs/first_run_setup_wizard.md`](first_run_setup_wizard.md) — 초기 설정 wizard
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — 초보자
  설치 가이드
- [`docs/tailscale_smartphone_access.md`](tailscale_smartphone_access.md) —
  스마트폰 원격 관제
- [`docs/beta_distribution_plan.md`](beta_distribution_plan.md) — 기존 베타
  배포 큰 그림 (본 문서가 구체화)
- [`docs/auto_update_plan.md`](auto_update_plan.md) — 기존 자동 업데이트 큰 그림
