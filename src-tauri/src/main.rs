// Agent Trader v1 — Tauri binary entry (thin wrapper).
//
// 모든 Tauri Builder / sidecar / invoke handler 로직은 `lib.rs` 의
// `agent_trader_v1_lib::run()` 으로 이동했다 — Tauri v2 표준 구조 (Cargo.toml
// `[lib] name = "agent_trader_v1_lib"` 선언과 매칭). 본 파일은 *binary entry*
// 전용으로 lib 의 run() 만 호출한다.
//
// 절대 원칙 (CLAUDE.md):
//   - broker / OrderExecutor / route_order 호출 0건 — 모든 실 로직은 lib 에.
//   - 안전 flag 변경 0건.

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    agent_trader_v1_lib::run();
}
