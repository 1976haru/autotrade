import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { HistoryRow } from "./Approvals";


function _row(overrides = {}) {
  return {
    id: 42,
    symbol: "005930",
    side: "BUY",
    quantity: 10,
    order_type: "MARKET",
    limit_price: null,
    status: "APPROVED",
    mode: "LIVE_MANUAL_APPROVAL",
    decided_at: "2026-05-05T12:00:00+00:00",
    decided_by: "user",
    note: "ok",
    created_at: "2026-05-05T11:55:00+00:00",
    audit_id: 1,
    ...overrides,
  };
}


describe("<HistoryRow>", () => {
  afterEach(cleanup);

  it("renders the status badge with green color for APPROVED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "APPROVED" })} />);
    const badge = getByText("APPROVED");
    expect(badge.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("renders the status badge with red color for REJECTED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "REJECTED" })} />);
    expect(getByText("REJECTED").style.color).toBe("rgb(239, 68, 68)"); // #ef4444
  });

  it("renders the status badge with gray color for CANCELLED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "CANCELLED" })} />);
    expect(getByText("CANCELLED").style.color).toBe("rgb(148, 163, 184)"); // #94a3b8
  });

  it("includes decided_by, mode, and note in the secondary line", () => {
    const { container } = render(
      <HistoryRow a={_row({ note: "stale signal", decided_by: "trader1" })} />,
    );
    expect(container.textContent).toContain("LIVE_MANUAL_APPROVAL");
    expect(container.textContent).toContain("by trader1");
    expect(container.textContent).toContain("stale signal");
  });

  it("handles missing note and decided_by gracefully", () => {
    const { container } = render(
      <HistoryRow a={_row({ note: null, decided_by: null })} />,
    );
    // Just verify it renders without crashing
    expect(container.textContent).toContain("#42");
  });

  it("renders limit_price when present", () => {
    const { container } = render(
      <HistoryRow a={_row({ order_type: "LIMIT", limit_price: 75_000 })} />,
    );
    expect(container.textContent).toContain("LIMIT");
    expect(container.textContent).toContain("75,000원");
  });
});
