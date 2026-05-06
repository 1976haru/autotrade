import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FuturesOrderAuditCard } from "./FuturesOrderAuditCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    futuresOrders:        vi.fn(),
    futuresOrdersSummary: vi.fn(),
  },
}));

const _ROW = (overrides = {}) => ({
  id: 1, created_at: "2026-05-07T01:00:00",
  mode: "SIMULATION", contract: "KOSPI200_F",
  side: "BUY", quantity: 1, order_type: "MARKET",
  leverage: 5.0, decision: "APPROVED", reasons: [],
  executed: true, broker_status: "FILLED",
  filled_quantity: 1, avg_fill_price: 350,
  margin_delta: 1000, liquidation_price: null,
  forced_liquidation: false, message: "",
  ...overrides,
});

const _SUMMARY = {
  total: 8,
  by_decision: { APPROVED: 6, REJECTED: 2 },
  forced_liquidation_count: 1,
  executed_count: 6,
  cumulative_margin_delta: 4500,
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.futuresOrders.mockResolvedValue([]);
  backendApi.futuresOrdersSummary.mockResolvedValue({
    total: 0, by_decision: {}, forced_liquidation_count: 0,
    executed_count: 0, cumulative_margin_delta: 0,
  });
});

describe("<FuturesOrderAuditCard>", () => {
  it("loads rows + summary on mount", async () => {
    backendApi.futuresOrders.mockResolvedValueOnce([_ROW()]);
    backendApi.futuresOrdersSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId, findByText } = render(<FuturesOrderAuditCard />);
    await findByText(/선물 주문 Audit/);
    const summary = await findByTestId("futures-summary");
    expect(summary.textContent).toContain("8");          // total
    expect(summary.textContent).toContain("6");          // executed
    expect(summary.textContent).toContain("1");          // forced
    await findByText(/누적 margin Δ/);
    await findByText("KOSPI200_F");
  });

  it("renders empty state when no rows", async () => {
    const { findByText } = render(<FuturesOrderAuditCard />);
    await findByText(/해당 조건의 선물 주문 없음/);
  });

  it("forced-only toggle requeries with forced=true then back to null", async () => {
    backendApi.futuresOrdersSummary.mockResolvedValue(_SUMMARY);
    const { findByTestId } = render(<FuturesOrderAuditCard />);
    const toggle = await findByTestId("forced-only-toggle");
    fireEvent.click(toggle);
    await waitFor(() => expect(backendApi.futuresOrders).toHaveBeenLastCalledWith(
      { limit: 50, forced: true },
    ));
    fireEvent.click(toggle);
    await waitFor(() => expect(backendApi.futuresOrders).toHaveBeenLastCalledWith(
      { limit: 50, forced: null },
    ));
  });

  it("renders FORCED badge on forced_liquidation rows", async () => {
    backendApi.futuresOrders.mockResolvedValueOnce([_ROW({ forced_liquidation: true })]);
    const { findByText } = render(<FuturesOrderAuditCard />);
    await findByText(/⚠ FORCED/);
  });

  it("renders error state when backend fails", async () => {
    backendApi.futuresOrders.mockRejectedValueOnce(new Error("offline"));
    const { findByText } = render(<FuturesOrderAuditCard />);
    await findByText(/선물 주문 audit 조회 실패: offline/);
  });

  it("refresh button re-queries both endpoints", async () => {
    const { findByText } = render(<FuturesOrderAuditCard />);
    await findByText(/해당 조건의 선물 주문 없음/);
    fireEvent.click(await findByText(/새로고침/));
    await waitFor(() => expect(backendApi.futuresOrders).toHaveBeenCalledTimes(2));
    expect(backendApi.futuresOrdersSummary).toHaveBeenCalledTimes(2);
  });
});
