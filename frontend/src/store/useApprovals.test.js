import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import {
  ACTIVE_POLL_MS,
  IDLE_POLL_MS,
  IDLE_THRESHOLD_MS,
  computePollIntervalMs,
  useApprovals,
} from "./useApprovals";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    listApprovals:        vi.fn(),
    listApprovalHistory:  vi.fn(),
    approveApproval:      vi.fn(),
    rejectApproval:       vi.fn(),
    cancelApproval:       vi.fn(),
  },
}));


describe("useApprovals", () => {
  beforeEach(() => {
    backendApi.listApprovals.mockReset();
    backendApi.listApprovalHistory.mockReset();
    backendApi.approveApproval.mockReset();
    backendApi.rejectApproval.mockReset();
    backendApi.cancelApproval.mockReset();
    // history defaults to [] so existing tests don't need to set it
    backendApi.listApprovalHistory.mockResolvedValue([]);
  });

  it("fetches the pending list on mount", async () => {
    backendApi.listApprovals.mockResolvedValue([{ id: 1, symbol: "005930", status: "PENDING" }]);
    const { result } = renderHook(() => useApprovals());

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.pending).toHaveLength(1);
    expect(result.current.pending[0].symbol).toBe("005930");
    expect(result.current.error).toBe("");
  });

  it("captures fetch errors into the error state", async () => {
    backendApi.listApprovals.mockRejectedValue(new Error("network down"));
    const { result } = renderHook(() => useApprovals());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("network down");
  });

  it("approve calls the API and refreshes the list", async () => {
    backendApi.listApprovals.mockResolvedValueOnce([{ id: 1, status: "PENDING" }]);
    backendApi.approveApproval.mockResolvedValueOnce({ approval: { id: 1, status: "APPROVED" } });
    backendApi.listApprovals.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.approve(1, { note: "looks good" });
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(1, { note: "looks good" });
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(2);
    expect(result.current.pending).toEqual([]);
  });

  it("approve forwards both decided_by and note when present", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval.mockResolvedValue({});
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.approve(3, { decided_by: "ops1", note: "looks good" });
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(3, {
      decided_by: "ops1", note: "looks good",
    });
  });

  it("approve drops empty fields so backend records null instead of \"\"", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval.mockResolvedValue({});
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.approve(4, { decided_by: "", note: "looks good" });
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(4, { note: "looks good" });
  });

  it("approve passes null when decision is empty object", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval.mockResolvedValue({});
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.approve(5, { decided_by: "", note: "" });
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(5, null);
  });

  it("reject calls the API and refreshes the list", async () => {
    backendApi.listApprovals.mockResolvedValueOnce([{ id: 7, status: "PENDING" }]);
    backendApi.rejectApproval.mockResolvedValueOnce({ id: 7, status: "REJECTED" });
    backendApi.listApprovals.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.reject(7);
    });

    expect(backendApi.rejectApproval).toHaveBeenCalledWith(7, null);
    expect(result.current.pending).toEqual([]);
  });

  it("approve passes null when no note is given", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval.mockResolvedValue({});
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.approve(42);
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(42, null);
  });

  it("cancel calls the API with the note and refreshes the list", async () => {
    backendApi.listApprovals.mockResolvedValueOnce([{ id: 9, status: "PENDING" }]);
    backendApi.cancelApproval.mockResolvedValueOnce({ id: 9, status: "CANCELLED" });
    backendApi.listApprovals.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.cancel(9, { note: "stale signal" });
    });

    expect(backendApi.cancelApproval).toHaveBeenCalledWith(9, { note: "stale signal" });
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(2);
    expect(result.current.pending).toEqual([]);
  });

  it("cancel passes null when no note is given", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.cancelApproval.mockResolvedValue({});
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.cancel(7);
    });

    expect(backendApi.cancelApproval).toHaveBeenCalledWith(7, null);
  });

  it("cancelMany sequences each id with the same decision and refreshes once", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.cancelApproval.mockResolvedValue({});
    backendApi.listApprovalHistory.mockResolvedValue([]);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    backendApi.listApprovals.mockClear();
    backendApi.listApprovalHistory.mockClear();
    backendApi.cancelApproval.mockClear();

    await act(async () => {
      await result.current.cancelMany([10, 11, 12], { decided_by: "ops1", note: "stale" });
    });

    expect(backendApi.cancelApproval).toHaveBeenCalledTimes(3);
    expect(backendApi.cancelApproval).toHaveBeenNthCalledWith(1, 10, { decided_by: "ops1", note: "stale" });
    expect(backendApi.cancelApproval).toHaveBeenNthCalledWith(2, 11, { decided_by: "ops1", note: "stale" });
    expect(backendApi.cancelApproval).toHaveBeenNthCalledWith(3, 12, { decided_by: "ops1", note: "stale" });
    // Single refresh + history fetch at the end (not 3 of each)
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(1);
    expect(backendApi.listApprovalHistory).toHaveBeenCalledTimes(1);
  });

  it("cancelMany with empty list is a no-op", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));
    backendApi.cancelApproval.mockClear();

    await act(async () => {
      await result.current.cancelMany([], { decided_by: "ops" });
    });

    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
  });

  it("approve refreshes the pending list on failure so backend-appended attempts surface", async () => {
    // 076: backend (PermissionGate) appends to PendingApproval.attempts on
    // re-eval failure. The hook needs to refresh after the failed call so
    // the new attempts entry appears in pending without waiting for the 5s
    // polling tick.
    backendApi.listApprovals.mockResolvedValueOnce([]);
    backendApi.approveApproval.mockRejectedValueOnce(new Error("재평가 거부됨"));
    backendApi.listApprovals.mockResolvedValueOnce([
      { id: 1, status: "PENDING", attempts: [{ at: "now", reasons: ["x"] }] },
    ]);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => { await result.current.approve(1); });

    // Mount fetch + post-failure refresh = 2 calls
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(2);
    expect(result.current.pending[0].attempts).toHaveLength(1);
  });

  it("approve returns {ok:true} on success and {ok:false, message} on failure", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("승인 시점 재평가에서 거부됨: emergency stop"));
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let res1, res2;
    await act(async () => { res1 = await result.current.approve(1); });
    await act(async () => { res2 = await result.current.approve(2); });

    expect(res1).toEqual({ ok: true });
    expect(res2.ok).toBe(false);
    expect(res2.message).toContain("재평가");
  });

  it("cancelMany returns {ok:true} on success and {ok:false} on partial failure", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([]);
    backendApi.cancelApproval
      .mockResolvedValueOnce({})           // call 1 → id 10 ok
      .mockResolvedValueOnce({})           // call 1 → id 11 ok
      .mockResolvedValueOnce({})           // call 2 → id 20 ok
      .mockRejectedValueOnce(new Error("boom")); // call 2 → id 21 fails

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    let res1, res2;
    await act(async () => { res1 = await result.current.cancelMany([10, 11], { note: "stale" }); });
    await act(async () => { res2 = await result.current.cancelMany([20, 21], { note: "stale" }); });

    expect(res1).toEqual({ ok: true });
    expect(res2.ok).toBe(false);
    expect(res2.message).toBe("boom");
  });

  it("cancelMany surfaces error and still refreshes so partial state shows up", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([]);
    backendApi.cancelApproval
      .mockResolvedValueOnce({})           // id 10 ok
      .mockRejectedValueOnce(new Error("boom"));  // id 11 fails

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    backendApi.listApprovals.mockClear();
    backendApi.listApprovalHistory.mockClear();

    await act(async () => {
      await result.current.cancelMany([10, 11, 12], { note: "stale" });
    });

    expect(backendApi.cancelApproval).toHaveBeenCalledTimes(2);
    expect(result.current.error).toBe("boom");
    // Refresh still fires after the failure so the UI reflects what got through
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(1);
    expect(backendApi.listApprovalHistory).toHaveBeenCalledTimes(1);
  });

  it("fetches history on mount", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([
      { id: 11, status: "APPROVED" },
      { id: 12, status: "CANCELLED" },
    ]);
    const { result } = renderHook(() => useApprovals());

    await waitFor(() => expect(result.current.history).toHaveLength(2));
    expect(backendApi.listApprovalHistory).toHaveBeenCalledTimes(1);
    expect(result.current.history[0].id).toBe(11);
  });

  it("cancel refreshes both pending list and history", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.cancelApproval.mockResolvedValueOnce({ id: 5, status: "CANCELLED" });
    backendApi.listApprovalHistory.mockResolvedValue([{ id: 5, status: "CANCELLED" }]);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.cancel(5);
    });

    // Mount + post-cancel = 2 history fetches
    expect(backendApi.listApprovalHistory).toHaveBeenCalledTimes(2);
  });

  it("history fetch failure surfaces in error without breaking pending", async () => {
    backendApi.listApprovals.mockResolvedValue([{ id: 1, status: "PENDING" }]);
    backendApi.listApprovalHistory.mockRejectedValue(new Error("history down"));

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.error).toBe("history down"));
    // PENDING list still loaded successfully
    expect(result.current.pending).toHaveLength(1);
  });

  it("refreshHistory accepts a status filter and forwards it to the API", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([]);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.refreshHistory("CANCELLED");
    });

    expect(backendApi.listApprovalHistory).toHaveBeenLastCalledWith({
      status: "CANCELLED", limit: 50, offset: 0,
    });
  });

  // ---------- 085: history pagination ----------

  it("history fetches first page on mount with offset=0 and limit=50", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([
      { id: 1, status: "APPROVED" },
    ]);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(backendApi.listApprovalHistory).toHaveBeenLastCalledWith({
      status: undefined, limit: 50, offset: 0,
    });
    // 1 row < 50 → no more available
    expect(result.current.historyHasMore).toBe(false);
  });

  it("historyHasMore stays true when first page returns exactly the page size", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    const fullPage = Array.from({ length: 50 }, (_, i) => ({ id: i, status: "APPROVED" }));
    backendApi.listApprovalHistory.mockResolvedValue(fullPage);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.historyHasMore).toBe(true);
  });

  it("loadMoreHistory appends the next page with the running offset", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    const page1 = Array.from({ length: 50 }, (_, i) => ({ id: 100 - i, status: "APPROVED" }));
    const page2 = Array.from({ length: 25 }, (_, i) => ({ id: 50 - i, status: "REJECTED" }));
    backendApi.listApprovalHistory
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.history).toHaveLength(50);
    expect(result.current.historyHasMore).toBe(true);

    await act(async () => { await result.current.loadMoreHistory(); });
    expect(backendApi.listApprovalHistory).toHaveBeenLastCalledWith({
      limit: 50, offset: 50,
    });
    expect(result.current.history).toHaveLength(75);
    // Second page returned less than full → no more
    expect(result.current.historyHasMore).toBe(false);
  });

  it("loadMoreHistory is a no-op once historyHasMore is false", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.listApprovalHistory.mockResolvedValue([{ id: 1, status: "APPROVED" }]);
    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    backendApi.listApprovalHistory.mockClear();
    await act(async () => { await result.current.loadMoreHistory(); });
    expect(backendApi.listApprovalHistory).not.toHaveBeenCalled();
  });

  it("post-action refresh resets history to first page (loses pagination)", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    const page1 = Array.from({ length: 50 }, (_, i) => ({ id: 100 - i, status: "APPROVED" }));
    const page2 = Array.from({ length: 50 }, (_, i) => ({ id: 50 - i, status: "REJECTED" }));
    backendApi.listApprovalHistory
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce(page2);

    const { result } = renderHook(() => useApprovals());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => { await result.current.loadMoreHistory(); });
    expect(result.current.history).toHaveLength(100);

    // Simulate a post-action refresh — should reset
    backendApi.listApprovalHistory.mockResolvedValueOnce([{ id: 1, status: "APPROVED" }]);
    await act(async () => { await result.current.refreshHistory(); });
    expect(result.current.history).toHaveLength(1);
    expect(result.current.historyHasMore).toBe(false);
  });
});


describe("computePollIntervalMs (100)", () => {
  const NOW = 10_000_000;

  it("returns active interval when pending count > 0", () => {
    expect(computePollIntervalMs({
      pendingCount: 1, lastActivityAt: NOW - IDLE_THRESHOLD_MS - 1000, now: NOW,
    })).toBe(ACTIVE_POLL_MS);
    expect(computePollIntervalMs({
      pendingCount: 100, lastActivityAt: NOW, now: NOW,
    })).toBe(ACTIVE_POLL_MS);
  });

  it("returns active interval when last activity is within IDLE_THRESHOLD", () => {
    expect(computePollIntervalMs({
      pendingCount: 0, lastActivityAt: NOW - 60_000, now: NOW, // 1 min ago
    })).toBe(ACTIVE_POLL_MS);
    expect(computePollIntervalMs({
      pendingCount: 0, lastActivityAt: NOW - (IDLE_THRESHOLD_MS - 1), now: NOW,
    })).toBe(ACTIVE_POLL_MS);
  });

  it("returns idle interval when pending=0 and last activity is older than threshold", () => {
    expect(computePollIntervalMs({
      pendingCount: 0, lastActivityAt: NOW - IDLE_THRESHOLD_MS, now: NOW,
    })).toBe(IDLE_POLL_MS);
    expect(computePollIntervalMs({
      pendingCount: 0, lastActivityAt: NOW - 2 * IDLE_THRESHOLD_MS, now: NOW,
    })).toBe(IDLE_POLL_MS);
  });

  it("constants reflect documented 5s/30s/5min trio", () => {
    expect(ACTIVE_POLL_MS).toBe(5_000);
    expect(IDLE_POLL_MS).toBe(30_000);
    expect(IDLE_THRESHOLD_MS).toBe(5 * 60 * 1000);
  });
});


describe("useApprovals adaptive polling (100)", () => {
  beforeEach(() => {
    backendApi.listApprovals.mockReset();
    backendApi.listApprovalHistory.mockReset();
    backendApi.approveApproval.mockReset();
    backendApi.rejectApproval.mockReset();
    backendApi.cancelApproval.mockReset();
    backendApi.listApprovalHistory.mockResolvedValue([]);
  });

  // mount + 첫 fetch까지 microtasks를 풀어 loading=false 상태로 도달.
  // vi.useFakeTimers 환경에선 testing-library의 waitFor가 못 도므로 직접 act.
  const _flush = async () => {
    // 여러 단계의 await chain을 모두 풀어야 scheduleNext()까지 도달한다.
    for (let i = 0; i < 6; i++) await Promise.resolve();
  };

  const _mount = async () => {
    let r;
    await act(async () => {
      r = renderHook(() => useApprovals());
      await _flush();
    });
    return r;
  };

  it("polls every 5s while pending is non-empty", async () => {
    backendApi.listApprovals.mockResolvedValue([{ id: 1, status: "PENDING" }]);
    vi.useFakeTimers();
    const r = await _mount();
    expect(r.result.current.loading).toBe(false);
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(5_000); await _flush(); });
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(2);

    await act(async () => { vi.advanceTimersByTime(5_000); await _flush(); });
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(3);

    r.unmount();
    vi.useRealTimers();
  });

  it("transitions to 30s interval after IDLE_THRESHOLD with empty queue", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    vi.useFakeTimers();
    const r = await _mount();

    // 첫 active-paced tick들 — _lastActivityRef는 mount 시점이라 IDLE_THRESHOLD
    // 안. 5s씩 4번 진행 → 활성.
    let tickCount = 1;  // mount fetch
    for (let i = 0; i < 4; i++) {
      await act(async () => { vi.advanceTimersByTime(5_000); await _flush(); });
      tickCount += 1;
    }
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(tickCount);

    // IDLE_THRESHOLD 너머로 5분 + 마진 점프. 다음 scheduleNext가 idle 30s를 골라야.
    await act(async () => { vi.advanceTimersByTime(5 * 60 * 1000); await _flush(); });
    const callsAfterIdleEntry = backendApi.listApprovals.mock.calls.length;

    // 25s — idle tick 아직 안 fire.
    await act(async () => { vi.advanceTimersByTime(25_000); await _flush(); });
    expect(backendApi.listApprovals.mock.calls.length).toBe(callsAfterIdleEntry);

    // 30s 넘기면 — idle tick fire.
    await act(async () => { vi.advanceTimersByTime(6_000); await _flush(); });
    expect(backendApi.listApprovals.mock.calls.length).toBeGreaterThan(callsAfterIdleEntry);

    r.unmount();
    vi.useRealTimers();
  });

  it("snaps back to active 5s interval when a new pending arrives", async () => {
    backendApi.listApprovals.mockResolvedValue([]);
    vi.useFakeTimers();
    const r = await _mount();

    // Drift past idle threshold
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000); await _flush(); });

    // 다음 idle tick에서 pending 1건 발견.
    backendApi.listApprovals.mockResolvedValue([{ id: 9, status: "PENDING" }]);
    await act(async () => { vi.advanceTimersByTime(IDLE_POLL_MS); await _flush(); });
    expect(r.result.current.pending).toHaveLength(1);

    // 그 다음 tick은 active-paced(5s).
    const callsBefore = backendApi.listApprovals.mock.calls.length;
    await act(async () => { vi.advanceTimersByTime(5_000); await _flush(); });
    expect(backendApi.listApprovals.mock.calls.length).toBe(callsBefore + 1);

    r.unmount();
    vi.useRealTimers();
  });

  it("approve action shifts schedule back to active for the *next* cycle", async () => {
    // Note: an in-flight idle 30s timer that started before approve() runs
    // to completion — approve doesn't preempt timers, only marks activity
    // for the *next* scheduleNext() call. So we let the in-flight idle tick
    // finish first, then verify the cycle after that one is active-paced.
    backendApi.listApprovals.mockResolvedValue([]);
    backendApi.approveApproval.mockResolvedValue({});
    vi.useFakeTimers();
    const r = await _mount();

    // Drift past idle threshold so the next scheduled tick is on an idle
    // 30s timer.
    await act(async () => { vi.advanceTimersByTime(10 * 60 * 1000); await _flush(); });

    // operator action — _lastActivityRef 갱신.
    await act(async () => { await r.result.current.approve(1, { note: "n" }); });

    // 진행 중인 idle 30s timer가 발사되도록 30s 진행.
    await act(async () => { vi.advanceTimersByTime(30_000); await _flush(); });
    const callsAfterIdleFires = backendApi.listApprovals.mock.calls.length;

    // 그 시점 scheduleNext()가 lastActivityRef를 보고 active 5s를 선택.
    // 5s 후 — active tick fire.
    await act(async () => { vi.advanceTimersByTime(5_000); await _flush(); });
    expect(backendApi.listApprovals.mock.calls.length).toBe(callsAfterIdleFires + 1);

    r.unmount();
    vi.useRealTimers();
  });
});
