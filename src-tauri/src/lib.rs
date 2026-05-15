// Agent Trader v1 — Tauri library entrypoint (#86 skeleton + #90 sidecar wiring).
//
// 절대 원칙 (CLAUDE.md):
//   - 본 lib 은 broker / OrderExecutor / route_order 어떤 backend 모듈도
//     직접 호출하지 않는다. backend 와의 상호작용은 모두 HTTP (`/api/*`).
//   - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING
//     같은 안전 flag 는 본 lib 에서 *읽기만* 하며 *쓰기* 0건.
//   - tauri updater plugin 은 conf 의 `pubkey` 가 비어 있으면 자동 비활성.
//   - sidecar (autotrade-backend.exe) 는 `tauri-plugin-shell` 의 sidecar
//     spawning 으로 시작. *secret 인자 전달 없음* — .env 는 backend 가 직접 로드.
//
// Tauri v2 표준 구조: Cargo.toml `[lib] name = "agent_trader_v1_lib"` 가
// 선언되어 있어 cargo 가 `src/lib.rs` 를 요구. main.rs 는 본 lib 의 run() 만
// 호출하는 thin wrapper.

use std::sync::Mutex;

use tauri::Manager;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

// 백엔드 sidecar child handle — 앱 종료 시 부드러운 shutdown 위해 보관.
struct BackendChild(Mutex<Option<CommandChild>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(BackendChild(Mutex::new(None)))
        .setup(|app| {
            // #90: backend sidecar 자동 실행.
            // tauri.conf.json 의 externalBin 에 등록된 "binaries/autotrade-backend" 를 spawn.
            // .env / Secret / API Key 인자 전달 0건 — backend 가 %APPDATA%/Autotrade/.env
            // 등에서 직접 로드한다.
            match app.shell().sidecar("autotrade-backend") {
                Ok(cmd) => match cmd.spawn() {
                    Ok((_rx, child)) => {
                        log::info!("backend sidecar spawned (pid={})", child.pid());
                        if let Some(state) = app.try_state::<BackendChild>() {
                            if let Ok(mut guard) = state.0.lock() {
                                *guard = Some(child);
                            }
                        }
                    }
                    Err(err) => {
                        log::warn!(
                            "backend sidecar spawn failed: {} \
                             — frontend will display a friendly error and \
                             allow the user to start backend manually.",
                            err
                        );
                    }
                },
                Ok(_) => {} // unreachable in current API
                Err(err) => {
                    log::warn!(
                        "backend sidecar not available: {} \
                         — build it via scripts/build_backend_sidecar.ps1 first.",
                        err
                    );
                }
            }

            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                // 종료 시 sidecar 도 정리.
                if let Some(state) = window.app_handle().try_state::<BackendChild>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![desktop_info])
        .run(tauri::generate_context!())
        .expect("error while running Agent Trader v1");
}

/// Frontend 가 `invoke("desktop_info")` 로 호출해 native 측 메타데이터를 받는다.
/// 본 PR 시점에는 정적 값 — 안전 flag 는 backend `/api/status` 에서 가져온다.
#[tauri::command]
fn desktop_info() -> serde_json::Value {
    serde_json::json!({
        "platform":      std::env::consts::OS,
        "arch":          std::env::consts::ARCH,
        "app_name":      "Agent Trader v1",
        "tauri_version": env!("CARGO_PKG_VERSION"),
        "backend_url":   "http://127.0.0.1:8000",
        "sidecar_name":  "autotrade-backend",
    })
}
