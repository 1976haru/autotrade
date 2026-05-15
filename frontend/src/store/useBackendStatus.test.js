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
});
