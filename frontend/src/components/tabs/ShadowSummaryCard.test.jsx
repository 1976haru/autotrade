import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ShadowSummaryCard } from "./ShadowSummaryCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { shadowSummary: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.shadowSummary.mockResolvedValue({
    total: 0,
    would_have_approved_count: 0,
    would_have_rejected_count: 0,
    by_strategy: {},
    avg_estimated_slippage_bps: 0.0,
    actual_broker_orders_sent: 0,
    invariant_note: "LIVE_SHADOW 기록은 실제 주문이 아닙니다.",
  });
});


describe("<ShadowSummaryCard>", () => {
  it("shows three count tiles + invariant 0", () => {
    const { getByTestId } = render(<ShadowSummaryCard
      summary={{
        total: 12,
        would_have_approved_count: 9,
        would_have_rejected_count: 3,
        by_strategy: {},
        avg_estimated_slippage_bps: 1.5,
        actual_broker_orders_sent: 0,
        invariant_note: "x",
      }}
      loading={false} error="" />);
    expect(getByTestId("shadow-tile-total").textContent).toContain("12");
    expect(getByTestId("shadow-tile-approved").textContent).toContain("9");
    expect(getByTestId("shadow-tile-rejected").textContent).toContain("3");
    expect(getByTestId("shadow-tile-invariant").textContent).toContain("0");
  });

  it("renders the '실제 주문 아님' badge prominently", () => {
    const { getByTestId } = render(<ShadowSummaryCard
      summary={{
        total: 1, would_have_approved_count: 1, would_have_rejected_count: 0,
        by_strategy: {}, avg_estimated_slippage_bps: 0.0,
        actual_broker_orders_sent: 0, invariant_note: "x",
      }}
      loading={false} error="" />);
    expect(getByTestId("shadow-not-real-badge").textContent).toContain("실제 주문 아님");
  });

  it("flags invariant violation when actual_broker_orders_sent > 0", () => {
    const { getByTestId } = render(<ShadowSummaryCard
      summary={{
        total: 5, would_have_approved_count: 4, would_have_rejected_count: 1,
        by_strategy: {}, avg_estimated_slippage_bps: 0.0,
        actual_broker_orders_sent: 1, invariant_note: "x",
      }}
      loading={false} error="" />);
    const tile = getByTestId("shadow-tile-invariant");
    expect(tile.textContent).toContain("1");
    expect(tile.textContent).toContain("invariant 위반");
  });

  it("formats avg slippage to 2 decimal places", () => {
    const { getByText } = render(<ShadowSummaryCard
      summary={{
        total: 10, would_have_approved_count: 7, would_have_rejected_count: 3,
        by_strategy: {}, avg_estimated_slippage_bps: 1.234567,
        actual_broker_orders_sent: 0, invariant_note: "x",
      }}
      loading={false} error="" />);
    expect(getByText(/1\.23 bps/)).toBeTruthy();
  });

  it("shows the LIVE_SHADOW disclaimer text", () => {
    const { container } = render(<ShadowSummaryCard
      summary={{
        total: 0, would_have_approved_count: 0, would_have_rejected_count: 0,
        by_strategy: {}, avg_estimated_slippage_bps: 0.0,
        actual_broker_orders_sent: 0, invariant_note: "x",
      }}
      loading={false} error="" />);
    expect(container.textContent).toMatch(/실제 주문 없이 신호만 기록/);
    expect(container.textContent).toMatch(/실 체결과 다를 수 있/);
  });

  it("shows loading state without summary", () => {
    const { getByText } = render(<ShadowSummaryCard
      summary={null} loading={true} error="" />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows error state with retry button", () => {
    const onRefresh = vi.fn();
    const { getByText, getByTestId } = render(<ShadowSummaryCard
      summary={null} loading={false} error="boom" onRefresh={onRefresh} />);
    expect(getByTestId("shadow-summary-error").textContent).toContain("shadow 요약 조회 실패");
    fireEvent.click(getByText(/다시 시도/));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getByText } = render(<ShadowSummaryCard
      summary={{
        total: 0, would_have_approved_count: 0, would_have_rejected_count: 0,
        by_strategy: {}, avg_estimated_slippage_bps: 0.0,
        actual_broker_orders_sent: 0, invariant_note: "x",
      }}
      loading={false} error="" onRefresh={onRefresh} />);
    fireEvent.click(getByText(/새로고침/));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("renders nothing when summary is null and not loading/error", () => {
    const { container } = render(<ShadowSummaryCard
      summary={null} loading={false} error="" />);
    expect(container.firstChild).toBeNull();
  });
});


describe("useShadowSummary integration via card", () => {
  it("loads summary on mount and renders tile counts", async () => {
    const { useShadowSummary } = await import("./ShadowSummaryCard");
    backendApi.shadowSummary.mockResolvedValueOnce({
      total: 4,
      would_have_approved_count: 3,
      would_have_rejected_count: 1,
      by_strategy: { sma: 4 },
      avg_estimated_slippage_bps: 0.0,
      actual_broker_orders_sent: 0,
      invariant_note: "x",
    });

    function Probe() {
      const r = useShadowSummary();
      return (
        <ShadowSummaryCard
          summary={r.summary} loading={r.loading} error={r.error}
          onRefresh={r.refresh}
        />
      );
    }

    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.shadowSummary).toHaveBeenCalled());
    const tile = await findByTestId("shadow-tile-total");
    expect(tile.textContent).toContain("4");
  });
});
