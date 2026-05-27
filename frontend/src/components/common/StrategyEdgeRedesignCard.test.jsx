/**
 * StrategyEdgeRedesignCard 테스트 — complete/notready/error + 주문/적용 버튼 0 + 자동적용 false.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { StrategyEdgeRedesignCard } from "./StrategyEdgeRedesignCard";

afterEach(cleanup);

function _complete() {
  return {
    available: true, verdict: "STRATEGY_EDGE_STILL_NOT_FOUND",
    conclusion: ["기존 4전략 + 신규 후보 6종 모두 비용 후 PF<1 — 진입 방향 자체가 틀림."],
    failure_analysis: {
      ORB: { gross_pf: 0.447, net_pf: 0.201, stop_first_ratio: 0.412, avg_mfe_bps: 79.3, avg_mae_bps: -106.6 },
      MOMENTUM: { gross_pf: 0.401, net_pf: 0.191, stop_first_ratio: 0.475, avg_mfe_bps: 100.2, avg_mae_bps: -117.2 },
      GAP: { gross_pf: 0.231, net_pf: 0.106, stop_first_ratio: 0.604, avg_mfe_bps: 135.8, avg_mae_bps: -152.6 },
      VWAP: { gross_pf: 0.287, net_pf: 0.136, stop_first_ratio: 0.538, avg_mfe_bps: 110.0, avg_mae_bps: -139.7 },
    },
    candidate_results: {
      ORB_CONFIRMATION: { profit_factor: 0.442, oos: { oos_pf: 0.662 }, mdd_pct: 78.3, verdict: "REJECT" },
      MOMENTUM_PULLBACK: { profit_factor: 0.455, oos: { oos_pf: 0.52 }, mdd_pct: 164.1, verdict: "REJECT" },
    },
    no_trade_filter: { orb_baseline_pf: 0.201, orb_filtered_pf: 0.199 },
    exit_structure_analysis: {
      evaluated_on: "VWAP_RECLAIM", scope: "OOS", n: 274,
      pf_by_structure: { "existing_1.0_1.5_30": 0.192, "wider_1.5_2.5_30": 0.339 },
    },
    survivors: [], watch: [], exclude_strategies: ["ORB", "MOMENTUM", "GAP", "VWAP"],
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
  };
}

const _api = (r) => ({ strategyEdgeRedesignLatest: vi.fn(async () => r) });

describe("<StrategyEdgeRedesignCard>", () => {
  it("complete: 실패원인/후보/verdict 표시 + 자동적용 false", async () => {
    render(<StrategyEdgeRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ser-verdict");
    expect(screen.getByTestId("ser-verdict").textContent).toContain("STRATEGY_EDGE_STILL_NOT_FOUND");
    expect(screen.getByTestId("ser-failure").textContent).toContain("ORB");
    expect(screen.getByTestId("ser-candidates").textContent).toContain("ORB_CONFIRMATION");
    expect(screen.getByTestId("ser-survivors").textContent).toContain("없음");
    expect(screen.getByTestId("ser-auto").textContent).toContain("false");
  });

  it("notready", async () => {
    render(<StrategyEdgeRedesignCard apiClient={_api({ available: false, verdict: "BACKTEST_INFRA_INCOMPLETE" })} />);
    expect(await screen.findByTestId("ser-notready")).toBeTruthy();
    expect(screen.queryByTestId("ser-verdict")).toBeNull();
  });

  it("error", async () => {
    render(<StrategyEdgeRedesignCard apiClient={{ strategyEdgeRedesignLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("ser-error")).toBeTruthy();
  });

  it("적용/실전/주문/자동매매 버튼 0개", async () => {
    const { container } = render(<StrategyEdgeRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ser-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<StrategyEdgeRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ser-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("자동 적용되지 않습니다");
  });
});
