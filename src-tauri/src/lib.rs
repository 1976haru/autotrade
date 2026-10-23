// Agent Trader v1 — Tauri library entrypoint (#86 skeleton + #90 sidecar wiring
// + fix/desktop-sidecar-runtime-diagnostics: sidecar stdout/stderr/exit capture
// + WINDOWS-ONECLICK-EXE-V1: tray 상주 / 단일 실행 / 안전 종료 / port 신원 / 로그 마스킹).
//
// 절대 원칙 (CLAUDE.md):
//   - 본 lib 은 broker / OrderExecutor / route_order 어떤 backend 모듈도
//     직접 호출하지 않는다. backend 와의 상호작용은 모두 HTTP (`/api/*`).
//   - ENABLE_LIVE_TRADING / ENABLE_AI_EXECUTION / ENABLE_FUTURES_LIVE_TRADING
//     같은 안전 flag 는 본 lib 에서 *읽기만* 하며 *쓰기* 0건.
//   - tauri updater plugin 은 conf 의 `pubkey` 가 비어 있으면 자동 비활성.
//   - sidecar (autotrade-backend.exe) 는 `tauri-plugin-shell` 의 sidecar
//     spawning 으로 시작. *secret 인자 전달 없음* — .env 는 backend 가 직접 로드.
//   - 앱 실행 ≠ 매매 시작: 본 lib 은 자동매매 시작 API 를 호출하지 않는다. 신규 매수는
//     운영자가 화면에서 '자동매매 시작' 을 누른 뒤에만 (backend 루프 기본 PAUSED).
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
// - secret 마스킹: 기록 *전* Rust 단(mask_secrets) + frontend 표시 단 (defense in depth).
//
// 창/종료 정책 (WINDOWS-ONECLICK-EXE-V1):
// - X 버튼 = 트레이로 숨김. 백엔드와 장마감(15:00~15:30) 청산은 계속 동작.
// - 실제 종료 = 트레이 '안전 종료' → frontend 가 보유/미체결 경고 → 사용자가 선택 시
//   `confirm_quit` → sidecar 정리 후 종료. 어떤 경로로 끝나든 RunEvent::Exit 에서 sidecar 정리.
// - PC 전원 종료/강제 종료/절전 시에는 자동 청산을 보장할 수 없다 (frontend 에 상시 표시).

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};

use tauri::async_runtime;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// 백엔드 sidecar child handle — 앱 종료 시 부드러운 shutdown 위해 보관.
struct BackendChild(Mutex<Option<CommandChild>>);

// 로그 파일 위치 / 크기 정책.
const LOG_DIR_REL: &str = "Autotrade/logs";
const LOG_FILE_NAME: &str = "desktop-backend.log";
const LOG_MAX_BYTES: u64 = 64 * 1024; // 64KB — 너무 크면 frontend 가 무거워짐.
const PORT_FILE_REL: &str = "Autotrade/backend-port.json";

// 트레이 메뉴 → frontend 전역 훅 (정적 문자열만 — 외부 입력 0건).
const JS_SHOW_STATUS: &str =
    "window.__agentTraderDesktop && window.__agentTraderDesktop.showStatus()";
const JS_REQUEST_QUIT: &str =
    "window.__agentTraderDesktop && window.__agentTraderDesktop.requestQuit()";

fn appdata_base() -> Option<PathBuf> {
    // Windows: %APPDATA%  /  다른 OS: HOME/.config fallback.
    if let Some(appdata) = std::env::var_os("APPDATA") {
        Some(PathBuf::from(appdata))
    } else {
        std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config"))
    }
}

fn log_dir() -> Option<PathBuf> {
    let dir = appdata_base()?.join(LOG_DIR_REL);
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

/// 로그 기록 *전* secret 마스킹 (defense in depth — 예전엔 frontend 표시 단계에서만
/// 마스킹해 로그 파일 원문에는 남을 수 있었다).
/// - `Bearer <token>` → `Bearer ****`
/// - `KEY=value` 에서 KEY 가 secret/app_key/appkey/account/token/authorization/password 포함
///   → `KEY=****`
/// - `"appsecret": "x"` 류 (콜론으로 끝나는 키 토큰 다음 값 토큰) → `****`
fn mask_secrets(line: &str) -> String {
    const SENSITIVE: [&str; 7] = [
        "secret", "app_key", "appkey", "account", "token", "authorization", "password",
    ];
    fn is_sensitive(key: &str) -> bool {
        let k: String = key
            .chars()
            .filter(|c| *c != '"' && *c != ':' && *c != ',' && *c != '{')
            .collect::<String>()
            .to_ascii_lowercase();
        !k.is_empty() && SENSITIVE.iter().any(|s| k.contains(s))
    }
    let mut out: Vec<String> = Vec::new();
    let mut mask_next = false;
    for tok in line.split(' ') {
        // "Bearer" 는 값이 아니라 스킴 — 다음 토큰(실제 토큰)을 가린다 (먼저 검사해야
        // "Authorization: Bearer x" 에서 x 가 노출되지 않는다).
        if tok.eq_ignore_ascii_case("bearer") {
            out.push(tok.to_string());
            mask_next = true;
            continue;
        }
        if mask_next && !tok.is_empty() {
            out.push("****".to_string());
            mask_next = false;
            continue;
        }
        if let Some((k, v)) = tok.split_once('=') {
            if is_sensitive(k) && !v.is_empty() {
                out.push(format!("{}=****", k));
                continue;
            }
        }
        if tok.ends_with(':') && is_sensitive(tok) {
            out.push(tok.to_string());
            mask_next = true;
            continue;
        }
        out.push(tok.to_string());
    }
    out.join(" ")
}

fn append_log(line: &str) {
    let Some(path) = log_path() else { return; };
    let Ok(mut f) = OpenOptions::new().create(true).append(true).open(&path) else { return; };
    let ts = now_unix_secs();
    let _ = writeln!(f, "[{}] {}", ts, mask_secrets(line));
}

/// 메인 창을 보이고 앞으로 가져온다 (트레이 클릭 / 중복 실행 시).
fn show_main_window(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// 창을 띄운 뒤 frontend 가 등록한 전역 훅 호출.
fn show_main_and_call(app: &AppHandle, js_hook: &str) {
    show_main_window(app);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.eval(js_hook);
    }
}

/// sidecar(백엔드) 종료 — '안전 종료' 확정 / 프로세스 종료 시에만.
fn kill_sidecar(app: &AppHandle, reason: &str) {
    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                append_log(&format!("sidecar killed ({})", reason));
                let _ = child.kill();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let app = tauri::Builder::default()
        // 반드시 첫 plugin — 아이콘을 두 번 눌러도 새 창/새 백엔드를 만들지 않고
        // 기존 창을 앞으로 가져온다 (두 번째 프로세스는 즉시 종료).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            append_log("second launch detected - focusing existing window (no new sidecar)");
            show_main_window(app);
        }))
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
            // 등에서 직접 로드한다. (Windows 에서 shell plugin sidecar 는 콘솔 창 없이 실행)
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

            // 트레이 상주 — 창을 닫아도 백엔드·장마감 청산 유지.
            let open_i = MenuItem::with_id(app, "open", "대시보드 열기", true, None::<&str>)?;
            let status_i = MenuItem::with_id(app, "status", "상태 확인", true, None::<&str>)?;
            let quit_i = MenuItem::with_id(app, "quit", "안전 종료", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open_i, &status_i, &quit_i])?;
            let mut tray = TrayIconBuilder::with_id("agent-trader-tray")
                .tooltip("Agent Trader - 창을 닫아도 장마감 청산 관리 중 (종료는 트레이 메뉴 '안전 종료')")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main_window(app),
                    "status" => show_main_and_call(app, JS_SHOW_STATUS),
                    "quit" => show_main_and_call(app, JS_REQUEST_QUIT),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main_window(tray.app_handle());
                    }
                });
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            #[cfg(debug_assertions)]
            {
                if let Some(window) = app.get_webview_window("main") {
                    window.open_devtools();
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // X 버튼 = 트레이로 숨김. 예전엔 여기서 sidecar 를 kill 해, 보유/미체결이
            // 있어도 15:00~15:30 자동 청산이 멈췄다. 실제 종료는 '안전 종료' 확인 후에만.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
                append_log("window hidden to tray (backend/EOD keeps running)");
            }
        })
        .invoke_handler(tauri::generate_handler![
            desktop_info,
            read_backend_log,
            backend_port_info,
            confirm_quit,
            hide_to_tray
        ])
        .build(tauri::generate_context!())
        .expect("error while building Agent Trader v1");

    app.run(|app_handle, event| {
        // 어떤 경로로든 프로세스가 끝나면 sidecar 를 남기지 않는다 (고아 백엔드 방지).
        if let RunEvent::Exit = event {
            kill_sidecar(app_handle, "app exit");
        }
    });
}

/// Frontend 가 `invoke("desktop_info")` 로 호출해 native 측 메타데이터를 받는다.
/// 안전 flag 는 backend `/api/status` 에서 가져온다.
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
        "tray_mode":     true,
        "single_instance": true,
    })
}

/// Frontend "로그 보기" 가 `invoke("read_backend_log")` 로 호출.
/// %APPDATA%/Autotrade/logs/desktop-backend.log 의 전체 내용을 반환 (기록 시 마스킹됨).
/// 파일이 없거나 read 실패 시 진단 가능한 에러 문자열 반환 (예외 throw 0).
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

/// '안전 종료' 확정 — frontend 가 보유/미체결 경고를 보여 준 *뒤* 사용자가 선택했을 때만.
#[tauri::command]
fn confirm_quit(app: AppHandle) {
    append_log("safe quit confirmed by user");
    kill_sidecar(&app, "safe quit");
    app.exit(0);
}

/// 창만 트레이로 숨김 (frontend 의 '트레이로 계속 실행' 버튼).
#[tauri::command]
fn hide_to_tray(app: AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
}

/// %APPDATA%/Autotrade/backend-port.json — 이번 실행본 sidecar 의 port + instance_id.
/// frontend 가 /health.instance_id 와 대조해 *이번 실행본* 백엔드에만 연결한다.
/// secret 0건 (host/port/mode/uuid/commit/time).
#[tauri::command]
fn backend_port_info() -> serde_json::Value {
    let Some(path) = appdata_base().map(|b| b.join(PORT_FILE_REL)) else {
        return serde_json::json!({ "available": false, "reason": "APPDATA unavailable" });
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => match serde_json::from_str::<serde_json::Value>(&s) {
            Ok(mut v) => {
                if let Some(obj) = v.as_object_mut() {
                    obj.insert("available".into(), serde_json::Value::Bool(true));
                }
                v
            }
            Err(e) => serde_json::json!({ "available": false, "reason": format!("parse: {}", e) }),
        },
        Err(e) => serde_json::json!({ "available": false, "reason": format!("read: {}", e) }),
    }
}

#[cfg(test)]
mod tests {
    use super::mask_secrets;

    #[test]
    fn masks_bearer_and_key_values() {
        assert_eq!(mask_secrets("Authorization: Bearer abc.def"), "Authorization: Bearer ****");
        assert_eq!(mask_secrets("authorization Bearer tok123"), "authorization Bearer ****");
        assert_eq!(mask_secrets("KIS_APP_SECRET=xyz other=1"), "KIS_APP_SECRET=**** other=1");
        assert_eq!(mask_secrets("\"appkey\": \"PSabc\","), "\"appkey\": ****");
        assert_eq!(mask_secrets("uvicorn started on 127.0.0.1:8000"),
                   "uvicorn started on 127.0.0.1:8000");
    }
}
