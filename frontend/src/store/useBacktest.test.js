import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useBacktest } from "./useBacktest";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    runBacktest:      vi.fn(),
    compareBacktests: vi.fn(),
  },
}));


describe("useBacktest", () => {
  beforeEach(() => {
    backendApi.runBacktest.mockReset();
    backendApi.compareBacktests.mockReset();
  });

  it("submit stores the run result and clears errors", async () => {
    backendApi.runBacktest.mockResolvedValue({ run_id: 1, total_pnl: 5_000 });
    const { result } = renderHook(() => useBacktest());

    await act(async () => {
      await result.current.submit({ strategy: "sma_crossover" });
    });

    expect(result.current.run.run_id).toBe(1);
    expect(result.current.error).toBe("");
  });

  it("submit captures errors without crashing", async () => {
    backendApi.runBacktest.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useBacktest());

    await act(async () => {
      await result.current.submit({ strategy: "sma_crossover" });
    });

    expect(result.current.run).toBeNull();
    expect(result.current.error).toBe("boom");
  });

  it("compare stores the comparison result and forwards request to API", async () => {
    backendApi.compareBacktests.mockResolvedValue({
      sort_by: "total_pnl",
      bars_processed: 30,
      runs: [
        { run_id: 11, params: { short: 5, long: 20 }, total_pnl: 8_000 },
        { run_id: 12, params: { short: 3, long: 7 },  total_pnl: 3_000 },
      ],
    });
    const { result } = renderHook(() => useBacktest());

    const req = {
      strategy: "sma_crossover",
      param_sets: [{ short: 5, long: 20 }, { short: 3, long: 7 }],
      sort_by: "total_pnl",
    };
    await act(async () => {
      await result.current.compare(req);
    });

    expect(backendApi.compareBacktests).toHaveBeenCalledWith(req);
    expect(result.current.comparison.runs).toHaveLength(2);
    expect(result.current.comparison.sort_by).toBe("total_pnl");
    expect(result.current.error).toBe("");
  });

  it("compare error is captured and comparison stays null", async () => {
    backendApi.compareBacktests.mockRejectedValue(new Error("compare-down"));
    const { result } = renderHook(() => useBacktest());

    await act(async () => {
      await result.current.compare({ strategy: "x", param_sets: [] });
    });

    expect(result.current.comparison).toBeNull();
    expect(result.current.error).toBe("compare-down");
  });

  it("comparison and run states do not overwrite each other", async () => {
    backendApi.runBacktest.mockResolvedValue({ run_id: 1, total_pnl: 100 });
    backendApi.compareBacktests.mockResolvedValue({
      sort_by: "total_pnl", bars_processed: 5, runs: [{ run_id: 2 }],
    });
    const { result } = renderHook(() => useBacktest());

    await act(async () => { await result.current.submit({ strategy: "a" }); });
    expect(result.current.run.run_id).toBe(1);

    await act(async () => { await result.current.compare({ strategy: "a", param_sets: [] }); });
    expect(result.current.comparison.runs[0].run_id).toBe(2);
    // Single run survives the comparison call
    expect(result.current.run.run_id).toBe(1);
  });

  it("loading flag flips around compare calls", async () => {
    let resolveFn;
    backendApi.compareBacktests.mockImplementation(
      () => new Promise((resolve) => { resolveFn = resolve; }),
    );
    const { result } = renderHook(() => useBacktest());

    let promise;
    act(() => {
      promise = result.current.compare({ strategy: "x", param_sets: [] });
    });
    await waitFor(() => expect(result.current.loading).toBe(true));

    await act(async () => {
      resolveFn({ sort_by: "total_pnl", bars_processed: 0, runs: [] });
      await promise;
    });
    expect(result.current.loading).toBe(false);
  });
});
