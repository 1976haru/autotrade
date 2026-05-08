import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useWatchlists, useWatchlistSummary } from "./useWatchlists";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    listWatchlists:      vi.fn(),
    watchlistSummary:    vi.fn(),
    createWatchlist:     vi.fn(),
    patchWatchlist:      vi.fn(),
    deleteWatchlist:     vi.fn(),
    addWatchlistItem:    vi.fn(),
    removeWatchlistItem: vi.fn(),
    importWatchlistCsv:  vi.fn(),
  },
}));


describe("useWatchlists", () => {
  beforeEach(() => {
    Object.values(backendApi).forEach((fn) => fn?.mockReset?.());
    backendApi.listWatchlists.mockResolvedValue({
      watchlists: [], max_items: 200, recommended_items: 50,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads watchlists on mount and exposes the limits", async () => {
    backendApi.listWatchlists.mockResolvedValue({
      watchlists: [{ id: 1, name: "core", item_count: 3, is_active: true }],
      max_items: 200, recommended_items: 50,
    });

    const { result } = renderHook(() => useWatchlists());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.watchlists).toHaveLength(1);
    expect(result.current.watchlists[0].name).toBe("core");
    expect(result.current.maxItems).toBe(200);
    expect(result.current.recommendedItems).toBe(50);
    expect(result.current.error).toBe("");
  });

  it("captures fetch errors", async () => {
    backendApi.listWatchlists.mockRejectedValue(new Error("offline"));

    const { result } = renderHook(() => useWatchlists());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("offline");
  });

  it("create then refresh", async () => {
    backendApi.listWatchlists
      .mockResolvedValueOnce({ watchlists: [], max_items: 200, recommended_items: 50 })
      .mockResolvedValueOnce({
        watchlists: [{ id: 1, name: "new", item_count: 0, is_active: false }],
        max_items: 200, recommended_items: 50,
      });
    backendApi.createWatchlist.mockResolvedValue({ id: 1, name: "new" });

    const { result } = renderHook(() => useWatchlists());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await result.current.create({ name: "new" });
    await waitFor(() => expect(result.current.watchlists).toHaveLength(1));
  });

  it("addItem propagates user-friendly error message", async () => {
    backendApi.addWatchlistItem.mockRejectedValue(
      new Error("관심종목은 한 목록당 최대 200개까지 등록할 수 있습니다."),
    );

    const { result } = renderHook(() => useWatchlists());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await expect(result.current.addItem(1, { symbol: "005930" }))
      .rejects.toThrow("최대 200개");
  });

  it("importCsv returns the summary and triggers refresh", async () => {
    backendApi.importWatchlistCsv.mockResolvedValue({
      added: 5, skipped: 2, invalid: 1, total_after_import: 50, errors: [],
    });

    const { result } = renderHook(() => useWatchlists());
    await waitFor(() => expect(result.current.loading).toBe(false));

    const out = await result.current.importCsv(1, "symbol\n005930\n");
    expect(out.added).toBe(5);
    expect(backendApi.listWatchlists).toHaveBeenCalledTimes(2);  // mount + post-import
  });
});


describe("useWatchlistSummary", () => {
  beforeEach(() => {
    backendApi.watchlistSummary.mockReset();
  });

  it("loads the summary on mount", async () => {
    backendApi.watchlistSummary.mockResolvedValue({
      active: { id: 1, name: "core" },
      active_item_count: 3,
      top_symbols: ["005930", "000660", "035720"],
      watchlist_count: 2,
      max_items: 200,
      recommended_items: 50,
    });

    const { result } = renderHook(() => useWatchlistSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.summary.active.name).toBe("core");
    expect(result.current.summary.top_symbols).toHaveLength(3);
  });

  it("falls back to null on error", async () => {
    backendApi.watchlistSummary.mockRejectedValue(new Error("offline"));

    const { result } = renderHook(() => useWatchlistSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.summary).toBe(null);
    expect(result.current.error).toBe("offline");
  });
});
