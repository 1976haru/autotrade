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
      market_summary: {
        price: 75000, market_regime: "TREND_UP", data_status: "OK",
        vwap: 75200, rsi: 58.2, gap_pct: 0.68, price_age_seconds: 5,
      },
      votes: [
        { strategy: "ORB", signal: "HOLD", score: 40, confidence: 0.4 },
        { strategy: "MOMENTUM", signal: "BUY", score: 82, confidence: 0.76 },
        { strategy: "GAP", signal: "HOLD", score: 20, confidence: 0.3 },
        { strategy: "VWAP", signal: "BUY", score: 76, confidence: 0.7 },
      ],
      council: { buy_score: 45.5, sell_score: 0, hold_score: 14 },
      order_quality_summary: {
        broker_order_no: "PAPER-1", order_status: "FILLED", fill_status: "FILLED",
        latency_ms: 333, slippage_bps: 13.33, partial_fill: false,
      },
    },
    {
      episode_id: "ep-002", symbol: "000660", final_action: "HOLD",
      confidence: 40, quality_score: 30, reason_code: "NO_STRATEGY_SIGNAL",
      selected_strategies: [], broker_order_no: null, outcome: null,
      is_live_authorization: false,
      market_summary: { data_status: "NO_MARKET_DATA" },
    },
    {
      episode_id: "ep-003", symbol: "035720", final_action: "BUY",
      confidence: 60, quality_score: 65, reason_code: "PRICE_STALE",
      selected_strategies: ["ORB"], broker_order_no: null, outcome: null,
      is_live_authorization: false,
      market_summary: { data_status: "PRICE_STALE", reason_code: "PRICE_STALE" },
    },
  ],
  count: 3,
  summary: {
    total: 3, submitted_count: 1, is_live_authorization: false,
    by_data_status: { OK: 1, NO_MARKET_DATA: 1, PRICE_STALE: 1 },
  },
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
      .toMatch(/총 3건 · 제출 1건/));
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

  // ── P-22: market snapshot 요약 표시 ──

  it("P-22: 현재가/regime/VWAP/RSI/Gap 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-market-ep-001")).toBeTruthy());
    const m = screen.getByTestId("episode-market-ep-001").textContent;
    expect(m).toMatch(/현재가 75,000원/);
    expect(m).toMatch(/TREND_UP/);
    expect(m).toMatch(/VWAP 75,200/);
    expect(m).toMatch(/RSI 58\.2/);
    expect(m).toMatch(/Gap \+0\.68%/);
    expect(m).toMatch(/age 5s/);
  });

  it("P-22: NO_MARKET_DATA 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-market-ep-002")).toBeTruthy());
    expect(screen.getByTestId("episode-market-ep-002").textContent)
      .toMatch(/시장 데이터 없음/);
  });

  it("P-22: PRICE_STALE 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-market-ep-003")).toBeTruthy());
    expect(screen.getByTestId("episode-market-ep-003").textContent)
      .toMatch(/현재가가 오래되어 PRICE_STALE/);
  });

  // ── P-23: 4전략 vote 표시 ──

  it("P-23: ORB/Momentum/Gap/VWAP 4전략 signal+score 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-votes-ep-001")).toBeTruthy());
    expect(screen.getByTestId("episode-vote-ep-001-ORB").textContent).toMatch(/ORB: HOLD\/40/);
    expect(screen.getByTestId("episode-vote-ep-001-MOMENTUM").textContent).toMatch(/MOMENTUM: BUY\/82/);
    expect(screen.getByTestId("episode-vote-ep-001-GAP").textContent).toMatch(/GAP: HOLD\/20/);
    expect(screen.getByTestId("episode-vote-ep-001-VWAP").textContent).toMatch(/VWAP: BUY\/76/);
  });

  it("P-23: buy/sell/hold score 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-scores-ep-001")).toBeTruthy());
    expect(screen.getByTestId("episode-scores-ep-001").textContent)
      .toMatch(/buy 45\.5.*sell 0.*hold 14/);
  });

  it("P-23: final action + selected_strategies 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-001")).toBeTruthy());
    const row = screen.getByTestId("episode-row-ep-001").textContent;
    expect(row).toMatch(/BUY/);
    expect(row).toMatch(/선택: MOMENTUM, VWAP/);
  });

  // ── P-24: 주문·체결 품질 표시 ──

  it("P-24: 주문번호/상태/체결/지연/슬리피지 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-quality-ep-001")).toBeTruthy());
    const q = screen.getByTestId("episode-quality-ep-001").textContent;
    expect(q).toMatch(/PAPER-1/);
    expect(q).toMatch(/FILLED/);
    expect(q).toMatch(/지연 333ms/);
    expect(q).toMatch(/슬리피지 13\.33bps/);
  });

  it("P-24: order_quality 없는 episode 는 품질 줄 미표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-002")).toBeTruthy());
    expect(screen.queryByTestId("episode-quality-ep-002")).toBeNull();
  });
});
