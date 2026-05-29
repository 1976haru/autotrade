/**
 * MeanReversionExitResearchCard 테스트 — complete/notready/error/loading + 버튼/경고 lock.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { MeanReversionExitResearchCard } from "./MeanReversionExitResearchCard";

afterEach(cleanup);

function _complete() {
  return {
    available: true, verdict: "EXIT_REDESIGN_REJECTED", is_research_only: true,
    conclusion: ["모든 entry×exit 조합이 비용 후 PF<1 — 왕복 31bps 가 +30bps 목표보다 큼."],
    exit_plans: ["EXISTING_TREND", "SMALL_T30_S60", "TIME_15"],
    matrix: {
      VWAP_DEVIATION_REVERT: {
        TIME_15: { trade_count: 493, net_pf: 0.390, oos_pf: 0.441, mdd_pct: 100,
                   slippage_stress: { "10.0bps": 0.3 }, target_hit_ratio: 0.2,
                   stop_first_ratio: 0.1, time_exit_ratio: 0.7, cost_verdict: "NO_GROSS_EDGE",
                   verdict: "REJECT" },
      },
    },
    best_exit_per_entry: {
      VWAP_DEVIATION_REVERT: { exit: "NO_STOP_TIME_10", net_pf: 0.392, oos_pf: 0.5, verdict: "REJECT" },
      GAP_FADE_MEAN_REVERSION: { exit: "TIME_15", net_pf: 0.597, oos_pf: 0.385, verdict: "REJECT" },
    },
    survivors: [], fragile: [],
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
  };
}

const _api = (r) => ({ meanReversionExitLatest: vi.fn(async () => r) });

describe("<MeanReversionExitResearchCard>", () => {
  it("loading 초기 상태", () => {
    render(<MeanReversionExitResearchCard apiClient={{ meanReversionExitLatest: () => new Promise(() => {}) }} />);
    expect(screen.getByTestId("mre-loading")).toBeTruthy();
  });

  it("complete: best exit/verdict + research_only", async () => {
    render(<MeanReversionExitResearchCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mre-verdict");
    expect(screen.getByTestId("mre-verdict").textContent).toContain("EXIT_REDESIGN_REJECTED");
    expect(screen.getByTestId("mre-best").textContent).toContain("VWAP_DEVIATION_REVERT");
    expect(screen.getByTestId("mre-survivors").textContent).toContain("없음");
    expect(screen.getByTestId("mre-auto").textContent).toContain("true");   // research_only
    expect(screen.getByTestId("mre-auto").textContent).toContain("false");  // auto_apply
  });

  it("notready (no data)", async () => {
    render(<MeanReversionExitResearchCard apiClient={_api({ available: false, verdict: "BACKTEST_INFRA_INCOMPLETE" })} />);
    expect(await screen.findByTestId("mre-notready")).toBeTruthy();
    expect(screen.queryByTestId("mre-verdict")).toBeNull();
  });

  it("error/fail", async () => {
    render(<MeanReversionExitResearchCard apiClient={{ meanReversionExitLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("mre-error")).toBeTruthy();
  });

  it("적용/실전/주문/자동매매 버튼 0개", async () => {
    const { container } = render(<MeanReversionExitResearchCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mre-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<MeanReversionExitResearchCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mre-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("등록/자동 적용되지 않습니다");
  });
});
