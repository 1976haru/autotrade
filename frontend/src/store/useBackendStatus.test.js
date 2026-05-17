import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  backendApi,
  discoverBackendBaseUrl,
  getBackendBaseUrl,
} from "../services/backend/client";
import { useBackendStatus } from "./useBackendStatus";


vi.mock("../services/backend/client", () => ({
  backendApi: { getStatus: vi.fn() },
  discoverBackendBaseUrl: vi.fn(),
  getBackendBaseUrl: vi.fn(() => "http://127.0.0.1:8000"),
}));


describe("useBackendStatus", () => {
  beforeEach(() => {
    backendApi.getStatus.mockReset();
    discoverBackendBaseUrl.mockReset();
    getBackendBaseUrl.mockReturnValue("http://127.0.0.1:8000");
    // 기본: discovery 가 8000 success — 기존 test 동작 호환.
    discoverBackendBaseUrl.mockResolvedValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8000",
      port: 8000,
      viaHealth: false,
    });
  });

  it("fetches /api/status once on mount and exposes the result", async () => {
    const payload = {
      default_mode: "LIVE_MANUAL_APPROVAL",
      enable_live_trading: false,
      enable_ai_execution: false,
    };
    backendApi.getStatus.mockResolvedValue(payload);

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(backendApi.getStatus).toHaveBeenCalledTimes(1);
    expect(result.current.status).toEqual(payload);
    expect(result.current.error).toBe("");
    expect(result.current.viaFallback).toBe(false);
    expect(result.current.baseUrl).toBe("http://127.0.0.1:8000");
  });

  it("captures fetch errors into the error slot without crashing", async () => {
    backendApi.getStatus.mockRejectedValue(new Error("status-down"));

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("status-down");
    expect(result.current.status).toBeNull();
  });

  // fix/frontend-detects-fallback-backend-port: 8000 실패 + 8001 success 시
  // baseUrl 이 8001 로 update + viaFallback=true.
  it("uses fallback baseUrl when discovery returns 8001", async () => {
    discoverBackendBaseUrl.mockResolvedValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8001",
      port: 8001,
      viaHealth: false,
    });
    backendApi.getStatus.mockResolvedValue({ default_mode: "PAPER" });

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.baseUrl).toBe("http://127.0.0.1:8001");
    expect(result.current.viaFallback).toBe(true);
    expect(result.current.error).toBe("");
  });

  it("reports error when /api/status fails but still surfaces discovered baseUrl", async () => {
    discoverBackendBaseUrl.mockResolvedValue({
      ok: true,
      baseUrl: "http://127.0.0.1:8002",
      port: 8002,
      viaHealth: true,
    });
    backendApi.getStatus.mockRejectedValue(new Error("status route 500"));

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("status route 500");
    expect(result.current.baseUrl).toBe("http://127.0.0.1:8002");
    expect(result.current.viaFallback).toBe(true);
  });

  // ============================================================
  // fix/desktop-nonblocking-migration-health: db_ready 재폴링
  // ============================================================

  it("exposes dbReady=true when status.db_ready=true", async () => {
    backendApi.getStatus.mockResolvedValue({
      default_mode: "PAPER",
      db_ready: true,
      migration_status: "completed",
    });

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.dbReady).toBe(true);
    expect(result.current.status.db_ready).toBe(true);
    expect(result.current.status.migration_status).toBe("completed");
  });

  it("exposes dbReady=false when status.db_ready=false", async () => {
    backendApi.getStatus.mockResolvedValue({
      default_mode: "PAPER",
      db_ready: false,
      migration_status: "running",
    });

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.dbReady).toBe(false);
    expect(result.current.status.db_ready).toBe(false);
  });

  it("dbReady=false when status omits db_ready (backwards compat — no false alarm)", async () => {
    // 옛 backend (pre-fix) 가 db_ready 필드를 안 보내면 dbReady=false.
    // *backend offline 으로 오인되면 안 되지만* BackendOfflineBanner 측 분기
    // (`status.db_ready === false`) 가 *strict* 비교라 trigger 되지 않음.
    backendApi.getStatus.mockResolvedValue({ default_mode: "SIMULATION" });

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.dbReady).toBe(false);
    expect(result.current.status.db_ready).toBeUndefined();
  });

  // 주의: db_ready=false → db_ready=true 자동 전환 (re-poll) 의 *동적* 동작은
  // jsdom + React act + 실 timer 조합으로 안정적으로 테스트하기 어렵다. 본
  // 전환은 두 단으로 별도 검증:
  //  1) useBackendStatus 가 status.db_ready 를 *carry* 한다 — 위 dbReady=true /
  //     false 테스트로 검증.
  //  2) launcher 가 db_ready=false 응답을 받으면 DB_PREPARING 으로 분류하고,
  //     이후 db_ready=true 응답이 오면 READY 로 자동 전환한다 —
  //     backendLauncher.test.js "emits DB_PREPARING ... then READY" 로 검증.
});
