import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useEmergencyStopAudits } from "./useAuditLogs";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    listOrderAudits:      vi.fn(),
    listAiAudits:         vi.fn(),
    listBacktestRuns:     vi.fn(),
    emergencyStopHistory: vi.fn(),
  },
}));


describe("useEmergencyStopAudits", () => {
  beforeEach(() => {
    backendApi.emergencyStopHistory.mockReset();
  });

  it("fetches the emergency-stop history with limit 50 on mount", async () => {
    backendApi.emergencyStopHistory.mockResolvedValue([
      { id: 2, enabled: false, created_at: "2026-05-05T12:05:00+00:00" },
      { id: 1, enabled: true,  created_at: "2026-05-05T12:00:00+00:00" },
    ]);

    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(backendApi.emergencyStopHistory).toHaveBeenCalledWith({ limit: 50 });
    expect(result.current.items).toHaveLength(2);
    expect(result.current.items[0].id).toBe(2);
  });

  it("captures fetch errors into the error state", async () => {
    backendApi.emergencyStopHistory.mockRejectedValue(new Error("history-down"));
    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("history-down");
    expect(result.current.items).toEqual([]);
  });
});
