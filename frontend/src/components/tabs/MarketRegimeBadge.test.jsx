import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarketRegimeBadge } from "./MarketRegimeBadge";


vi.mock("../../services/backend/client", () => ({
  backendApi: { marketRegime: vi.fn() },
}));

import { backendApi } from "../../services/backend/client";


describe("<MarketRegimeBadge>", () => {
  beforeEach(() => { backendApi.marketRegime.mockReset(); });
  afterEach(cleanup);

  it("renders regime label and palette color", async () => {
    backendApi.marketRegime.mockResolvedValue({
      regime: "TREND_UP",
      trade_permission: "ALLOW",
      risk_multiplier: 1.0,
    });
    const { findByTestId } = render(<MarketRegimeBadge />);
    const label = await findByTestId("market-regime-label");
    expect(label.textContent).toContain("추세 상승");
  });

  it("renders nothing on error", async () => {
    backendApi.marketRegime.mockRejectedValue(new Error("offline"));
    const { container } = render(<MarketRegimeBadge />);
    await waitFor(() => {
      expect(container.querySelector('[data-testid="market-regime-badge"]')).toBeNull();
    });
  });

  it("falls back to raw regime label for unknown values", async () => {
    backendApi.marketRegime.mockResolvedValue({
      regime: "FUTURE_REGIME",
      trade_permission: "ALLOW",
      risk_multiplier: 1.0,
    });
    const { findByTestId } = render(<MarketRegimeBadge />);
    const label = await findByTestId("market-regime-label");
    expect(label.textContent).toContain("FUTURE_REGIME");
  });
});
