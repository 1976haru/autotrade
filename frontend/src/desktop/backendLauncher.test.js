import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  LAUNCHER_STATES,
  classifyLauncherState,
  isDesktopApp,
  launcherStateColor,
  launcherStateLabel,
  probeBackendOnce,
  startBackendPoll,
  summarizeForCard,
} from "./backendLauncher";


// ============================================================
// 1. classifyLauncherState
// ============================================================

describe("classifyLauncherState", () => {
  it("returns CONNECTING when statusOk=false", () => {
    expect(classifyLauncherState({ statusOk: false })).toBe(LAUNCHER_STATES.CONNECTING);
  });

  it("returns UNSAFE when ENABLE_LIVE_TRADING=true", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { enable_live_trading: true, kis_is_paper: true },
    });
    expect(state).toBe(LAUNCHER_STATES.UNSAFE);
  });

  it("returns UNSAFE when ENABLE_AI_EXECUTION=true", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { enable_ai_execution: true, kis_is_paper: true },
    });
    expect(state).toBe(LAUNCHER_STATES.UNSAFE);
  });

  it("returns UNSAFE when ENABLE_FUTURES_LIVE_TRADING=true", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { enable_futures_live_trading: true, kis_is_paper: true },
    });
    expect(state).toBe(LAUNCHER_STATES.UNSAFE);
  });

  it("returns UNSAFE when KIS_IS_PAPER=false", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { kis_is_paper: false },
    });
    expect(state).toBe(LAUNCHER_STATES.UNSAFE);
  });

  it("returns NEEDS_ENV when KIS key missing but mock available", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { kis_is_paper: true, enable_live_trading: false, enable_ai_execution: false },
      readiness: { can_run_kis_paper: false, can_run_mock: true },
    });
    expect(state).toBe(LAUNCHER_STATES.NEEDS_ENV);
  });

  it("returns READY when safety OK and readiness OK", () => {
    const state = classifyLauncherState({
      statusOk: true,
      safety: { kis_is_paper: true, enable_live_trading: false, enable_ai_execution: false },
      readiness: { can_run_kis_paper: true, can_run_mock: true },
    });
    expect(state).toBe(LAUNCHER_STATES.READY);
  });

  it("falls back to readiness.safety_flags if safety missing", () => {
    const state = classifyLauncherState({
      statusOk: true,
      readiness: {
        safety_flags: { enable_live_trading: true, kis_is_paper: true },
        can_run_kis_paper: false, can_run_mock: true,
      },
    });
    expect(state).toBe(LAUNCHER_STATES.UNSAFE);
  });
});


// ============================================================
// 2. labels / colors
// ============================================================

describe("launcherStateLabel / launcherStateColor", () => {
  it("labels every defined state", () => {
    for (const s of Object.values(LAUNCHER_STATES)) {
      const lbl = launcherStateLabel(s);
      expect(typeof lbl).toBe("string");
      expect(lbl.length).toBeGreaterThan(0);
    }
  });

  it("does not surface 'LIVE' / 'Place Order' in labels (invariant)", () => {
    const banned = [
      "LIVE 켜기",
      "실거래 시작",
      "Place Order",
      "지금 매수",
      "지금 매도",
      "실계좌",
    ];
    for (const s of Object.values(LAUNCHER_STATES)) {
      const lbl = launcherStateLabel(s);
      for (const b of banned) {
        expect(lbl).not.toContain(b);
      }
    }
  });

  it("assigns colors to every state", () => {
    for (const s of Object.values(LAUNCHER_STATES)) {
      expect(typeof launcherStateColor(s)).toBe("string");
    }
  });
});


// ============================================================
// 3. isDesktopApp
// ============================================================

describe("isDesktopApp", () => {
  const _original = {};
  beforeEach(() => {
    _original.tauri = window.__TAURI__;
    _original.tauriInternals = window.__TAURI_INTERNALS__;
    _original.tauriMeta = window.__TAURI_METADATA__;
    delete window.__TAURI__;
    delete window.__TAURI_INTERNALS__;
    delete window.__TAURI_METADATA__;
  });
  afterEach(() => {
    if (_original.tauri != null) window.__TAURI__ = _original.tauri;
    if (_original.tauriInternals != null) window.__TAURI_INTERNALS__ = _original.tauriInternals;
    if (_original.tauriMeta != null) window.__TAURI_METADATA__ = _original.tauriMeta;
  });

  it("returns false in browser env", () => {
    expect(isDesktopApp()).toBe(false);
  });

  it("returns true when __TAURI_INTERNALS__ is set", () => {
    window.__TAURI_INTERNALS__ = { invoke: () => null };
    expect(isDesktopApp()).toBe(true);
  });

  it("returns true when __TAURI__ is set", () => {
    window.__TAURI__ = {};
    expect(isDesktopApp()).toBe(true);
  });
});


// ============================================================
// 4. probeBackendOnce
// ============================================================

describe("probeBackendOnce", () => {
  it("returns statusOk=false when fetch throws", async () => {
    const probe = await probeBackendOnce({
      fetchImpl: () => { throw new Error("network down"); },
    });
    expect(probe.statusOk).toBe(false);
    expect(probe.error).toContain("network down");
  });

  it("returns statusOk=false on non-200", async () => {
    const probe = await probeBackendOnce({
      fetchImpl: vi.fn(async () => ({ ok: false, status: 500 })),
    });
    expect(probe.statusOk).toBe(false);
  });

  it("returns status + readiness on healthy backend", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/api/status")) {
        return { ok: true, async json() { return { default_mode: "PAPER", safety_flags: { kis_is_paper: true } }; } };
      }
      if (url.endsWith("/api/kis-paper/readiness")) {
        return { ok: true, async json() { return { ready: true, can_run_kis_paper: true, can_run_mock: true, safety_flags: { kis_is_paper: true } }; } };
      }
      return { ok: false, status: 404 };
    });
    const probe = await probeBackendOnce({ fetchImpl });
    expect(probe.statusOk).toBe(true);
    expect(probe.readiness?.can_run_kis_paper).toBe(true);
    expect(probe.safety?.kis_is_paper).toBe(true);
  });

  it("survives readiness failure (still statusOk=true)", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/api/status")) {
        return { ok: true, async json() { return { safety_flags: {} }; } };
      }
      throw new Error("readiness flap");
    });
    const probe = await probeBackendOnce({ fetchImpl });
    expect(probe.statusOk).toBe(true);
    expect(probe.readiness).toBeNull();
  });

  // fix/desktop-sidecar-runtime-diagnostics: /api/status 실패 시 /health
  // fallback 으로 backend 가 *살아있는지* 만이라도 확인.
  it("falls back to /health when /api/status returns 500", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/api/status")) {
        return { ok: false, status: 500 };
      }
      if (url.endsWith("/health")) {
        return { ok: true, async json() { return { ok: true }; } };
      }
      return { ok: false, status: 404 };
    });
    const probe = await probeBackendOnce({ fetchImpl });
    expect(probe.statusOk).toBe(true);
    // status object 가 health fallback 임을 표시.
    expect(probe.status.__via_health_fallback).toBe(true);
    // /health 호출이 실제로 일어남.
    const calls = fetchImpl.mock.calls.map((c) => c[0]);
    expect(calls.some((u) => u.endsWith("/health"))).toBe(true);
  });

  it("both /api/status and /health failing returns statusOk=false", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false, status: 500 }));
    const probe = await probeBackendOnce({ fetchImpl });
    expect(probe.statusOk).toBe(false);
    // 두 endpoint 모두 시도되었음을 확인.
    expect(fetchImpl.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("/health fallback exception also surfaces statusOk=false", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/api/status")) return { ok: false, status: 500 };
      throw new Error("health endpoint TCP reset");
    });
    const probe = await probeBackendOnce({ fetchImpl });
    expect(probe.statusOk).toBe(false);
  });
});


// ============================================================
// 5. startBackendPoll
// ============================================================

describe("startBackendPoll", () => {
  it("emits CONNECTING when backend is down", async () => {
    const updates = [];
    let cancelImmediate;
    await new Promise((resolve) => {
      const ctl = startBackendPoll({
        intervalMs: 5,
        timeoutMs: 50,
        fetchImpl: () => Promise.resolve({ ok: false, status: 500 }),
        onUpdate(snap) {
          updates.push(snap.state);
          if (updates.length >= 2) {
            cancelImmediate();
            resolve();
          }
        },
      });
      cancelImmediate = () => ctl.cancel();
    });
    expect(updates).toContain(LAUNCHER_STATES.CONNECTING);
  });

  it("emits READY then keeps polling", async () => {
    const updates = [];
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/api/status")) {
        return { ok: true, async json() { return { safety_flags: { kis_is_paper: true, enable_live_trading: false, enable_ai_execution: false } }; } };
      }
      if (url.endsWith("/api/kis-paper/readiness")) {
        return { ok: true, async json() { return { can_run_kis_paper: true, can_run_mock: true }; } };
      }
      return { ok: false };
    });
    await new Promise((resolve) => {
      const ctl = startBackendPoll({
        intervalMs: 5,
        timeoutMs: 200,
        fetchImpl,
        onUpdate(snap) {
          updates.push(snap.state);
          if (snap.state === LAUNCHER_STATES.READY) {
            ctl.cancel();
            resolve();
          }
        },
      });
    });
    expect(updates).toContain(LAUNCHER_STATES.READY);
  });

  it("emits FAILED after timeout", async () => {
    const updates = [];
    await new Promise((resolve) => {
      startBackendPoll({
        intervalMs: 5,
        timeoutMs: 20,
        fetchImpl: () => Promise.resolve({ ok: false, status: 500 }),
        nowImpl: (() => {
          let t = 0;
          return () => { t += 30; return t; };
        })(),
        onUpdate(snap) {
          updates.push(snap.state);
          if (snap.state === LAUNCHER_STATES.FAILED) {
            resolve();
          }
        },
      });
    });
    expect(updates).toContain(LAUNCHER_STATES.FAILED);
  });
});


// ============================================================
// 6. summarizeForCard
// ============================================================

describe("summarizeForCard", () => {
  it("returns IDLE summary when snapshot is null", () => {
    const out = summarizeForCard(null);
    expect(out.state).toBe(LAUNCHER_STATES.IDLE);
    expect(out.canStartTest).toBe(false);
  });

  it("canStartTest=true on READY", () => {
    const out = summarizeForCard({ state: LAUNCHER_STATES.READY });
    expect(out.canStartTest).toBe(true);
  });

  it("canStartTest=true on NEEDS_ENV (mock still usable)", () => {
    const out = summarizeForCard({ state: LAUNCHER_STATES.NEEDS_ENV });
    expect(out.canStartTest).toBe(true);
  });

  it("canStartTest=false on UNSAFE", () => {
    const out = summarizeForCard({ state: LAUNCHER_STATES.UNSAFE });
    expect(out.canStartTest).toBe(false);
  });

  it("hint mentions AppData path for NEEDS_ENV", () => {
    const out = summarizeForCard({ state: LAUNCHER_STATES.NEEDS_ENV });
    expect(out.hint).toContain("Autotrade");
  });

  it("hint mentions ENABLE_LIVE for UNSAFE", () => {
    const out = summarizeForCard({ state: LAUNCHER_STATES.UNSAFE });
    expect(out.hint).toContain("ENABLE_LIVE_TRADING");
  });

  it("no banned phrases in any hint (invariant)", () => {
    const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작"];
    for (const s of Object.values(LAUNCHER_STATES)) {
      const out = summarizeForCard({ state: s });
      for (const b of banned) {
        expect(out.hint).not.toContain(b);
      }
    }
  });
});
