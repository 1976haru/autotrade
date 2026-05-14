// Agent Trader v1 — Tauri entry (베타 desktop installer skeleton).
//
// 본 PR 시점: skeleton only. 실제 Rust 빌드는 후속 PR(릴리스 자동화)에서 활성화.
// 본 파일이 들어가야 `cargo tauri build` 가 의미를 가진다.
//
// 절대 원칙 (CLAUDE.md):
//   - 본 main 은 broker / OrderExecutor / route_order 어떤 backend 모듈도
//     직접 호출하지 않는다. backend 와의 상호작용은 모두 HTTP (`/api/*`).
//   - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING
//     같은 안전 flag 는 본 main 에서 *읽기만* 하며 *쓰기* 0건.
//   - tauri updater plugin 은 conf 의 `pubkey` 가 비어 있으면 자동 비활성.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // 백엔드 자동 실행 hook 위치 — docs/desktop_packaging.md §3.
            // 실 spawn 코드는 후속 PR (PyInstaller 빌드 + sidecar 등록 후).
            log::info!("Agent Trader v1 starting; backend sidecar wiring deferred to follow-up PR.");
            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![desktop_info])
        .run(tauri::generate_context!())
        .expect("error while running Agent Trader v1");
}

/// Frontend 가 `invoke("desktop_info")` 로 호출해 native 측 메타데이터를 받는다.
/// 본 PR 시점에는 정적 값 — backend 자동 실행 / updater 연결 후 동적 carry.
#[tauri::command]
fn desktop_info() -> serde_json::Value {
    serde_json::json!({
        "platform":      std::env::consts::OS,
        "arch":          std::env::consts::ARCH,
        "app_name":      "Agent Trader v1",
        "tauri_version": env!("CARGO_PKG_VERSION"),
        "backend_url":   "http://127.0.0.1:8000",
        // 안전 flag 는 backend `/api/status` 에서 가져옴 — 본 native command 에는 표시 X.
    })
}
