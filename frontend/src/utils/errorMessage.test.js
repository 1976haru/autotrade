import { afterEach, describe, expect, it, vi } from "vitest";

import { friendlyErrorMessage, isDemoBuild } from "./errorMessage";


describe("friendlyErrorMessage", () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it("returns null for null/undefined/empty", () => {
    expect(friendlyErrorMessage(null)).toBeNull();
    expect(friendlyErrorMessage(undefined)).toBeNull();
    expect(friendlyErrorMessage("")).toBeNull();
  });

  it("converts 'Failed to fetch' to uvicorn hint by default", () => {
    const out = friendlyErrorMessage("Failed to fetch");
    expect(out).toContain("백엔드");
    expect(out).toContain("uvicorn");
    expect(out).not.toContain("Failed to fetch");
  });

  it("converts network errors to demo hint when VITE_DEMO_MODE=true", () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    const out = friendlyErrorMessage("Failed to fetch");
    expect(out).toContain("GitHub Pages");
    expect(out).not.toContain("Failed to fetch");
  });

  it("treats other network phrases consistently", () => {
    for (const raw of ["NetworkError when attempting", "ERR_NETWORK", "Load failed"]) {
      const out = friendlyErrorMessage(raw);
      expect(out).not.toContain(raw);
      expect(out.length).toBeGreaterThan(10);
    }
  });

  it("passes through meaningful Korean messages unchanged", () => {
    const out = friendlyErrorMessage("승인 시점 재평가에서 거부됨: max_order_notional");
    expect(out).toContain("승인 시점 재평가");
  });

  it("accepts an Error object via .message", () => {
    const out = friendlyErrorMessage(new Error("Failed to fetch"));
    expect(out).toContain("백엔드");
    expect(out).not.toContain("Failed to fetch");
  });
});


describe("isDemoBuild", () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it("returns false by default", () => {
    vi.stubEnv("VITE_DEMO_MODE", "");
    expect(isDemoBuild()).toBe(false);
  });

  it("returns true when 'true' string is set", () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    expect(isDemoBuild()).toBe(true);
  });
});
