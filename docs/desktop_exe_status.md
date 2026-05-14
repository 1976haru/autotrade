# Desktop EXE/MSI 빌드 상태 — 2026-05 (#89)

> 본 문서는 *현재 시점* 의 Windows installer 산출물 존재 여부 + 빌드 시도
> 결과를 기록한다. 사용자 (베타테스터) 가 "EXE 가 이미 있는가?" 라는 질문에
> 즉시 답할 수 있는 단일 진실.

## 1. 한 줄 결론

**현재 main 브랜치에는 빌드된 Windows installer (`.exe` / `.msi`) 가
존재하지 *않습니다*.** `src-tauri/` skeleton (#86) + #89 의 빌드 시도 결과
"빌드 불가 — Rust 툴체인 미설치" 가 확인됐습니다.

| 항목 | 값 |
|---|---|
| 기존 EXE/MSI 발견 여부 | ❌ 없음 |
| 빌드 시도 결과 | 🛑 환경 부족 — Rust / cargo / tauri-cli 미설치 |
| 최신 main 기준 산출물 | (생성된 적 없음) |
| 베타테스터 배포 가능 여부 | ❌ — 본 PR 시점 *재빌드 필요* |
| 대체 실행 방법 | `scripts/start_kis_paper_test_windows.bat` (Python + uvicorn 기반) |

## 2. 점검 한 위치

```
src-tauri/                                  ← skeleton 존재 (#86)
├─ tauri.conf.json                          ✅
├─ Cargo.toml                               ✅
├─ build.rs                                 ✅
├─ src/main.rs                              ✅
├─ capabilities/default.json                ✅
├─ icons/README.md                          (실 아이콘 binary 부재)
├─ target/                                  ❌ 존재 안 함 (빌드 시도 결과)
└─ target/release/bundle/                   ❌ 존재 안 함

frontend/src-tauri/                         ❌ 존재 안 함 (단일 src-tauri 만)
```

본 PR 시점 검색 결과:
```bash
$ find src-tauri -name "*.msi" -o -name "*.exe"
(빈 결과)
$ ls -la src-tauri/target
ls: cannot access 'src-tauri/target': No such file or directory
```

## 3. 빌드 시도 환경

본 PR 의 개발 환경에서 확인된 툴체인:

| 도구 | 필수 여부 | 본 머신 상태 |
|---|---|---|
| `cargo`     | 필수 (Rust) | ❌ 미설치 (`which cargo` 없음) |
| `rustc`     | 필수 (Rust) | ❌ 미설치 |
| `tauri-cli` | 필수 (`cargo install tauri-cli --version "^2"`) | ❌ 미설치 |
| Node 20+    | frontend build | ✅ 설치됨 (`/c/Program Files/nodejs`) |
| Python 3.12+| backend       | ✅ 설치됨 |
| Visual Studio Build Tools / Windows SDK | Tauri Windows installer 빌드 시 | ❓ 확인 안 됨 |

→ **`npm run tauri build` / `cargo tauri build` 실행 가능 0건** (현 환경).

## 4. 빌드를 시도하면 일어날 일 (예상)

`scripts/start_kis_paper_test_windows.bat` 이 *backend 실행만* 안내하는 이유:
- Rust 없이 `cargo tauri build` 호출 시 `cargo: command not found` 즉시 종료.
- 본 PR 은 *EXE 없이도* KIS 모의투자 테스트가 가능하도록 backend + 브라우저
  접속 방식을 제공.

## 5. EXE 가 *필요할 때* 의 빌드 절차

본 절차는 *후속 PR* (별도 옵트인) 에서 활성화. 현재 PR 은 *문서 + 스크립트
스켈레톤* 만 준비.

```powershell
# 1) Rust 툴체인 (1회만)
winget install Rustlang.Rustup

# 2) Tauri CLI
cargo install tauri-cli --version "^2" --locked

# 3) frontend 의존성
npm --prefix frontend install

# 4) icons 실 binary 채움 (src-tauri/icons/README.md 참고)
#    - 32x32.png / 128x128.png / 128x128@2x.png / icon.ico

# 5) backend sidecar 빌드 (후속 PR — PyInstaller)
# pyinstaller --onefile --name agent-trader-backend ...

# 6) 데스크톱 빌드
cd src-tauri
cargo tauri build
# 결과:
#   src-tauri/target/release/bundle/msi/Agent Trader v1_1.0.0_x64_en-US.msi
#   src-tauri/target/release/bundle/nsis/Agent Trader v1_1.0.0_x64-setup.exe
```

자세한 설계: [`docs/desktop_packaging.md`](desktop_packaging.md) (#86).

## 6. EXE 가 *없어도* 가능한 베타테스터 흐름 (현재 PR 권장)

```
1. 사용자가 GitHub Releases 또는 zip 으로 프로젝트 clone
2. scripts/start_kis_paper_test_windows.bat 더블클릭
3. Python 의존성 자동 설치 (idempotent)
4. backend 자동 실행 (http://127.0.0.1:8000)
5. 브라우저로 http://localhost:5173 접속 (또는 PC IP)
6. 대시보드 → "한투 모의투자 AI 자동매매 테스트" 카드 → 준비상태 확인 → 시작
```

본 흐름은 *Tauri / Rust 의존성 0건* 이고, 베타테스터에게 EXE 같은 *원클릭* UX
는 아니지만 *실제 테스트 가능* 한 가장 짧은 경로.

## 7. EXE 만들 때의 안전 invariant (재확인 — #86 정책)

EXE 안에 다음을 *절대 굽지 않습니다*:
- `.env` 파일 어떤 것도
- `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO`
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN`
- Tauri updater private key

Tauri bundle 의 `bundle.resources` 에 *어떤 secret 파일도 추가하지 않는다*.
`tauri.conf.json` 의 `bundle.resources` 가 비어 있는지 빌드 전 확인 필수.

자세한 정책: [`docs/desktop_packaging.md`](desktop_packaging.md) §3 + §7,
[`docs/desktop_update_policy.md`](desktop_update_policy.md) §3.

## 8. 후속 추적

- 본 시점부터 EXE 가 생성되면 `git status` / GitHub Releases 에 artifact 가
  나타날 것 — `.gitignore` 에 `*.msi` / `*.nsis` 등이 등록돼 있어 *git
  commit 0건*.
- EXE 산출 시 본 문서를 갱신 — "재빌드 필요" → "최신 X.Y.Z 빌드 가능"
- GitHub Release tag `v1.0.0` 이상에서 정식 installer 첨부 가능.

## 9. 참고

- [`docs/desktop_packaging.md`](desktop_packaging.md) — #86 패키징 설계
- [`docs/desktop_update_policy.md`](desktop_update_policy.md) — #86 업데이트 정책
- [`docs/beta_tester_install_guide.md`](beta_tester_install_guide.md) — #86 설치
  가이드 (EXE 가 준비된 시점의 절차)
- [`docs/kis_paper_oneclick.md`](kis_paper_oneclick.md) — #89 KIS 모의투자
  one-click 테스트 정책
- [`scripts/start_kis_paper_test_windows.bat`](../scripts/start_kis_paper_test_windows.bat)
  / [`.ps1`](../scripts/start_kis_paper_test_windows.ps1) — EXE 없는 실행 보조
