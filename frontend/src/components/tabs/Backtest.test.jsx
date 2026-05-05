import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EquityCurve } from "./Backtest";


function _trade(pnl) {
  return {
    symbol: "X",
    entry_ts: "2026-01-01T00:00:00+00:00",
    entry_price: 100,
    exit_ts: "2026-01-02T00:00:00+00:00",
    exit_price: 100 + pnl,
    quantity: 1,
    pnl,
  };
}


describe("<EquityCurve>", () => {
  afterEach(cleanup);

  it("renders nothing when there are no trades", () => {
    const { container } = render(<EquityCurve trades={[]} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("renders nothing when trades is null", () => {
    const { container } = render(<EquityCurve trades={null} />);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("draws an svg with N+1 points (including baseline at 0) for N trades", () => {
    const { container } = render(
      <EquityCurve trades={[_trade(50), _trade(-20), _trade(30)]} />,
    );
    const polyline = container.querySelector("polyline");
    expect(polyline).toBeTruthy();
    // points string is space-separated "x,y" tokens
    const tokens = polyline.getAttribute("points").trim().split(/\s+/);
    expect(tokens).toHaveLength(4); // 3 trades + 1 starting baseline
  });

  it("uses green stroke when final cumulative pnl is positive", () => {
    const { container } = render(
      <EquityCurve trades={[_trade(50), _trade(-10)]} />,
    );
    const polyline = container.querySelector("polyline");
    expect(polyline.getAttribute("stroke")).toBe("#22c55e");
  });

  it("uses red stroke when final cumulative pnl is negative", () => {
    const { container } = render(
      <EquityCurve trades={[_trade(20), _trade(-50)]} />,
    );
    const polyline = container.querySelector("polyline");
    expect(polyline.getAttribute("stroke")).toBe("#ef4444");
  });

  it("exposes the final pnl as a data attribute for sanity-checking", () => {
    const { container } = render(
      <EquityCurve trades={[_trade(100), _trade(-30)]} />,
    );
    const svg = container.querySelector('[data-testid="equity-curve"]');
    expect(svg.getAttribute("data-final-pnl")).toBe("70");
  });

  it("includes a dashed zero baseline line", () => {
    const { container } = render(<EquityCurve trades={[_trade(40)]} />);
    const baseline = container.querySelector("line[stroke-dasharray]");
    expect(baseline).toBeTruthy();
    expect(baseline.getAttribute("stroke-dasharray")).toBe("2,3");
  });

  it("draws a positive equity curve that monotonically rises for all-winners", () => {
    // Smoke test: y-coordinates should monotonically decrease (svg y grows down)
    // when cumulative pnl monotonically rises.
    const { container } = render(
      <EquityCurve trades={[_trade(10), _trade(20), _trade(30)]} />,
    );
    const tokens = container.querySelector("polyline").getAttribute("points").split(/\s+/);
    const ys = tokens.map((t) => parseFloat(t.split(",")[1]));
    for (let i = 1; i < ys.length; i++) {
      expect(ys[i]).toBeLessThanOrEqual(ys[i - 1]);
    }
  });
});
