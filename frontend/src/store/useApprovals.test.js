import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useApprovals } from "./useApprovals";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    listApprovals:    vi.fn(),
    approveApproval:  vi.fn(),
    rejectApproval:   vi.fn(),
  },
}));


describe("useApprovals", () => {
  beforeEach(() => {
    backendApi.listApprovals.mockReset();
    backendApi.approveApproval.mockReset();
    backendApi.rejectApproval.mockReset();
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
});
