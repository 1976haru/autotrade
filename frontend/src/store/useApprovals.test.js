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
      await result.current.approve(1, "looks good");
    });

    expect(backendApi.approveApproval).toHaveBeenCalledWith(1, { note: "looks good" });
    expect(backendApi.listApprovals).toHaveBeenCalledTimes(2);
    expect(result.current.pending).toEqual([]);
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
      await result.current.cancel(9, "stale signal");
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
