/**
 * RiskFilterValidationCard 테스트 — complete/notready/error + 주문버튼 0 + 자동적용 false.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RiskFilterValidationCard } from "./RiskFilterValidationCard";

afterEach(cleanup);

function _complete() {
  return {
    available: true, verdict: "RISK_FILTER_READY_FOR_PAPER_REHEARSAL_CANDIDATE",
    oos: {
      baseline: { profit_factor: 1.279, mdd_pct: 20.7, expectancy: 14.0 },
      risk_filter: { profit_factor: 1.372, mdd_pct: 16.8, expectancy: 20.2 },
    },
    rolling: [{ filter_ge_baseline: true }, { filter_ge_baseline: true }, { filter_ge_baseline: true }],
    filtered_out_analysis: { total_trades: 299, removed_count: 146, loss_concentrated_in_removed: true,
                             missed_winners_count: 53, avoided_losers_count: 93 },
    recommendation: "OOS + rolling + stress 모두 baseline 이상. 단 runtime 자동 적용 금지.",
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
    disclaimer: "연구용 백테스트이며 실전매매 권고가 아닙니다.",
  };
}

const _api = (r) => ({ riskFilterValidationLatest: vi.fn(async () => r) });

describe("<RiskFilterValidationCard>", () => {
  it("complete: verdict + OOS + 자동적용 false", async () => {
    render(<RiskFilterValidationCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rfv-verdict");
    expect(screen.getByTestId("rfv-verdict").textContent).toContain("PAPER_REHEARSAL_CANDIDATE");
    expect(screen.getByTestId("rfv-oos").textContent).toContain("1.372");
    expect(screen.getByTestId("rfv-filtered").textContent).toContain("true");
    expect(screen.getByTestId("rfv-auto").textContent).toContain("false");
  });

  it("notready", async () => {
    render(<RiskFilterValidationCard apiClient={_api({ available: false, verdict: "RISK_FILTER_REJECTED" })} />);
    expect(await screen.findByTestId("rfv-notready")).toBeTruthy();
    expect(screen.queryByTestId("rfv-verdict")).toBeNull();
  });

  it("error", async () => {
    render(<RiskFilterValidationCard apiClient={{ riskFilterValidationLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("rfv-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 버튼 0개", async () => {
    const { container } = render(<RiskFilterValidationCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rfv-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<RiskFilterValidationCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rfv-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("자동 적용되지 않습니다");
  });
});
