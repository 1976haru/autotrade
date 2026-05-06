import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VirtualPositionsCard } from "./VirtualPositionsCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { virtualPositions: vi.fn() },
}));

const _POS = (overrides = {}) => ({
  symbol: "005930", strategy: "ai_orb",
  quantity: 10, avg_price: 70_000, last_price: 72_000,
  unrealized_pnl: 20_000, unrealized_pct: 0.0285,
  hold_seconds: 1_800, realized_pnl: 0,
  ...overrides,
});

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => { backendApi.virtualPositions.mockResolvedValue([]); });

describe("<VirtualPositionsCard>", () => {
  it("loads positions on mount and shows totals", async () => {
    backendApi.virtualPositions.mockResolvedValueOnce([
      _POS({ unrealized_pnl: 20_000, realized_pnl: 5_000 }),
      _POS({ symbol: "000660", unrealized_pnl: -3_000, realized_pnl: 1_000 }),
    ]);
    const { findByTestId, findByText } = render(<VirtualPositionsCard />);
    await findByText(/가상 포지션/);
    const totals = await findByTestId("virtual-positions-totals");
    expect(totals.textContent).toContain("실현 PnL");
    expect(totals.textContent).toContain("미실현 PnL");
  });

  it("renders empty state when no rows", async () => {
    const { findByText } = render(<VirtualPositionsCard />);
    await findByText(/오픈 포지션 없음/);
  });

  it("renders error state when backend fails", async () => {
    backendApi.virtualPositions.mockRejectedValueOnce(new Error("nope"));
    const { findByText } = render(<VirtualPositionsCard />);
    await findByText(/가상 포지션 조회 실패: nope/);
  });

  it("renders position rows with strategy badge", async () => {
    backendApi.virtualPositions.mockResolvedValueOnce([_POS()]);
    const { findByText } = render(<VirtualPositionsCard />);
    await findByText("005930");
    await findByText(/ai_orb/);
  });

  it("refresh button re-queries", async () => {
    const { findByText } = render(<VirtualPositionsCard />);
    await findByText(/오픈 포지션 없음/);
    fireEvent.click(await findByText(/새로고침/));
    await waitFor(() => expect(backendApi.virtualPositions).toHaveBeenCalledTimes(2));
  });
});
