import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  backendFetch,
  discoverBackendBaseUrl,
  formatBackendErrorDetail,
  getBackendBaseUrl,
  resetBackendBaseUrl,
  setBackendBaseUrl,
} from "./client";


describe("formatBackendErrorDetail", () => {
  it("returns null for nullish detail", () => {
    expect(formatBackendErrorDetail(null)).toBeNull();
    expect(formatBackendErrorDetail(undefined)).toBeNull();
  });

  it("passes through string detail unchanged (FastAPI default error shape)", () => {
    expect(formatBackendErrorDetail("approval not found")).toBe("approval not found");
  });

  it("formats 070 risk_check_failed_at_approve into a Korean operator message", () => {
    const detail = {
      error: "risk_check_failed_at_approve",
      reasons: ["emergency stop is enabled", "max_order_notional 초과"],
    };
    expect(formatBackendErrorDetail(detail)).toBe(
      "승인 시점 재평가에서 거부됨: emergency stop is enabled / max_order_notional 초과",
    );
  });

  it("joins generic reasons array with slashes", () => {
    const detail = {
      decision: "REJECTED",
      reasons: ["a", "b", "c"],
    };
    expect(formatBackendErrorDetail(detail)).toBe("a / b / c");
  });

  it("falls back to JSON stringify for unfamiliar object shapes", () => {
    const detail = { weird: "shape", n: 42 };
    expect(formatBackendErrorDetail(detail)).toBe('{"weird":"shape","n":42}');
  });

  it("empty reasons array yields an empty string (caller falls back)", () => {
    const detail = { reasons: [] };
    expect(formatBackendErrorDetail(detail)).toBe("");
  });
});


// fix/frontend-detects-fallback-backend-port:
// mutable base URL + multi-port discovery.

describe("backend client base URL state", () => {
  beforeEach(() => {
    resetBackendBaseUrl();
  });

  it("getBackendBaseUrl defaults to 127.0.0.1:8000", () => {
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8000");
  });

  it("setBackendBaseUrl updates the global URL", () => {
    setBackendBaseUrl("http://127.0.0.1:8001");
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8001");
  });

  it("setBackendBaseUrl ignores empty / non-string", () => {
    setBackendBaseUrl("");
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8000");
    setBackendBaseUrl(null);
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8000");
  });

  it("resetBackendBaseUrl restores default", () => {
    setBackendBaseUrl("http://127.0.0.1:8001");
    resetBackendBaseUrl();
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8000");
  });
});


describe("backendFetch uses dynamic base URL", () => {
  beforeEach(() => {
    resetBackendBaseUrl();
  });

  afterEach(() => {
    if (typeof global.fetch === "function" && global.fetch.mockRestore) {
      global.fetch.mockRestore();
    }
  });

  it("uses current base URL on each call (not captured at module-load)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ result: "ok" }),
    }));
    global.fetch = fetchMock;

    await backendFetch("/api/status");
    expect(fetchMock.mock.calls[0][0]).toBe("http://127.0.0.1:8000/api/status");

    setBackendBaseUrl("http://127.0.0.1:8001");
    await backendFetch("/api/status");
    expect(fetchMock.mock.calls[1][0]).toBe("http://127.0.0.1:8001/api/status");
  });
});


describe("discoverBackendBaseUrl", () => {
  beforeEach(() => {
    resetBackendBaseUrl();
  });

  it("returns 8000 when /api/status succeeds on first port", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url === "http://127.0.0.1:8000/api/status")
        return { ok: true, status: 200, json: async () => ({}) };
      return { ok: false, status: 404 };
    });
    const r = await discoverBackendBaseUrl({ fetchImpl });
    expect(r.ok).toBe(true);
    expect(r.port).toBe(8000);
    expect(r.viaHealth).toBe(false);
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8000");
  });

  it("falls back to /health when /api/status fails on same port", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url === "http://127.0.0.1:8000/api/status")
        return { ok: false, status: 500 };
      if (url === "http://127.0.0.1:8000/health")
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      return { ok: false, status: 404 };
    });
    const r = await discoverBackendBaseUrl({ fetchImpl });
    expect(r.ok).toBe(true);
    expect(r.port).toBe(8000);
    expect(r.viaHealth).toBe(true);
  });

  it("falls back to 8001 when all 8000 endpoints fail", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url.startsWith("http://127.0.0.1:8000")) return { ok: false, status: 500 };
      if (url === "http://127.0.0.1:8001/api/status")
        return { ok: true, status: 200, json: async () => ({}) };
      return { ok: false, status: 404 };
    });
    const r = await discoverBackendBaseUrl({ fetchImpl });
    expect(r.ok).toBe(true);
    expect(r.port).toBe(8001);
    expect(getBackendBaseUrl()).toBe("http://127.0.0.1:8001");
  });

  it("further falls back to 8002", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url === "http://127.0.0.1:8002/api/status")
        return { ok: true, status: 200, json: async () => ({}) };
      return { ok: false, status: 500 };
    });
    const r = await discoverBackendBaseUrl({ fetchImpl });
    expect(r.ok).toBe(true);
    expect(r.port).toBe(8002);
  });

  it("returns ok=false when all ports fail", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: false, status: 500 }));
    const r = await discoverBackendBaseUrl({ fetchImpl });
    expect(r.ok).toBe(false);
  });

  it("custom ports list overrides default", async () => {
    const fetchImpl = vi.fn(async (url) => {
      if (url === "http://127.0.0.1:9999/api/status")
        return { ok: true, status: 200, json: async () => ({}) };
      return { ok: false, status: 500 };
    });
    const r = await discoverBackendBaseUrl({ ports: [9999], fetchImpl });
    expect(r.ok).toBe(true);
    expect(r.port).toBe(9999);
  });

  it("discovery response does not leak any secret keyword", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ default_mode: "PAPER", safety_flags: { kis_is_paper: true } }),
    }));
    const r = await discoverBackendBaseUrl({ fetchImpl });
    const s = JSON.stringify(r).toLowerCase();
    for (const forbidden of [
      "api_key", "secret", "password", "kis_app_key", "kis_app_secret",
      "anthropic", "openai",
    ]) {
      expect(s).not.toContain(forbidden);
    }
  });
});
