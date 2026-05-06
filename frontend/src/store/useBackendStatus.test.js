import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useBackendStatus } from "./useBackendStatus";


vi.mock("../services/backend/client", () => ({
  backendApi: { getStatus: vi.fn() },
}));


describe("useBackendStatus", () => {
  beforeEach(() => {
    backendApi.getStatus.mockReset();
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
  });

  it("captures fetch errors into the error slot without crashing", async () => {
    backendApi.getStatus.mockRejectedValue(new Error("status-down"));

    const { result } = renderHook(() => useBackendStatus());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("status-down");
    expect(result.current.status).toBeNull();
  });
});
