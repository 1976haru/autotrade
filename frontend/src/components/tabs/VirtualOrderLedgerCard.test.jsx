import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VirtualOrderLedgerCard } from "./VirtualOrderLedgerCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    virtualOrders:        vi.fn(),
    virtualOrdersSummary: vi.fn(),
  },
}));

const _ROW = (overrides = {}) => ({
  id: 1, created_at: "2026-05-07T01:00:00",
  updated_at: "2026-05-07T01:00:00",
  symbol: "005930", side: "BUY", quantity: 5, order_type: "MARKET",
  status: "FILLED", strategy: "ai_orb",
  filled_quantity: 5, avg_fill_price: 75_000,
  ...overrides,
});

const _SUMMARY = {
  total: 12, pending_count: 4, terminal_count: 8,
  by_status: {
    NEW: 2, ACCEPTED: 1, PARTIALLY_FILLED: 1,
    FILLED: 6, CANCELLED: 1, REJECTED: 1, EXPIRED: 0,
  },
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.virtualOrders.mockResolvedValue([]);
  backendApi.virtualOrdersSummary.mockResolvedValue({
    total: 0, pending_count: 0, terminal_count: 0,
    by_status: { NEW: 0, ACCEPTED: 0, PARTIALLY_FILLED: 0,
                 FILLED: 0, CANCELLED: 0, REJECTED: 0, EXPIRED: 0 },
  });
});

describe("<VirtualOrderLedgerCard>", () => {
  it("loads summary + orders on mount", async () => {
    backendApi.virtualOrders.mockResolvedValueOnce([_ROW()]);
    backendApi.virtualOrdersSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId, findByText } = render(<VirtualOrderLedgerCard />);
    await findByText(/가상 주문 원장/);
    const summary = await findByTestId("virtual-orders-summary");
    expect(summary.textContent).toContain("총 12건");
    expect(summary.textContent).toContain("진행 4건");
    expect(summary.textContent).toContain("종결 8건");
    await findByText("005930");
  });

  it("renders empty state when no rows", async () => {
    const { findByText } = render(<VirtualOrderLedgerCard />);
    await findByText(/해당 조건의 주문 없음/);
  });

  it("clicking a status chip refilters by that status", async () => {
    backendApi.virtualOrdersSummary.mockResolvedValue(_SUMMARY);
    const { findByTestId } = render(<VirtualOrderLedgerCard />);
    await findByTestId("virtual-orders-summary");
    fireEvent.click(await findByTestId("virtual-filter-FILLED"));
    await waitFor(() => expect(backendApi.virtualOrders).toHaveBeenLastCalledWith(
      { limit: 50, status: "FILLED" },
    ));
  });

  it("ALL chip clears the status filter", async () => {
    backendApi.virtualOrdersSummary.mockResolvedValue(_SUMMARY);
    const { findByTestId } = render(<VirtualOrderLedgerCard />);
    await findByTestId("virtual-orders-summary");
    fireEvent.click(await findByTestId("virtual-filter-FILLED"));
    fireEvent.click(await findByTestId("virtual-filter-ALL"));
    await waitFor(() => expect(backendApi.virtualOrders).toHaveBeenLastCalledWith(
      { limit: 50, status: null },
    ));
  });

  it("renders error state when backend fails", async () => {
    backendApi.virtualOrders.mockRejectedValueOnce(new Error("boom"));
    const { findByText } = render(<VirtualOrderLedgerCard />);
    await findByText(/가상 주문 조회 실패: boom/);
  });

  it("status chips show counts from summary by_status", async () => {
    backendApi.virtualOrdersSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId } = render(<VirtualOrderLedgerCard />);
    // findByTestId resolves as soon as the chip shell exists — but the count
    // text "NEW 2" only appears after `setSummary(...)` propagates. On slower
    // runners (CI Linux) this races. waitFor polls the actual textContent so
    // the assertion is robust regardless of state propagation timing.
    const newChip = await findByTestId("virtual-filter-NEW");
    await waitFor(() => expect(newChip.textContent).toContain("NEW 2"));
    const filledChip = await findByTestId("virtual-filter-FILLED");
    await waitFor(() => expect(filledChip.textContent).toContain("FILLED 6"));
  });

  it("refresh button re-queries both endpoints", async () => {
    const { findByText } = render(<VirtualOrderLedgerCard />);
    await findByText(/해당 조건의 주문 없음/);
    fireEvent.click(await findByText(/새로고침/));
    await waitFor(() => expect(backendApi.virtualOrders).toHaveBeenCalledTimes(2));
    expect(backendApi.virtualOrdersSummary).toHaveBeenCalledTimes(2);
  });
});
