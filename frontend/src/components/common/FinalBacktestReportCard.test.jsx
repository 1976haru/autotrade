/**
 * FinalBacktestReportCard 테스트 — complete/notready/error + 주문버튼 0 + 자동적용 false.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { FinalBacktestReportCard } from "./FinalBacktestReportCard";

afterEach(cleanup);

function _complete() {
  const s = (pf, mdd, grade) => ({ intrabar_1m: { profit_factor: pf, mdd_pct: mdd }, grade });
  return {
    available: true, verdict: "STRATEGY_EDGE_NOT_FOUND",
    per_strategy: {
      ORB: s(0.20, 166, "WEAK_OR_EXCLUDE"), MOMENTUM: s(0.19, 316, "WEAK_OR_EXCLUDE"),
      GAP: s(0.11, 128, "WEAK_OR_EXCLUDE"), VWAP: s(0.14, 434, "WEAK_OR_EXCLUDE"),
    },
    council: {
      basic: { profit_factor: 0.642, mdd_pct: 60.5 }, with_risk_filter: { profit_factor: 0.657 },
      best_single_strategy: "ORB", best_single_pf: 0.20, avg_single_pf: 0.16,
      council_vs_best_single_pf_delta: 0.441, council_vs_avg_single_pf_delta: 0.483,
      agent_helped_count: 138, agent_hurt_count: 114,
    },
    conclusion: ["비용 후 어떤 단독 전략도 PF≥1.0 미달, Council 도 미달."],
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true, disclaimer: "연구용 백테스트이며 실전매매 권고가 아닙니다.",
  };
}

const _api = (r) => ({ finalBacktestReportLatest: vi.fn(async () => r) });

describe("<FinalBacktestReportCard>", () => {
  it("complete: 전략/council/verdict 표시 + 자동적용 false", async () => {
    render(<FinalBacktestReportCard apiClient={_api(_complete())} />);
    await screen.findByTestId("fbr-verdict");
    expect(screen.getByTestId("fbr-verdict").textContent).toContain("STRATEGY_EDGE_NOT_FOUND");
    expect(screen.getByTestId("fbr-strategies").textContent).toContain("ORB");
    expect(screen.getByTestId("fbr-council").textContent).toContain("0.642");
    expect(screen.getByTestId("fbr-auto").textContent).toContain("false");
  });

  it("notready", async () => {
    render(<FinalBacktestReportCard apiClient={_api({ available: false, verdict: "BACKTEST_INFRA_INCOMPLETE" })} />);
    expect(await screen.findByTestId("fbr-notready")).toBeTruthy();
    expect(screen.queryByTestId("fbr-verdict")).toBeNull();
  });

  it("error", async () => {
    render(<FinalBacktestReportCard apiClient={{ finalBacktestReportLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("fbr-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 버튼 0개", async () => {
    const { container } = render(<FinalBacktestReportCard apiClient={_api(_complete())} />);
    await screen.findByTestId("fbr-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<FinalBacktestReportCard apiClient={_api(_complete())} />);
    await screen.findByTestId("fbr-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("자동 적용되지 않습니다");
  });
});
