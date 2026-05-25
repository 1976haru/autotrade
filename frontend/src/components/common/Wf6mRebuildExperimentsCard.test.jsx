/**
 * Wf6mRebuildExperimentsCard 테스트 — 4상태 + verdict + 주문버튼 0 + 경고문구.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Wf6mRebuildExperimentsCard } from "./Wf6mRebuildExperimentsCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    final_verdict: "WATCHLIST_ONLY",
    baseline: { _label: "BASELINE", total_return_pct: -18.1, profit_factor: 0.85,
                max_drawdown_pct: 30.1, verdict: "STILL_NOT_RECOMMENDED" },
    best_config: { _label: "UNI_go_tune_top10", total_return_pct: 13.8, profit_factor: 1.25,
                   max_drawdown_pct: 11.0, trade_count: 408, verdict: "WATCHLIST_ONLY",
                   low_confidence: false },
    most_improved_top10: [
      { _label: "UNI_go_tune_top10", total_return_pct: 13.8, profit_factor: 1.25,
        max_drawdown_pct: 11.0, verdict: "WATCHLIST_ONLY", low_confidence: false },
      { _label: "TIME_cutoff_1300", total_return_pct: 11.6, profit_factor: 1.12,
        max_drawdown_pct: 16.8, verdict: "WATCHLIST_ONLY", low_confidence: false },
    ],
    most_stable_top10: [
      { _label: "AGENT_POSITION_SIZER_ONLY", total_return_pct: 6.0, profit_factor: 1.07,
        max_drawdown_pct: 15.5 },
    ],
    unresolved_issues: ["OOS 양(+) 미확인 — 과최적화 위험 잔존"],
    next_steps: ["별도 PR 재현·재백테스트"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, contains_secret: false,
    no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { wf6mRebuildLatest: vi.fn(async () => report) };
}

describe("<Wf6mRebuildExperimentsCard>", () => {
  it("완료 상태: verdict + best + 개선/안정 조합", async () => {
    render(<Wf6mRebuildExperimentsCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrb-verdict");
    expect(screen.getByTestId("wf6mrb-final-verdict").textContent).toContain("WATCHLIST_ONLY");
    expect(screen.getByTestId("wf6mrb-best").textContent).toContain("UNI_go_tune_top10");
    expect(screen.getByTestId("wf6mrb-improved").textContent).toContain("TIME_cutoff_1300");
    expect(screen.getByTestId("wf6mrb-unresolved").textContent).toContain("과최적화");
  });

  it("데이터 없음 상태", async () => {
    render(<Wf6mRebuildExperimentsCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("wf6mrb-empty")).toBeTruthy();
    expect(screen.queryByTestId("wf6mrb-verdict")).toBeNull();
  });

  it("실패 상태", async () => {
    const api = { wf6mRebuildLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Wf6mRebuildExperimentsCard apiClient={api} />);
    expect(await screen.findByTestId("wf6mrb-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Wf6mRebuildExperimentsCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrb-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Wf6mRebuildExperimentsCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrb-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("LOW_CONFIDENCE 플래그 표시", async () => {
    render(<Wf6mRebuildExperimentsCard apiClient={_api(_report({
      best_config: { _label: "BEST_COMBO", total_return_pct: 1.1, profit_factor: 1.58,
                     max_drawdown_pct: 0.9, trade_count: 14, verdict: "WATCHLIST_ONLY",
                     low_confidence: true } }))} />);
    await screen.findByTestId("wf6mrb-verdict");
    expect(screen.getByTestId("wf6mrb-best").textContent).toContain("LOW_CONFIDENCE");
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<Wf6mRebuildExperimentsCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrb-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });
});
