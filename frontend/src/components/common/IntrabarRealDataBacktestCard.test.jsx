/**
 * IntrabarRealDataBacktestCard 테스트 — complete/notready/error 상태 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { IntrabarRealDataBacktestCard } from "./IntrabarRealDataBacktestCard";

afterEach(cleanup);

function _complete(overrides = {}) {
  return {
    available: true, verdict: "INTRABAR_REALDATA_READY", confidence_level: "MEDIUM",
    coverage: { symbol_count_1m: 5, replayable_trades: 120 },
    execution_5m_vs_1m: {
      five_minute: { profit_factor: 1.4, return_pct: 3.2, mdd_pct: 8 },
      intrabar_1m: { profit_factor: 1.1, return_pct: 1.5, mdd_pct: 9 },
    },
    ranking: { earliest_first_selected: ["000001"], composite_selected: ["005930"],
               ranking_differs: true, selected_avg_score: 67, rejected_avg_score: 40 },
    cost_slippage_stress: [{ slippage_bps: 5, profit_factor: 1.1 },
                           { slippage_bps: 15, profit_factor: 0.8 }],
    is_live_authorization: false, no_profit_guarantee: true,
    disclaimer: "이 결과는 연구용 백테스트이며 실전매매 권고가 아닙니다.",
    ...overrides,
  };
}

function _notready() {
  return { available: false, verdict: "REALDATA_BACKTEST_NOT_READY",
           confidence_level: "LOW", coverage: { symbol_count_1m: 0 },
           is_live_authorization: false, no_profit_guarantee: true,
           disclaimer: "연구용 백테스트이며 실전매매 권고가 아닙니다." };
}

const _api = (r) => ({ intrabarRealDataBacktestLatest: vi.fn(async () => r) });

describe("<IntrabarRealDataBacktestCard>", () => {
  it("complete: verdict + 5m vs 1m + ranking 표시", async () => {
    render(<IntrabarRealDataBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ird-verdict");
    expect(screen.getByTestId("ird-exec").textContent).toContain("1.4");
    expect(screen.getByTestId("ird-ranking").textContent).toContain("005930");
    expect(screen.getByTestId("ird-live").textContent).toContain("false");
  });

  it("notready: NOT_READY 안내", async () => {
    render(<IntrabarRealDataBacktestCard apiClient={_api(_notready())} />);
    expect(await screen.findByTestId("ird-notready")).toBeTruthy();
    expect(screen.queryByTestId("ird-verdict")).toBeNull();
  });

  it("error", async () => {
    const api = { intrabarRealDataBacktestLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<IntrabarRealDataBacktestCard apiClient={api} />);
    expect(await screen.findByTestId("ird-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 버튼 0개", async () => {
    const { container } = render(<IntrabarRealDataBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ird-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input/textarea 0개 + 경고 문구", async () => {
    const { container } = render(<IntrabarRealDataBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("ird-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("연구용 백테스트이며 실전매매 권고가 아닙니다");
  });
});
