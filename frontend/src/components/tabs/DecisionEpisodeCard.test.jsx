/**
 * P-21: DecisionEpisodeCard 단위 테스트.
 *
 * invariant:
 *  - 최근 episode 표시 (symbol/action/strategies/confidence/quality/reason_code/
 *    broker_order_no 여부/outcome).
 *  - empty state.
 *  - "주문 신호 아님" 안내 + 버튼/입력 0개.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { DecisionEpisodeCard } from "./DecisionEpisodeCard";

const _SAMPLE = {
  episodes: [
    {
      episode_id: "ep-001", symbol: "005930", final_action: "BUY",
      confidence: 72, quality_score: 82, reason_code: "KIS_PAPER_SUBMITTED",
      selected_strategies: ["MOMENTUM", "VWAP"], broker_order_no: "PAPER-1",
      outcome: { label: "WIN" }, is_live_authorization: false,
    },
    {
      episode_id: "ep-002", symbol: "000660", final_action: "HOLD",
      confidence: 40, quality_score: 30, reason_code: "NO_STRATEGY_SIGNAL",
      selected_strategies: [], broker_order_no: null, outcome: null,
      is_live_authorization: false,
    },
  ],
  count: 2,
  summary: { total: 2, submitted_count: 1, is_live_authorization: false },
  is_live_authorization: false,
};

function _api(payload = _SAMPLE) {
  return { agentDecisionEpisodes: vi.fn(async () => payload) };
}

describe("<DecisionEpisodeCard>", () => {
  afterEach(cleanup);

  it("카드 + 학습용 배지 렌더", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("decision-episode-card")).toBeTruthy());
    expect(screen.getByTestId("episode-badge-learning").textContent).toMatch(/주문 신호 아님/);
  });

  it("episode 행 표시 (symbol/action/strategies/order/outcome)", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-001")).toBeTruthy());
    const row = screen.getByTestId("episode-row-ep-001");
    expect(row.textContent).toMatch(/005930/);
    expect(row.textContent).toMatch(/BUY/);
    expect(row.textContent).toMatch(/MOMENTUM, VWAP/);
    expect(screen.getByTestId("episode-order-ep-001").textContent).toMatch(/있음\(PAPER-1\)/);
    expect(row.textContent).toMatch(/WIN/);
  });

  it("주문 없는 HOLD episode → 없음/미정 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-002")).toBeTruthy());
    expect(screen.getByTestId("episode-order-ep-002").textContent).toMatch(/없음/);
    expect(screen.getByTestId("episode-row-ep-002").textContent).toMatch(/미정/);
  });

  it("summary 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-summary").textContent)
      .toMatch(/총 2건 · 제출 1건/));
  });

  it("empty state", async () => {
    render(<DecisionEpisodeCard apiClient={_api({ episodes: [], count: 0, summary: { total: 0 } })} />);
    await waitFor(() => expect(screen.getByTestId("episode-empty").textContent)
      .toMatch(/아직 기록된 Decision Episode가 없습니다/));
  });

  it("API 실패 시 에러 표시", async () => {
    const api = { agentDecisionEpisodes: vi.fn(async () => { throw new Error("down"); }) };
    render(<DecisionEpisodeCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("episode-error").textContent).toMatch(/down/));
  });

  it("버튼/입력 0개 + 실거래 라벨 0개 (invariant)", async () => {
    const { container } = render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("decision-episode-card")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const banned of ["Place Order", "지금 매수", "실거래 시작", "ENABLE_LIVE_TRADING"]) {
      expect(container.textContent).not.toContain(banned);
    }
  });

  it("footer 학습용 안내", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-footer").textContent)
      .toMatch(/주문 신호가 아니며 실거래 권한이 아닙니다/));
  });
});
