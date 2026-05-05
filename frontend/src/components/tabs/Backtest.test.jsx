import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { CompareTable, EquityCurve } from "./Backtest";


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


function _runRow(overrides = {}) {
  return {
    run_id: 1,
    strategy: "sma_crossover",
    params: { short: 5, long: 20 },
    bars_processed: 30,
    initial_cash: 10_000_000,
    final_cash: 10_050_000,
    total_pnl: 50_000,
    win_count: 3, loss_count: 2,
    win_rate: 0.6, max_drawdown: 5_000,
    avg_win: 30_000, avg_loss: -10_000,
    profit_factor: 3.0, sharpe_ratio: 1.5,
    data_source: "bars",
    trades: [],
    ...overrides,
  };
}


describe("<CompareTable>", () => {
  afterEach(cleanup);

  it("returns null when comparison is null", () => {
    const { container } = render(<CompareTable comparison={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders one row per run with rank starting at 1", () => {
    const { container, getAllByTestId } = render(
      <CompareTable comparison={{
        sort_by: "total_pnl", bars_processed: 30,
        runs: [_runRow({ run_id: 1 }), _runRow({ run_id: 2 }), _runRow({ run_id: 3 })],
      }} />,
    );
    const rows = getAllByTestId("compare-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].getAttribute("data-rank")).toBe("0");
    expect(rows[2].getAttribute("data-rank")).toBe("2");
    // Rank label visible
    expect(container.textContent).toContain("1");
    expect(container.textContent).toContain("3");
  });

  it("highlights the winner (rank 0) with a different background", () => {
    const { getAllByTestId } = render(
      <CompareTable comparison={{
        sort_by: "total_pnl", bars_processed: 30,
        runs: [_runRow({ run_id: 1, total_pnl: 80_000 }),
               _runRow({ run_id: 2, total_pnl: 20_000 })],
      }} />,
    );
    const rows = getAllByTestId("compare-row");
    expect(rows[0].style.background).not.toBe(rows[1].style.background);
  });

  it("renders sharpe and profit_factor as em-dashes when null", () => {
    const { container } = render(
      <CompareTable comparison={{
        sort_by: "total_pnl", bars_processed: 30,
        runs: [_runRow({ sharpe_ratio: null, profit_factor: null })],
      }} />,
    );
    // Two em-dashes for the two null fields
    const dashCount = (container.textContent.match(/—/g) || []).length;
    expect(dashCount).toBeGreaterThanOrEqual(2);
  });

  it("shows the sort_by label in the header line", () => {
    const { container } = render(
      <CompareTable comparison={{
        sort_by: "sharpe_ratio", bars_processed: 30,
        runs: [_runRow()],
      }} />,
    );
    expect(container.textContent).toContain("sharpe_ratio");
  });
});
