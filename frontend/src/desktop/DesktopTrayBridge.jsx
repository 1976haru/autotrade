import { useCallback, useEffect, useState } from "react";

import { backendApi } from "../services/backend/client";
import { isTauriRuntime } from "./backendIdentity";

// WINDOWS-ONECLICK-EXE-V1 — 트레이 메뉴(Rust) ↔ 화면 다리.
//
// Rust 트레이 메뉴가 창을 띄운 뒤 `window.__agentTraderDesktop.showStatus()` /
// `.requestQuit()` 를 호출한다 (정적 JS — 외부 입력 0). 본 컴포넌트는:
//   - 상태 확인: 보유/미체결/EOD 상태 + "자동 청산 보장 아님" 안내.
//   - 안전 종료: 서버의 shutdown-check 로 보유/미체결을 확인해 경고하고,
//     [트레이로 계속 실행] (권장, 기본 포커스) / [그래도 종료] 중 사용자가 선택.
//     '그래도 종료' 만 Rust `confirm_quit` 을 호출 — X 버튼은 항상 트레이로 숨김.
// 주문 API 호출 0건 — read-only 확인 + Tauri 창/종료 명령만.

function invokeTauri(cmd) {
  if (isTauriRuntime() && typeof window.__TAURI_INTERNALS__.invoke === "function") {
    return window.__TAURI_INTERNALS__.invoke(cmd);
  }
  return Promise.resolve(null);
}

export function DesktopTrayBridge({ api = backendApi, invoke = invokeTauri, enabled = isTauriRuntime() }) {
  const [mode, setMode] = useState(null);       // null | "status" | "quit"
  const [check, setCheck] = useState(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setFailed(false);
    setCheck(null);
    try {
      setCheck(await api.kisPaperEodShutdownCheck());
    } catch {
      setFailed(true);
    }
  }, [api]);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return undefined;
    window.__agentTraderDesktop = {
      showStatus: () => { setMode("status"); load(); },
      requestQuit: () => { setMode("quit"); load(); },
    };
    return () => { delete window.__agentTraderDesktop; };
  }, [enabled, load]);

  if (!enabled || !mode) return null;

  const holdings = check?.program_holdings || [];
  const risky = failed || (check && check.needs_running_for_eod);
  const close = () => setMode(null);
  const toTray = async () => { close(); await invoke("hide_to_tray"); };
  const quit = async () => { await invoke("confirm_quit"); };

  return (
    <div role="dialog" aria-modal="true" data-testid="desktop-tray-dialog" style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", zIndex: 9999,
      display: "flex", alignItems: "center", justifyContent: "center", padding: 16 }}>
      <div style={{ background: "var(--c-surface, #fff)", color: "var(--c-text, #111)",
        borderRadius: 14, padding: 18, maxWidth: 460, width: "100%", lineHeight: 1.5 }}>
        <div style={{ fontWeight: 800, fontSize: 18 }}>
          {mode === "quit" ? "Agent Trader 종료" : "Agent Trader 상태"}
        </div>

        {!check && !failed && <div data-testid="tray-loading">확인 중…</div>}
        {failed && (
          <div data-testid="tray-check-failed" style={{ color: "#c0392b", fontWeight: 700 }}>
            보유·미체결 상태를 확인하지 못했습니다. 종료하면 장마감 자동 청산이 실행되지 않을 수 있습니다.
          </div>
        )}
        {check && (
          <div data-testid="tray-check">
            <div style={{ fontWeight: 700, color: risky ? "#c0392b" : "#15a05f" }}>
              {check.message_ko}
            </div>
            <div>프로그램 보유: {holdings.length}종목 · 미체결: {check.open_order_qty}주 · 청산 상태: {check.eod_state}</div>
          </div>
        )}

        <div data-testid="tray-guarantee" style={{ fontSize: 13, marginTop: 8, color: "#7a3b00" }}>
          ※ 창의 X 버튼은 프로그램을 끄지 않고 트레이로 숨깁니다 (장마감 자동 청산 계속).
          PC 전원 종료·강제 종료·절전·네트워크 단절 시에는 자동 청산을 보장할 수 없습니다.
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
          {mode === "quit" ? (
            <>
              <button type="button" autoFocus data-testid="tray-keep" onClick={toTray}
                style={{ flex: 1, padding: "10px 12px", fontWeight: 800 }}>
                트레이로 계속 실행 (권장)
              </button>
              <button type="button" data-testid="tray-quit" onClick={quit}
                style={{ flex: 1, padding: "10px 12px", fontWeight: 800,
                  background: risky ? "#c0392b" : undefined, color: risky ? "#fff" : undefined }}>
                {risky ? "그래도 종료 (자동 청산 중단)" : "종료"}
              </button>
            </>
          ) : (
            <button type="button" autoFocus data-testid="tray-close" onClick={close}
              style={{ flex: 1, padding: "10px 12px", fontWeight: 800 }}>
              닫기
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export default DesktopTrayBridge;
