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
//   `confirm_quit` → 백엔드 정상 종료 요청(POST /api/system/shutdown) → 종료 확인 →
//   시간 내 미종료 시에만 강제 종료(P0-SAFETY-FIX-V1). 어떤 경로로 끝나든 RunEvent::Exit 에서 sidecar 정리.
// - PC 전원 종료/강제 종료/절전 시에는 자동 청산을 보장할 수 없다 (frontend 에 상시 표시).

use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use tauri::async_runtime;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

// 백엔드 sidecar child handle — 앱 종료 시 부드러운 shutdown 위해 보관.
struct BackendChild(Mutex<Option<CommandChild>>);

// sidecar(부트로더) 종료 이벤트 수신 여부 — 부트로더는 python 자식이 끝나야 종료된다.
static SIDECAR_EXITED: AtomicBool = AtomicBool::new(false);
static DESKTOP_AUTH_TOKEN: OnceLock<String> = OnceLock::new();
// 정상 종료(lifespan 정리: 진행 중 EOD 단계 완료 대기·lease 해제) 최대 대기 — 넘으면 강제 종료.
const GRACEFUL_EXIT_TIMEOUT_SECS: u64 = 40;

// 로그 파일 위치 / 크기 정책.
const LOG_DIR_REL: &str = "Autotrade/logs";
const LOG_FILE_NAME: &str = "desktop-backend.log";
const LOG_MAX_BYTES: u64 = 64 * 1024; // 64KB — 너무 크면 frontend 가 무거워짐.
const PORT_FILE_REL: &str = "Autotrade/backend-port.json";

#[cfg(windows)]
fn secure_desktop_token() -> Result<String, String> {
    #[link(name = "bcrypt")]
    extern "system" {
        fn BCryptGenRandom(algorithm: *mut core::ffi::c_void, buffer: *mut u8,
                           length: u32, flags: u32) -> i32;
    }
    const BCRYPT_USE_SYSTEM_PREFERRED_RNG: u32 = 0x00000002;
    let mut bytes = [0u8; 32];
    let status = unsafe {
        BCryptGenRandom(core::ptr::null_mut(), bytes.as_mut_ptr(), bytes.len() as u32,
                        BCRYPT_USE_SYSTEM_PREFERRED_RNG)
    };
    if status < 0 { return Err(format!("BCryptGenRandom failed: {status}")); }
    Ok(bytes.iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(not(windows))]
fn secure_desktop_token() -> Result<String, String> {
    Err("one-click PAPER authentication is Windows-only".into())
}

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

/// `taskkill /PID <pid> /T /F` — pid 와 그 자식 프로세스 전부 종료 (콘솔 창 없이).
#[cfg(windows)]
fn kill_process_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let out = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .output();
    match out {
        Ok(o) => append_log(&format!("taskkill tree pid={} ok={}", pid, o.status.success())),
        Err(e) => append_log(&format!("taskkill tree pid={} failed: {}", pid, e)),
    }
}

/// sidecar(백엔드) 종료 — '안전 종료' 확정 / 프로세스 종료 시에만.
fn kill_sidecar(app: &AppHandle, reason: &str) {
    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.take() {
                append_log(&format!("sidecar killed ({})", reason));
                // PyInstaller onefile sidecar = bootloader + 실제 python 자식. bootloader 만
                // kill 하면 자식(백엔드)이 고아로 남아 포트를 계속 점유했다 (EXE-FINAL
                // 검증에서 '안전 종료' 후 백엔드 생존 확인) → 프로세스 트리 단위로 종료.
                #[cfg(windows)]
                kill_process_tree(child.pid());
                let _ = child.kill();
            }
        }
    }
}

/// WebView2 프로필의 stale PWA Service Worker 제거 (EXE-FINAL-BUILD-V2 설치 검증에서 발견).
/// Windows Tauri 는 frontend 를 http://tauri.localhost 로 서빙 → 구버전 frontend 가 SW 를
/// 등록했고, 다음 실행부터 SW 의 navigation fetch 가 Tauri 커스텀 프로토콜을 못 거쳐
/// 실패 → offline.html("오프라인 상태")만 표시됐다. frontend 는 이제 Tauri 에서 SW 를
/// 등록하지 않지만, 이미 설치된 PC 는 offline.html 이 떠서 그 JS 가 실행될 기회가 없다 →
/// webview 생성 *전* 여기서 정리. 다른 인스턴스가 사용 중이면 rename 이 실패하므로
/// 건너뛴다(삭제는 rename 성공 시에만). localStorage 등 다른 프로필 데이터는 미접촉.
fn purge_stale_service_worker(profile_default_dir: &std::path::Path) -> bool {
    let sw = profile_default_dir.join("Service Worker");
    if !sw.exists() {
        return false;
    }
    let stale = profile_default_dir.join(format!("Service Worker.stale-{}", now_unix_secs()));
    if std::fs::rename(&sw, &stale).is_err() {
        return false;
    }
    let _ = std::fs::remove_dir_all(&stale);
    true
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    let context = tauri::generate_context!();
    #[cfg(windows)]
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let profile = PathBuf::from(local)
            .join(&context.config().identifier)
            .join("EBWebView")
            .join("Default");
        if purge_stale_service_worker(&profile) {
            append_log("stale webview service worker removed (offline.html fallback fix)");
        }
    }

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
            let desktop_token = secure_desktop_token()
                .expect("secure Windows desktop authentication token unavailable");
            let _ = DESKTOP_AUTH_TOKEN.set(desktop_token.clone());
            match app.shell().sidecar("autotrade-backend") {
                Ok(cmd) => match cmd.env("AUTOTRADE_DESKTOP_AUTH_TOKEN", desktop_token).spawn() {
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
                                        SIDECAR_EXITED.store(true, Ordering::SeqCst);
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
            approve_paper_session,
            confirm_quit,
            hide_to_tray
        ])
        .build(context)
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

/// 포트 파일의 instance_id — 헤더 주입 방지를 위해 영숫자/하이픈만 허용.
fn sanitize_instance_id(raw: &str) -> Option<String> {
    let id = raw.trim();
    if id.is_empty() || id.len() > 64 || !id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-') {
        return None;
    }
    Some(id.to_string())
}

/// 이번 실행본 백엔드에 정상 종료 요청 (loopback, instance_id 헤더). 반환 = HTTP 상태코드.
fn post_graceful_shutdown(port: u16, instance_id: &str) -> Result<u16, String> {
    use std::net::{SocketAddr, TcpStream};
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|e| e.to_string())?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(10)));
    let body = r#"{"confirm":true}"#;
    let req = format!(
        "POST /api/system/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{}\r\n\
         Content-Type: application/json\r\nX-Autotrade-Instance: {}\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{}",
        port, instance_id, body.len(), body
    );
    s.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    let mut resp = String::new();
    let _ = s.read_to_string(&mut resp);
    resp.split_whitespace().nth(1).and_then(|c| c.parse::<u16>().ok())
        .ok_or_else(|| "no HTTP status".to_string())
}

fn post_desktop_json(port: u16, path: &str, token: &str, body: &str) -> Result<String, String> {
    use std::net::{SocketAddr, TcpStream};
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_secs(3)).map_err(|e| e.to_string())?;
    let _ = s.set_read_timeout(Some(Duration::from_secs(15)));
    let req = format!(
        "POST {} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nContent-Type: application/json\r\n\
         X-Autotrade-Desktop-Auth: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        path, port, token, body.as_bytes().len(), body,
    );
    s.write_all(req.as_bytes()).map_err(|e| e.to_string())?;
    let mut resp = String::new();
    s.read_to_string(&mut resp).map_err(|e| e.to_string())?;
    let code = resp.split_whitespace().nth(1).and_then(|v| v.parse::<u16>().ok())
        .ok_or_else(|| "invalid backend HTTP response".to_string())?;
    let payload = resp.split_once("\r\n\r\n").map(|(_, b)| b).unwrap_or("");
    if !(200..300).contains(&code) { return Err(format!("HTTP {code}: {payload}")); }
    Ok(payload.to_string())
}

/// One explicit click in the authenticated Windows desktop session.  The
/// secret is never returned to JavaScript/localStorage.
#[tauri::command]
fn approve_paper_session(target_date: String, explicit_ack: bool) -> Result<serde_json::Value, String> {
    if !explicit_ack { return Err("explicit PAPER BUY/SELL approval required".into()); }
    let info = backend_port_info();
    let port = info.get("port").and_then(|v| v.as_u64()).and_then(|v| u16::try_from(v).ok())
        .ok_or_else(|| "backend port unavailable".to_string())?;
    let token = DESKTOP_AUTH_TOKEN.get().ok_or_else(|| "desktop auth unavailable".to_string())?;
    let user = std::env::var("USERNAME").unwrap_or_else(|_| "unknown-windows-user".into());
    let domain = std::env::var("USERDOMAIN").unwrap_or_default();
    let actor = if domain.is_empty() { user } else { format!("{}\\{}", domain, user) };
    let body = serde_json::json!({
        "target_date": target_date,
        "explicit_ack": true,
        "actor": actor,
    }).to_string();
    let payload = post_desktop_json(
        port, "/api/auto-paper/paper-session-approval", token, &body,
    )?;
    serde_json::from_str(&payload).map_err(|e| e.to_string())
}

/// 정상 종료 → sidecar 종료 확인. true = 시간 내 스스로 종료(lifespan 정리 완료).
fn graceful_stop_backend(timeout: Duration) -> bool {
    if SIDECAR_EXITED.load(Ordering::SeqCst) {
        return true;
    }
    let info = backend_port_info();
    let port = info.get("port").and_then(|p| p.as_u64()).and_then(|p| u16::try_from(p).ok());
    let inst = info.get("instance_id").and_then(|v| v.as_str()).and_then(sanitize_instance_id);
    let (Some(port), Some(inst)) = (port, inst) else {
        append_log("graceful shutdown skipped: port file unavailable");
        return false;
    };
    match post_graceful_shutdown(port, &inst) {
        Ok(200) => append_log("graceful shutdown accepted by backend"),
        Ok(code) => {
            append_log(&format!("graceful shutdown refused (HTTP {})", code));
            return false;
        }
        Err(e) => {
            append_log(&format!("graceful shutdown request failed: {}", e));
            return false;
        }
    }
    let deadline = std::time::Instant::now() + timeout;
    while std::time::Instant::now() < deadline {
        if SIDECAR_EXITED.load(Ordering::SeqCst) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

/// '안전 종료' 확정 — frontend 가 보유/미체결 경고를 보여 준 *뒤* 사용자가 선택했을 때만.
/// P0-SAFETY-FIX-V1: 정상 종료 요청 → 종료 확인 → 시간 내 미종료 시에만 프로세스 트리 강제 종료.
#[tauri::command]
fn confirm_quit(app: AppHandle) {
    append_log("safe quit confirmed by user");
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
    std::thread::spawn(move || {
        let graceful = graceful_stop_backend(Duration::from_secs(GRACEFUL_EXIT_TIMEOUT_SECS));
        append_log(if graceful {
            "backend exited gracefully"
        } else {
            "backend did not exit gracefully - forcing process tree kill"
        });
        kill_sidecar(&app, if graceful { "safe quit (graceful)" } else { "safe quit (forced fallback)" });
        app.exit(0);
    });
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
    use super::{mask_secrets, purge_stale_service_worker, sanitize_instance_id};

    #[test]
    fn instance_id_header_injection_refused() {
        assert_eq!(sanitize_instance_id("2f9f0efe6c0842a8").as_deref(), Some("2f9f0efe6c0842a8"));
        assert_eq!(sanitize_instance_id(" abc-123 ").as_deref(), Some("abc-123"));
        assert!(sanitize_instance_id("abc\r\nX-Evil: 1").is_none());
        assert!(sanitize_instance_id("").is_none());
        assert!(sanitize_instance_id(&"a".repeat(65)).is_none());
    }

    #[test]
    fn purges_only_service_worker_dir() {
        let base = std::env::temp_dir().join(format!("at-sw-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(base.join("Service Worker").join("CacheStorage")).unwrap();
        std::fs::create_dir_all(base.join("Local Storage")).unwrap();
        std::fs::write(base.join("Local Storage").join("keep.txt"), "x").unwrap();
        assert!(purge_stale_service_worker(&base));
        assert!(!base.join("Service Worker").exists());
        assert!(base.join("Local Storage").join("keep.txt").exists());
        let leftovers = std::fs::read_dir(&base).unwrap()
            .filter(|e| e.as_ref().unwrap().file_name().to_string_lossy().starts_with("Service Worker"))
            .count();
        assert_eq!(leftovers, 0);
        assert!(!purge_stale_service_worker(&base)); // 없으면 no-op
        let _ = std::fs::remove_dir_all(&base);
    }

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
