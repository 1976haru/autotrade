# `src-tauri/binaries/` — backend sidecar 산출물 (#90)

이 디렉토리에는 **Tauri externalBin** 으로 등록된 backend sidecar 실행파일이
빌드 시 자동으로 생성된다. **git 에는 절대 commit 되지 않는다** (`.gitignore`).

## 어떤 파일이 들어가는가?

```
autotrade-backend-x86_64-pc-windows-msvc.exe     # Windows x64 PyInstaller 산출물
```

Tauri 의 sidecar 규칙: `<name>-<target-triple>.<ext>`. 본 프로젝트는 Windows x64
만 1차 타겟. macOS / Linux 빌드는 후속 PR.

## 어떻게 생성하나?

```powershell
pwsh scripts/build_backend_sidecar.ps1
# 또는 더블클릭:
scripts\build_backend_sidecar.bat
```

스크립트가 하는 일:
1. `backend/requirements.txt` 설치
2. `pyinstaller>=6.0` 설치
3. `backend/app_desktop_launcher.py` → `autotrade-backend.exe`
4. 산출물을 `src-tauri/binaries/autotrade-backend-x86_64-pc-windows-msvc.exe`
   로 복사

## 무엇이 들어가지 *않는가*?

- `backend/.env` — Tauri bundle 에 포함되지 않는다 (`bundle.resources` 비어
  있음). 운영자가 `%APPDATA%\Autotrade\.env` 또는 `backend/.env` 에 직접 채움.
- `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` — *어떤 형태로도*
  bundle 에 포함되지 않는다. PyInstaller 빌드 인자에 secret 0건.
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `TELEGRAM_BOT_TOKEN` — 동일.
- Tauri updater private key — `GitHub Secrets::TAURI_PRIVATE_KEY` 에만.

## 안전 invariant

- `ENABLE_LIVE_TRADING` 등 안전 flag 는 본 sidecar 가 *변경하지 않는다*.
  `app_desktop_launcher.py` 는 `os.environ` 의 키를 *읽기만* 한다.
- backend 가 실행되면 `RiskManager` + `KisPaperReadiness` 가 `KIS_IS_PAPER=
  false` / `ENABLE_LIVE_TRADING=true` 를 차단.
- sidecar 의 stdout / log 에 Secret 원문 출력 0건 — 존재 여부만 표시.
