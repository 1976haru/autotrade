import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useApprovals } from "./useApprovals";


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

    expect(backendApi.listApprovalHistory).toHaveBeenLastCalledWith({ status: "CANCELLED" });
  });
});
