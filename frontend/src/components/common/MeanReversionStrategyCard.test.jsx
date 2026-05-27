/**
 * MeanReversionStrategyCard 테스트 — complete/notready/error + 주문/적용 버튼 0 + research_only.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { MeanReversionStrategyCard } from "./MeanReversionStrategyCard";

afterEach(cleanup);

function _complete() {
  const c = (pf, oos, s10, cv, vd) => ({
    trade_count: 100, net_pf: pf, oos: { oos_pf: oos }, mdd_pct: 100,
    slippage_stress: { "10.0bps": s10 }, cost_verdict: cv, verdict: vd,
  });
  return {
    available: true, verdict: "MEAN_REVERSION_EDGE_NOT_FOUND", is_research_only: true,
    conclusion: ["평균회귀 후보 6종 모두 비용 전에도 PF<1 — 엣지 없음."],
    hypothesis_analysis: {
      trend_follower_forward_returns: { n: 767, mean_h30_bps: -1.4 },
      interpretation: ["추세추종 BUY 후 30분 forward ≈0, 사실상 noise → 반전 가설 약함."],
    },
    candidate_results: {
      VWAP_DEVIATION_REVERT: c(0.254, 0.226, 0.192, "NO_GROSS_EDGE", "REJECT"),
      GAP_FADE_MEAN_REVERSION: c(0.392, 0.701, 0.319, "NO_GROSS_EDGE", "REJECT"),
    },
    survivors: [], watch: [], low_confidence: [],
    reject: ["VWAP_DEVIATION_REVERT", "GAP_FADE_MEAN_REVERSION"],
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
  };
}

const _api = (r) => ({ meanReversionStrategyLatest: vi.fn(async () => r) });

describe("<MeanReversionStrategyCard>", () => {
  it("complete: 가설/후보/verdict + research_only", async () => {
    render(<MeanReversionStrategyCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mrs-verdict");
    expect(screen.getByTestId("mrs-verdict").textContent).toContain("MEAN_REVERSION_EDGE_NOT_FOUND");
    expect(screen.getByTestId("mrs-hypothesis").textContent).toContain("-1.4");
    expect(screen.getByTestId("mrs-candidates").textContent).toContain("VWAP_DEVIATION_REVERT");
    expect(screen.getByTestId("mrs-survivors").textContent).toContain("없음");
    expect(screen.getByTestId("mrs-auto").textContent).toContain("true");   // research_only true
    expect(screen.getByTestId("mrs-auto").textContent).toContain("false");  // auto_apply false
  });

  it("notready", async () => {
    render(<MeanReversionStrategyCard apiClient={_api({ available: false, verdict: "BACKTEST_INFRA_INCOMPLETE" })} />);
    expect(await screen.findByTestId("mrs-notready")).toBeTruthy();
    expect(screen.queryByTestId("mrs-verdict")).toBeNull();
  });

  it("error", async () => {
    render(<MeanReversionStrategyCard apiClient={{ meanReversionStrategyLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("mrs-error")).toBeTruthy();
  });

  it("적용/실전/주문/자동매매 버튼 0개", async () => {
    const { container } = render(<MeanReversionStrategyCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mrs-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<MeanReversionStrategyCard apiClient={_api(_complete())} />);
    await screen.findByTestId("mrs-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("등록/자동 적용되지 않습니다");
  });
});
