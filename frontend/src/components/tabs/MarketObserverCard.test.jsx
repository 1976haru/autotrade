import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MarketObserverCard, useMarketObserver } from "./MarketObserverCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { marketObserver: vi.fn() },
}));


const _DEFAULT_SNAPSHOT = {
  risk_level: "MEDIUM",
  recommended_stance: "DEFENSIVE",
  summary_lines: [
    "시장 위험도: 보통",
    "거래대금은 평소 수준입니다. 변동성이 평소보다 높습니다.",
    "신규 매수는 가능하지만 sizing 축소를 권장합니다.",
  ],
  turnover_state: "NORMAL",
  volatility_state: "ELEVATED",
  freshness_status: "FRESH",
  leading_sectors: ["반도체", "2차전지"],
  lagging_sectors: ["화학"],
  leading_themes: [],
  surge_count: 5,
  plunge_count: 3,
  indices: [],
  market_regime: null,
  reasons: [],
  is_order_signal: false,
  created_at: "2026-05-09T12:00:00+00:00",
};


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.marketObserver.mockResolvedValue(_DEFAULT_SNAPSHOT);
});


// ====================================================================
// 1. Card rendering
// ====================================================================


describe("<MarketObserverCard>", () => {
  it("renders 3-line summary lines", () => {
    const { getByTestId } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    expect(getByTestId("market-observer-line-0").textContent)
      .toContain("시장 위험도");
    expect(getByTestId("market-observer-line-1").textContent)
      .toMatch(/거래대금|변동성/);
    expect(getByTestId("market-observer-line-2").textContent)
      .toMatch(/매수|sizing|축소/);
  });

  it("renders '주문 신호 아님' badge prominently", () => {
    const { getByTestId } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    expect(getByTestId("market-observer-not-order-badge").textContent)
      .toContain("주문 신호 아님");
  });

  it("displays risk level + stance + key states", () => {
    const { container, getByTestId } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    expect(container.textContent).toContain("보통");      // risk level label
    expect(getByTestId("market-observer-stance").textContent)
      .toContain("보수적");                                // stance
    expect(container.textContent).toContain("NORMAL");
    expect(container.textContent).toContain("ELEVATED");
    expect(container.textContent).toContain("FRESH");
  });

  it("renders leading and lagging sector chips", () => {
    const { container } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    expect(container.textContent).toContain("반도체");
    expect(container.textContent).toContain("2차전지");
    expect(container.textContent).toContain("화학");
  });

  it("renders disclaimer that this is NOT an order signal", () => {
    const { container } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    expect(container.textContent).toMatch(/주문 신호가 아닙니다/);
  });

  it("does NOT render any BUY/SELL/HOLD buttons or text in primary CTA", () => {
    const { container } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error="" />,
    );
    // 어떤 enabled 버튼도 BUY/SELL/HOLD label을 가지지 않음.
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      expect(text).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행/);
    }
  });

  it("shows loading state without snapshot", () => {
    const { getByText } = render(
      <MarketObserverCard snapshot={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows friendly fallback text on error", () => {
    const { getByTestId } = render(
      <MarketObserverCard snapshot={null} loading={false}
                            error="boom" />,
    );
    const err = getByTestId("market-observer-error");
    expect(err.textContent).toMatch(/시장 관찰 데이터를/);
    // raw "boom" 메시지를 사용자에게 그대로 보여주지 않음 (friendly).
  });

  it("renders nothing when snapshot is null and not loading/error", () => {
    const { container } = render(
      <MarketObserverCard snapshot={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getAllByText } = render(
      <MarketObserverCard snapshot={_DEFAULT_SNAPSHOT}
                            loading={false} error=""
                            onRefresh={onRefresh} />,
    );
    fireEvent.click(getAllByText(/새로고침/)[0]);
    expect(onRefresh).toHaveBeenCalled();
  });

  it("shows market regime chip when present", () => {
    const withRegime = {
      ..._DEFAULT_SNAPSHOT,
      market_regime: {
        regime: "TREND_UP", confidence: 80,
        trade_permission: "ALLOW", reasons: [],
      },
    };
    const { container } = render(
      <MarketObserverCard snapshot={withRegime}
                            loading={false} error="" />,
    );
    expect(container.textContent).toContain("TREND_UP");
    expect(container.textContent).toContain("ALLOW");
  });
});


// ====================================================================
// 2. useMarketObserver integration
// ====================================================================


describe("useMarketObserver integration", () => {
  it("calls API on mount and renders into card", async () => {
    backendApi.marketObserver.mockResolvedValueOnce(_DEFAULT_SNAPSHOT);
    function Probe() {
      const r = useMarketObserver();
      return <MarketObserverCard
        snapshot={r.snapshot} loading={r.loading} error={r.error}
        onRefresh={r.refresh}
      />;
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.marketObserver).toHaveBeenCalled());
    const line0 = await findByTestId("market-observer-line-0");
    expect(line0.textContent).toContain("시장 위험도");
  });

  it("renders error state when API rejects", async () => {
    backendApi.marketObserver.mockRejectedValueOnce(new Error("network down"));
    function Probe() {
      const r = useMarketObserver();
      return <MarketObserverCard
        snapshot={r.snapshot} loading={r.loading} error={r.error}
      />;
    }
    const { findByTestId } = render(<Probe />);
    const err = await findByTestId("market-observer-error");
    expect(err.textContent).toMatch(/시장 관찰 데이터를/);
  });
});
