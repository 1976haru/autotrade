/**
 * Wf6mRootCauseCard 테스트 — 4상태(loading/complete/empty/error) + 주문버튼 0 + 경고문구.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { Wf6mRootCauseCard } from "./Wf6mRootCauseCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    baseline: { total_return_pct: -18.1, final_equity: 8190000, profit_factor: 0.85,
                max_drawdown_pct: 30.1, trade_count: 676, avg_hold_minutes: 258 },
    loss_causes_top: [
      { rank: 1, cause: "신호 과다 vs 5슬롯", evidence: "초과 시점 200건", fix: "랭킹 도입" },
      { rank: 2, cause: "EXCLUDE 손실", evidence: "+26%p", fix: "universe 축소" },
    ],
    cost_sensitivity: { baseline_cost: { total_return_pct: -18.1 },
                        zero_all_cost: { total_return_pct: 19.2 }, roundtrip_cost_pct: 3.1 },
    agent_damage: { council_baseline_return_pct: -18.1, agent_off_return_pct: -33.8,
                    veto_helped: true },
    symbol_group: { exclude_symbols: ["009150", "128940"],
                    exclude_removed_result: { total_return_pct: 8.0 },
                    improvement_pct_points: 26.1 },
    conclusions: ["개별 전략은 양(+)이나 결합이 음(-)", "Agent 진입권 회수 권장"],
    do_not_auto_apply: true, is_live_authorization: false, broker_order_sent: false,
    order_created: false, contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { wf6mRootCauseLatest: vi.fn(async () => report) };
}

describe("<Wf6mRootCauseCard>", () => {
  it("완료 상태: baseline + 손실원인 + 결론 표시", async () => {
    render(<Wf6mRootCauseCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrc-baseline");
    expect(screen.getByTestId("wf6mrc-baseline").textContent).toContain("-18.10%");
    expect(screen.getByTestId("wf6mrc-causes").textContent).toContain("5슬롯");
    expect(screen.getByTestId("wf6mrc-conclusions").textContent).toContain("Agent");
    expect(screen.getByTestId("wf6mrc-cost").textContent).toContain("19.20%");
    expect(screen.getByTestId("wf6mrc-exclude").textContent).toContain("26.10");
  });

  it("데이터 없음 상태", async () => {
    render(<Wf6mRootCauseCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("wf6mrc-empty")).toBeTruthy();
    expect(screen.queryByTestId("wf6mrc-baseline")).toBeNull();
  });

  it("실패 상태", async () => {
    const api = { wf6mRootCauseLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Wf6mRootCauseCard apiClient={api} />);
    expect(await screen.findByTestId("wf6mrc-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Wf6mRootCauseCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrc-baseline");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Wf6mRootCauseCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrc-baseline");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<Wf6mRootCauseCard apiClient={_api()} />);
    await screen.findByTestId("wf6mrc-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });
});
