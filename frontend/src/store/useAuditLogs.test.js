import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useEmergencyStopAudits, useOrderAudits } from "./useAuditLogs";


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

  it("fetches the first page (offset=0, limit=50) on mount", async () => {
    backendApi.emergencyStopHistory.mockResolvedValue([
      { id: 2, enabled: false, created_at: "2026-05-05T12:05:00+00:00" },
      { id: 1, enabled: true,  created_at: "2026-05-05T12:00:00+00:00" },
    ]);

    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(backendApi.emergencyStopHistory).toHaveBeenCalledWith({ offset: 0, limit: 50 });
    expect(result.current.items).toHaveLength(2);
    expect(result.current.items[0].id).toBe(2);
    // First page returned fewer than 50 — no more available
    expect(result.current.hasMore).toBe(false);
  });

  it("hasMore stays true when first page returns exactly the page size", async () => {
    const page = Array.from({ length: 50 }, (_, i) => ({
      id: 100 - i, enabled: i % 2 === 0,
      created_at: new Date(2026, 4, 5, 12, 0, 50 - i).toISOString(),
    }));
    backendApi.emergencyStopHistory.mockResolvedValue(page);

    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.hasMore).toBe(true);
  });

  it("loadMore fetches the next page with the running offset and appends", async () => {
    const page1 = Array.from({ length: 50 }, (_, i) => ({
      id: 100 - i, enabled: false, created_at: `2026-05-05T12:00:${String(i).padStart(2, "0")}+00:00`,
    }));
    const page2 = Array.from({ length: 25 }, (_, i) => ({
      id: 50 - i, enabled: true, created_at: `2026-05-04T12:00:${String(i).padStart(2, "0")}+00:00`,
    }));
    backendApi.emergencyStopHistory
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2);

    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toHaveLength(50);
    expect(result.current.hasMore).toBe(true);

    await act(async () => { await result.current.loadMore(); });
    expect(backendApi.emergencyStopHistory).toHaveBeenLastCalledWith({ offset: 50, limit: 50 });
    expect(result.current.items).toHaveLength(75);
    // Second page was less than full → no more
    expect(result.current.hasMore).toBe(false);
  });

  it("loadMore is a no-op once hasMore is false", async () => {
    backendApi.emergencyStopHistory.mockResolvedValue([{ id: 1, enabled: true,
      created_at: "2026-05-05T12:00:00+00:00" }]);
    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => { await result.current.loadMore(); });
    // Mount fetch only — loadMore did not call again
    expect(backendApi.emergencyStopHistory).toHaveBeenCalledTimes(1);
  });

  it("captures fetch errors into the error state", async () => {
    backendApi.emergencyStopHistory.mockRejectedValue(new Error("history-down"));
    const { result } = renderHook(() => useEmergencyStopAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("history-down");
    expect(result.current.items).toEqual([]);
  });
});


describe("useOrderAudits pagination", () => {
  beforeEach(() => {
    backendApi.listOrderAudits.mockReset();
  });

  it("fetches the first page on mount with offset=0", async () => {
    backendApi.listOrderAudits.mockResolvedValue([
      { id: 1, decision: "APPROVED", created_at: "2026-05-05T12:00:00+00:00" },
    ]);
    const { result } = renderHook(() => useOrderAudits());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(backendApi.listOrderAudits).toHaveBeenCalledWith({ offset: 0, limit: 50 });
    expect(result.current.hasMore).toBe(false);
  });
});
