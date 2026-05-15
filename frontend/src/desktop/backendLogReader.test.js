/**
 * backendLogReader 단위 테스트.
 *
 * invariants:
 * - sanitizeLogText 가 sk-* / ghp_* / Bearer / KIS_APP_KEY=... 마스킹
 * - readBackendLog 가 invokeImpl 호출 (Tauri command 시뮬레이션)
 * - 비-Tauri 환경에서는 null 반환
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isBackendLogAvailable,
  readBackendLog,
  sanitizeLogText,
} from "./backendLogReader";


describe("sanitizeLogText", () => {
  it("redacts openai-style sk- tokens", () => {
    const s = "loaded openai key sk-abcdefghijklmnopqrstuvwxyz1234567890";
    const out = sanitizeLogText(s);
    expect(out).toContain("[REDACTED]");
    expect(out).not.toContain("sk-abcdefghijklmnop");
  });

  it("redacts github PAT", () => {
    const s = "git: ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa fetch";
    const out = sanitizeLogText(s);
    expect(out).toContain("[REDACTED]");
    expect(out).not.toContain("ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
  });

  it("redacts Bearer tokens", () => {
    const s = "Authorization: Bearer abcdef.ghijkl-mnopqr.stuvwx-yz0123";
    const out = sanitizeLogText(s);
    expect(out).toContain("[REDACTED]");
  });

  it("redacts KIS_APP_KEY=... env-style strings", () => {
    const s = "STDERR: env override KIS_APP_KEY=abcd1234efgh5678ijkl";
    const out = sanitizeLogText(s);
    expect(out).toContain("KIS_APP_KEY=[REDACTED]");
    expect(out).not.toContain("abcd1234efgh5678ijkl");
  });

  it("redacts KIS_APP_SECRET / ANTHROPIC_API_KEY similar pattern", () => {
    const s =
      "KIS_APP_SECRET=ZZZZZZZZZZZZZZZZZZZZ\n" +
      "ANTHROPIC_API_KEY=sk-ant-AAAAAAAAAAAAAAAA";
    const out = sanitizeLogText(s);
    expect(out).toContain("KIS_APP_SECRET=[REDACTED]");
    expect(out).toContain("ANTHROPIC_API_KEY=[REDACTED]");
    expect(out).not.toContain("ZZZZZZZZZZZZZZZZZZZZ");
    expect(out).not.toContain("sk-ant-AAAAAAAAAAAAAAAA");
  });

  it("passes through normal log lines unchanged", () => {
    const s = "[1234567890] STDOUT: INFO     127.0.0.1:8000 - GET /api/status 200";
    expect(sanitizeLogText(s)).toBe(s);
  });

  it("handles empty / null input", () => {
    expect(sanitizeLogText("")).toBe("");
    expect(sanitizeLogText(null)).toBe("");
    expect(sanitizeLogText(undefined)).toBe("");
  });
});


describe("readBackendLog", () => {
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it("returns null in non-Tauri environment", async () => {
    const out = await readBackendLog();
    expect(out).toBeNull();
  });

  it("calls invokeImpl when provided", async () => {
    const invoke = vi.fn(async () => "log content");
    const out = await readBackendLog({ invokeImpl: invoke });
    expect(invoke).toHaveBeenCalledWith("read_backend_log");
    expect(out).toBe("log content");
  });

  it("sanitizes invokeImpl response before returning", async () => {
    const invoke = vi.fn(
      async () =>
        "STDOUT: KIS_APP_KEY=secretvalue123456 ok\n" +
        "STDERR: nothing"
    );
    const out = await readBackendLog({ invokeImpl: invoke });
    expect(out).toContain("KIS_APP_KEY=[REDACTED]");
    expect(out).not.toContain("secretvalue123456");
  });

  it("uses window.__TAURI_INTERNALS__.invoke when present", async () => {
    const invoke = vi.fn(async () => "tauri log");
    window.__TAURI_INTERNALS__ = { invoke };
    const out = await readBackendLog();
    expect(invoke).toHaveBeenCalledWith("read_backend_log");
    expect(out).toBe("tauri log");
  });

  it("returns error string on invoke exception", async () => {
    const invoke = vi.fn(async () => {
      throw new Error("plugin error");
    });
    const out = await readBackendLog({ invokeImpl: invoke });
    expect(out).toContain("invoke error");
    expect(out).toContain("plugin error");
  });
});


describe("isBackendLogAvailable", () => {
  afterEach(() => {
    delete window.__TAURI_INTERNALS__;
  });

  it("returns false in non-Tauri environment", () => {
    expect(isBackendLogAvailable()).toBe(false);
  });

  it("returns true when __TAURI_INTERNALS__ is present", () => {
    window.__TAURI_INTERNALS__ = { dummy: true };
    expect(isBackendLogAvailable()).toBe(true);
  });
});
