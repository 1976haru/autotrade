import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { ACTIVE_POLL_MS, IDLE_POLL_MS, IDLE_THRESHOLD_MS } from "./useApprovals";
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


describe("useOrderAudits adaptive polling (105)", () => {
  beforeEach(() => { backendApi.listOrderAudits.mockReset(); });
  afterEach(() => { vi.useRealTimers(); });

  // setTimeout-recursive scheduler에서 callback이 await를 갖고 있으면
  // `advanceTimersByTime`은 첫 timer만 fire한 채 microtask 못 풀어 다음 timer
  // 등록 X. `advanceTimersByTimeAsync`가 timer fire와 microtask flush를 자동
  // 인터리브.
  const _mount = async () => {
    let r;
    await act(async () => {
      r = renderHook(() => useOrderAudits());
      // mount fetch resolve까지 microtask 풀기
      for (let i = 0; i < 6; i++) await Promise.resolve();
    });
    return r;
  };

  it("polls at the active 5s interval after mount", async () => {
    backendApi.listOrderAudits.mockResolvedValue([
      { id: 1, decision: "APPROVED", created_at: "2026-05-05T12:00:00+00:00" },
    ]);
    vi.useFakeTimers();
    const r = await _mount();
    expect(backendApi.listOrderAudits).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS); });
    expect(backendApi.listOrderAudits).toHaveBeenCalledTimes(2);

    await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS); });
    expect(backendApi.listOrderAudits).toHaveBeenCalledTimes(3);

    r.unmount();
  });

  it("transitions to 30s interval after IDLE_THRESHOLD without new top-id", async () => {
    backendApi.listOrderAudits.mockResolvedValue([
      { id: 7, decision: "APPROVED", created_at: "2026-05-05T12:00:00+00:00" },
    ]);
    vi.useFakeTimers();
    const r = await _mount();

    // mount fetch는 첫 non-null top id를 보고 _lastActivityRef를 mount 시각으로
    // 갱신 — 첫 5분 동안 active 5s 페이스. 매 tick에서 동일 top id 7이라 활동
    // 갱신 X. 5분 직후의 schedule이 idle 30s를 골라야.
    //
    // IDLE_THRESHOLD + 1s = 301s까지만 advance. 마지막 active tick은 t=300에서
    // fire되며 그 직후 schedule next가 idle 30s를 등록(= setTimeout 330s).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_THRESHOLD_MS + 1_000);
    });
    const callsAfterIdleEntry = backendApi.listOrderAudits.mock.calls.length;

    // 25s 더 → t=326. 등록된 idle timer는 330s, 아직 fire X.
    await act(async () => { await vi.advanceTimersByTimeAsync(25_000); });
    expect(backendApi.listOrderAudits.mock.calls.length).toBe(callsAfterIdleEntry);

    // 6s 더 → t=332. 330s timer fire.
    await act(async () => { await vi.advanceTimersByTimeAsync(6_000); });
    expect(backendApi.listOrderAudits.mock.calls.length).toBeGreaterThan(callsAfterIdleEntry);

    r.unmount();
  });

  it("a new top id (i.e. fresh order) snaps polling back to active 5s", async () => {
    backendApi.listOrderAudits.mockResolvedValue([]);
    vi.useFakeTimers();
    const r = await _mount();

    // Drift past idle threshold — empty result 동일이라 활동 X
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_THRESHOLD_MS + 60_000);
    });

    // 다음 idle tick에 새 top id 발견
    backendApi.listOrderAudits.mockResolvedValue([
      { id: 99, decision: "APPROVED", created_at: "2026-05-05T13:00:00+00:00" },
    ]);
    await act(async () => { await vi.advanceTimersByTimeAsync(IDLE_POLL_MS); });

    // 그 시점 _lastActivityRef가 갱신되어 다음 schedule은 active 5s.
    const callsBefore = backendApi.listOrderAudits.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(ACTIVE_POLL_MS); });
    expect(backendApi.listOrderAudits.mock.calls.length).toBe(callsBefore + 1);

    r.unmount();
  });

  it("repeated fetches with the same top id don't keep resetting activity", async () => {
    backendApi.listOrderAudits.mockResolvedValue([
      { id: 5, decision: "APPROVED", created_at: "2026-05-05T12:00:00+00:00" },
    ]);
    vi.useFakeTimers();
    const r = await _mount();

    // 같은 top id가 반복 fetch돼도 _lastActivityRef는 mount 시점 이후 갱신 X.
    // IDLE_THRESHOLD + 1s까지 active ticks 후 schedule이 idle 30s로 전환.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(IDLE_THRESHOLD_MS + 1_000);
    });
    const callsAtIdleEntry = backendApi.listOrderAudits.mock.calls.length;

    // 25s — idle 30s tick not yet (다음 timer는 t=330s)
    await act(async () => { await vi.advanceTimersByTimeAsync(25_000); });
    expect(backendApi.listOrderAudits.mock.calls.length).toBe(callsAtIdleEntry);

    r.unmount();
  });
});
