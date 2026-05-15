// Agent Trader v1 — Tauri library entrypoint (#86 skeleton + #90 sidecar wiring
// + fix/desktop-sidecar-runtime-diagnostics: sidecar stdout/stderr/exit capture).
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
//
// sidecar 런타임 진단:
// - %APPDATA%/Autotrade/logs/desktop-backend.log 파일에 sidecar stdout / stderr /
//   exit code / spawn 실패 사유를 timestamp 포함 기록.
// - 매 startup 마다 신규 세션 마커 + 이전 로그 (마지막 64KB 만) 보존 — 무한 증가 방지.
// - `read_backend_log` Tauri command 로 frontend "로그 보기" UI 에 노출.
// - secret 마스킹은 frontend 단에서 한 번 더 적용 (defense in depth).

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::async_runtime;
use tauri::Manager;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// 백엔드 sidecar child handle — 앱 종료 시 부드러운 shutdown 위해 보관.
struct BackendChild(Mutex<Option<CommandChild>>);

// 로그 파일 위치 / 크기 정책.
const LOG_DIR_REL: &str = "Autotrade/logs";
const LOG_FILE_NAME: &str = "desktop-backend.log";
const LOG_MAX_BYTES: u64 = 64 * 1024; // 64KB — 너무 크면 frontend 가 무거워짐.

fn log_dir() -> Option<PathBuf> {
    // Windows: %APPDATA%\Autotrade\logs
    // 다른 OS 에서도 작동하도록 fallback (HOME/.config/...).
    let base = if let Some(appdata) = std::env::var_os("APPDATA") {
        PathBuf::from(appdata)
    } else if let Some(home) = std::env::var_os("HOME") {
        PathBuf::from(home).join(".config")
    } else {
        return None;
    };
    let dir = base.join(LOG_DIR_REL);
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

fn log_path() -> Option<PathBuf> {
    log_dir().map(|d| d.join(LOG_FILE_NAME))
}

fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn truncate_log_if_huge() {
    let Some(path) = log_path() else { return; };
    let Ok(meta) = std::fs::metadata(&path) else { return; };
    if meta.len() <= LOG_MAX_BYTES { return; }
    // 끝의 LOG_MAX_BYTES 만 유지.
    let Ok(mut f) = File::open(&path) else { return; };
    let _ = f.seek(SeekFrom::End(-(LOG_MAX_BYTES as i64)));
    let mut buf = Vec::with_capacity(LOG_MAX_BYTES as usize);
    if f.read_to_end(&mut buf).is_err() { return; }
    drop(f);
    if let Ok(mut out) = OpenOptions::new().write(true).truncate(true).create(true).open(&path) {
        let _ = out.write_all(b"--- log truncated (rotated to last 64KB) ---\n");
        let _ = out.write_all(&buf);
    }
}

fn append_log(line: &str) {
    let Some(path) = log_path() else { return; };
    let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) else { return; };
    let ts = now_unix_secs();
    let _ = writeln!(f, "[{}] {}", ts, line);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(BackendChild(Mutex::new(None)))
        .setup(|app| {
            // 진단 로그 — 매 startup 마다 세션 마커. 이전 로그가 64KB 초과면 rotate.
            truncate_log_if_huge();
            append_log("=== Agent Trader sidecar startup ===");
            append_log(&format!(
                "platform={} arch={}",
                std::env::consts::OS,
                std::env::consts::ARCH
            ));

            // #90: backend sidecar 자동 실행.
            // tauri.conf.json 의 externalBin 에 등록된 "binaries/autotrade-backend" 를 spawn.
            // .env / Secret / API Key 인자 전달 0건 — backend 가 %APPDATA%/Autotrade/.env
            // 등에서 직접 로드한다.
            match app.shell().sidecar("autotrade-backend") {
                Ok(cmd) => match cmd.spawn() {
                    Ok((mut rx, child)) => {
                        let pid = child.pid();
                        append_log(&format!("sidecar spawned (pid={})", pid));
                        log::info!("backend sidecar spawned (pid={})", pid);

                        if let Some(state) = app.try_state::<BackendChild>() {
                            if let Ok(mut guard) = state.0.lock() {
                                *guard = Some(child);
                            }
                        }

                        // sidecar stdout/stderr/exit 이벤트를 캡처해 로그 파일에 기록.
                        // 본 task 는 sidecar 가 살아있는 동안 background 에서 동작.
                        // *secret 필터링은 frontend 단에서* — Rust 단은 정직하게 기록만.
                        async_runtime::spawn(async move {
                            while let Some(event) = rx.recv().await {
                                match event {
                                    CommandEvent::Stdout(bytes) => {
                                        let s = String::from_utf8_lossy(&bytes);
                                        append_log(&format!(
                                            "STDOUT: {}",
                                            s.trim_end_matches(['\r', '\n'])
                                        ));
                                    }
                                    CommandEvent::Stderr(bytes) => {
                                        let s = String::from_utf8_lossy(&bytes);
                                        append_log(&format!(
                                            "STDERR: {}",
                                            s.trim_end_matches(['\r', '\n'])
                                        ));
                                    }
                                    CommandEvent::Terminated(payload) => {
                                        append_log(&format!(
                                            "TERMINATED: code={:?} signal={:?}",
                                            payload.code, payload.signal
                                        ));
                                    }
                                    CommandEvent::Error(err) => {
                                        append_log(&format!("EVENT_ERROR: {}", err));
                                    }
                                    _ => {}
                                }
                            }
                            append_log("sidecar event stream closed");
                        });
                    }
                    Err(err) => {
                        let msg = format!("SPAWN_FAILED: {}", err);
                        append_log(&msg);
                        log::warn!(
                            "backend sidecar spawn failed: {} \
                             — frontend will surface 'backend offline' banner.",
                            err
                        );
                    }
                },
                Ok(_) => {}
                Err(err) => {
                    let msg = format!("SIDECAR_NOT_AVAILABLE: {}", err);
                    append_log(&msg);
                    log::warn!(
                        "backend sidecar not available: {} \
                         — build via scripts/build_backend_sidecar.ps1 first.",
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
                            append_log("sidecar killed (window close)");
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![desktop_info, read_backend_log])
        .run(tauri::generate_context!())
        .expect("error while running Agent Trader v1");
}

/// Frontend 가 `invoke("desktop_info")` 로 호출해 native 측 메타데이터를 받는다.
/// 본 PR 시점에는 정적 값 — 안전 flag 는 backend `/api/status` 에서 가져온다.
#[tauri::command]
fn desktop_info() -> serde_json::Value {
    let log_p = log_path()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| "(unavailable)".into());
    serde_json::json!({
        "platform":      std::env::consts::OS,
        "arch":          std::env::consts::ARCH,
        "app_name":      "Agent Trader v1",
        "tauri_version": env!("CARGO_PKG_VERSION"),
        "backend_url":   "http://127.0.0.1:8000",
        "sidecar_name":  "autotrade-backend",
        "log_path":      log_p,
    })
}

/// Frontend "로그 보기" 가 `invoke("read_backend_log")` 로 호출.
/// %APPDATA%/Autotrade/logs/desktop-backend.log 의 전체 내용을 반환.
/// 파일이 없거나 read 실패 시 진단 가능한 에러 문자열 반환 (예외 throw 0).
/// Secret 마스킹은 frontend 단 sanitize 가 담당 — 본 함수는 *그대로* 반환.
#[tauri::command]
fn read_backend_log() -> String {
    let Some(path) = log_path() else {
        return String::from("(log path unavailable: APPDATA/HOME 환경변수 없음)");
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => s,
        Err(e) => format!(
            "(log read error: {} — path={})",
            e,
            path.display()
        ),
    }
}
