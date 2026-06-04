import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { TradingStatusBar } from "./TradingStatusBar";
import { computeTradingStatus } from "../../utils/tradingStatus";

describe("TradingStatusBar", () => {
  afterEach(() => cleanup());
  it("renders single trading-status headline + today numbers", () => {
    const status = computeTradingStatus({ running: true, marketOpen: true, cycle: 5 });
    const { getByTestId } = render(
      <TradingStatusBar status={status} today={{ orderCount: 15, filledCount: 0, realizedPnl: null }} kstTime="10:44" />,
    );
    expect(getByTestId("trading-status-bar")).toBeTruthy();
    expect(getByTestId("trading-status-headline").textContent).toContain("거래 중");
    const today = getByTestId("trading-status-today").textContent;
    expect(today).toContain("15");   // 주문
    expect(today).toContain("0");    // 체결
    expect(today).toContain("—");    // realized null -> dash (no fabricated number)
    expect(getByTestId("trading-status-kst").textContent).toContain("10:44");
  });

  it("blocked state shows reason (긴급정지)", () => {
    const status = computeTradingStatus({ emergencyStop: true });
    const { getByTestId } = render(<TradingStatusBar status={status} today={{}} kstTime="11:00" />);
    expect(getByTestId("trading-status-headline").textContent).toContain("거래 불가");
    expect(getByTestId("trading-status-headline").textContent).toContain("긴급정지");
  });

  it("renders nothing when status missing", () => {
    const { container } = render(<TradingStatusBar status={null} />);
    expect(container.firstChild).toBeNull();
  });
});
