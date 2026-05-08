import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../../services/backend/client";
import { WatchlistSummaryTile } from "./WatchlistSummaryTile";


vi.mock("../../services/backend/client", () => ({
  backendApi: { watchlistSummary: vi.fn() },
}));


describe("WatchlistSummaryTile", () => {
  beforeEach(() => {
    backendApi.watchlistSummary.mockReset();
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("renders the empty-state with a navigation link", async () => {
    backendApi.watchlistSummary.mockResolvedValue({
      active: null, active_item_count: 0, top_symbols: [],
      watchlist_count: 0, max_items: 200, recommended_items: 50,
    });
    const onNavigate = vi.fn();

    render(<WatchlistSummaryTile onNavigate={onNavigate} />);
    await waitFor(() => screen.getByTestId("watchlist-summary-tile"));

    fireEvent.click(screen.getByTestId("watchlist-summary-link"));
    expect(onNavigate).toHaveBeenCalledTimes(1);
  });

  it("renders the active watchlist with up to top 5 symbols", async () => {
    backendApi.watchlistSummary.mockResolvedValue({
      active: { id: 1, name: "core" }, active_item_count: 7,
      top_symbols: ["005930", "000660", "035720", "035420", "207940"],
      watchlist_count: 1, max_items: 200, recommended_items: 50,
    });

    render(<WatchlistSummaryTile />);
    await waitFor(() => screen.getByText("core"));

    expect(screen.getByText("7 / 200")).toBeTruthy();
    expect(screen.getByTestId("watchlist-summary-symbol-005930")).toBeTruthy();
    expect(screen.getByText(/외 2종/)).toBeTruthy();
  });

  it("shows error state on fetch failure", async () => {
    backendApi.watchlistSummary.mockRejectedValue(new Error("offline"));

    render(<WatchlistSummaryTile />);
    await waitFor(() => screen.getByTestId("watchlist-summary-error"));
    expect(screen.getByText("offline")).toBeTruthy();
  });
});
