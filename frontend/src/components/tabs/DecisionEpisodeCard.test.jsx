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
      outcome_summary: {
        status: "COMPLETE", label: "PROFITABLE", return_5m: 0.32, return_30m: 1.25,
        return_close: 0.95, max_favorable_excursion: 1.8, max_adverse_excursion: -0.4,
      },
      // P-27: 거래 복기.
      review_summary: {
        review_status: "COMPLETE", grade: "GOOD", primary_tag: "GOOD_DECISION",
        summary: "MOMENTUM+VWAP 진입이 수익(+0.95%)으로 연결됨 — 좋은 판단.",
      },
      review: {
        review_status: "COMPLETE", grade: "GOOD", primary_tag: "GOOD_DECISION",
        improvement_suggestions: ["MFE 대비 종가 수익률이 낮음 — 익절 타이밍 보완 필요"],
      },
    },
    {
      episode_id: "ep-002", symbol: "000660", final_action: "HOLD",
      confidence: 40, quality_score: 30, reason_code: "NO_STRATEGY_SIGNAL",
      selected_strategies: [], broker_order_no: null, outcome: null,
      is_live_authorization: false,
      market_summary: { data_status: "NO_MARKET_DATA" },
      outcome_summary: { status: "PENDING", label: "OUTCOME_PENDING" },
    },
    {
      episode_id: "ep-003", symbol: "035720", final_action: "BUY",
      confidence: 60, quality_score: 65, reason_code: "PRICE_STALE",
      selected_strategies: ["ORB"], broker_order_no: null, outcome: null,
      is_live_authorization: false,
      market_summary: { data_status: "PRICE_STALE", reason_code: "PRICE_STALE" },
      outcome_summary: { status: "UNAVAILABLE", label: "OUTCOME_UNAVAILABLE" },
      // P-27: 성과 부족 → 복기 보류.
      review_summary: { review_status: "DATA_INSUFFICIENT", grade: "DATA_INSUFFICIENT" },
      // 2-08: RiskOfficer veto 적용 (위험 플래그 초과로 HOLD 강등).
      council: {
        risk_veto_result: {
          veto_applied: true, pre_veto_action: "BUY", final_action: "HOLD",
          risk_flag_count: 2, max_risk_flags: 1, risk_profile: "BALANCED",
          reason_code: "RISK_OFFICER_VETO",
        },
      },
    },
    {
      // P-26: SELL episode — 매도 사유 표시.
      episode_id: "ep-004", symbol: "068270", final_action: "SELL",
      confidence: 64, quality_score: 70, reason_code: "STOP_LOSS",
      selected_strategies: ["VWAP"], broker_order_no: "PAPER-9", outcome: null,
      is_live_authorization: false,
      market_summary: { data_status: "OK", price: 41000 },
      outcome_summary: { status: "PENDING", label: "OUTCOME_PENDING" },
      sell_reason: {
        reason_code: "STOP_LOSS", category: "RISK_EXIT",
        message: "손절 기준에 도달하여 매도 판단",
      },
      sell_reason_summary: {
        reason_code: "STOP_LOSS", category: "RISK_EXIT",
        message: "손절 기준에 도달하여 매도 판단",
      },
    },
  ],
  count: 4,
  summary: {
    total: 4, submitted_count: 1, is_live_authorization: false,
    by_data_status: { OK: 2, NO_MARKET_DATA: 1, PRICE_STALE: 1 },
    by_sell_reason: { STOP_LOSS: 1 }, by_sell_category: { RISK_EXIT: 1 },
    by_review_grade: { GOOD: 1, DATA_INSUFFICIENT: 1 },
    by_review_tag: { GOOD_DECISION: 1 },
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
      .toMatch(/총 4건 · 제출 1건/));
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

  // ── P-25: 사후 성과 표시 ──

  it("P-25: COMPLETE 성과 — 라벨/5분/30분/종가/MFE/MAE 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-outcome-ep-001")).toBeTruthy());
    const o = screen.getByTestId("episode-outcome-ep-001").textContent;
    expect(o).toMatch(/COMPLETE/);
    expect(o).toMatch(/PROFITABLE/);
    expect(o).toMatch(/5분 \+0\.32%/);
    expect(o).toMatch(/30분 \+1\.25%/);
    expect(o).toMatch(/종가 \+0\.95%/);
    expect(o).toMatch(/MFE \+1\.8%/);
    expect(o).toMatch(/MAE -0\.4%/);
  });

  it("P-25: PENDING 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-outcome-ep-002")).toBeTruthy());
    expect(screen.getByTestId("episode-outcome-ep-002").textContent)
      .toMatch(/성과 라벨 대기 중/);
  });

  it("P-25: UNAVAILABLE 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-outcome-ep-003")).toBeTruthy());
    expect(screen.getByTestId("episode-outcome-ep-003").textContent)
      .toMatch(/시장 데이터 부족으로 성과 계산 불가/);
  });

  it("P-25: 성과 줄에 secret/실거래 버튼 없음", async () => {
    const { container } = render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-outcome-ep-001")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    for (const b of ["app_secret", "account_no", "Place Order", "실거래 시작"]) {
      expect(container.textContent).not.toContain(b);
    }
  });

  // ── P-26: 매도 사유 표시 ──

  it("P-26: SELL episode 에 매도 사유(STOP_LOSS) + 메시지 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-sell-reason-ep-004")).toBeTruthy());
    const t = screen.getByTestId("episode-sell-reason-ep-004").textContent;
    expect(t).toMatch(/매도 사유/);
    expect(t).toMatch(/STOP_LOSS/);
    expect(t).toMatch(/손절 기준에 도달/);
  });

  it("P-26: BUY/HOLD episode 에는 매도 사유 영역 미표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-001")).toBeTruthy());
    expect(screen.queryByTestId("episode-sell-reason-ep-001")).toBeNull();  // BUY
    expect(screen.queryByTestId("episode-sell-reason-ep-002")).toBeNull();  // HOLD
  });

  it("P-26: 매도 사유 줄에 secret/실거래/매수·매도 버튼 없음", async () => {
    const { container } = render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-sell-reason-ep-004")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    for (const b of ["app_secret", "account_no", "Place Order", "실거래 시작",
                     "지금 매도", "지금 매수"]) {
      expect(container.textContent).not.toContain(b);
    }
  });

  // ── P-27: 거래 복기 표시 ──

  it("P-27: COMPLETE 복기 — 등급/primary_tag/요약/개선 제안 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-review-ep-001")).toBeTruthy());
    const t = screen.getByTestId("episode-review-ep-001").textContent;
    expect(t).toMatch(/복기/);
    expect(t).toMatch(/GOOD/);
    expect(t).toMatch(/GOOD_DECISION/);
    expect(t).toMatch(/수익으로 연결됨|좋은 판단/);
    expect(screen.getByTestId("episode-review-suggest-ep-001").textContent)
      .toMatch(/개선 제안.*익절 타이밍 보완/);
  });

  it("P-27: DATA_INSUFFICIENT 복기 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-review-ep-003")).toBeTruthy());
    expect(screen.getByTestId("episode-review-ep-003").textContent)
      .toMatch(/성과 데이터 부족|DATA_INSUFFICIENT/);
  });

  it("P-27: review 없는 episode 는 복기 줄 미표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-002")).toBeTruthy());
    expect(screen.queryByTestId("episode-review-ep-002")).toBeNull();
  });

  it("P-27: 복기 줄에 secret/실거래/매수·매도 버튼 없음", async () => {
    const { container } = render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-review-ep-001")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    for (const b of ["app_secret", "account_no", "Place Order", "실거래 시작",
                     "지금 매도", "지금 매수"]) {
      expect(container.textContent).not.toContain(b);
    }
  });

  // ── 2-08: RiskOfficer veto 표시 ──

  it("2-08: veto 적용 episode 에 RiskOfficer veto 줄 표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-risk-veto-ep-003")).toBeTruthy());
    const t = screen.getByTestId("episode-risk-veto-ep-003").textContent;
    expect(t).toMatch(/RiskOfficer veto/);
    expect(t).toMatch(/위험 플래그 2개/);
    expect(t).toMatch(/허용 1개/);
    expect(t).toMatch(/BALANCED/);
    expect(t).toMatch(/HOLD 강등/);
  });

  it("2-08: veto 없는 episode 는 veto 줄 미표시", async () => {
    render(<DecisionEpisodeCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("episode-row-ep-001")).toBeTruthy());
    expect(screen.queryByTestId("episode-risk-veto-ep-001")).toBeNull();
  });
});
