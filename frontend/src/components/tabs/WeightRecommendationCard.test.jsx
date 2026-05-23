import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { WeightRecommendationCard } from "./WeightRecommendationCard";

const _SAMPLE = {
  recommendation: {
    recommendation_id: "wr-abc123",
    created_at: "2026-05-23T00:00:00+00:00",
    status: "RECOMMENDATION_ONLY",
    lookback_count: 100,
    risk_profile: "BALANCED",
    market_regime: "ALL",
    current_weights: { MOMENTUM: 30, VWAP: 25, ORB: 25, GAP: 20 },
    recommended_weights: { MOMENTUM: 35, VWAP: 27, ORB: 25, GAP: 13 },
    deltas: { MOMENTUM: 5, VWAP: 2, ORB: 0, GAP: -7 },
    strategy_actions: [
      { strategy: "ORB", action: "NEEDS_MORE_DATA", score: 0.0, current_weight: 25,
        recommended_weight: 25, delta: 0, sample_count: 3,
        reasons: ["평가 표본 3건 < 5건 — 변경 보류"] },
      { strategy: "MOMENTUM", action: "INCREASE", score: 0.73, current_weight: 30,
        recommended_weight: 35, delta: 5, sample_count: 12,
        reasons: ["profit factor 2.2 우수", "승률 75.0% 양호"] },
      { strategy: "GAP", action: "DECREASE", score: -0.9, current_weight: 20,
        recommended_weight: 13, delta: -7, sample_count: 10,
        reasons: ["profit factor 0.4 부진", "MDD 45.0% 높음 — 감점"] },
      { strategy: "VWAP", action: "INCREASE", score: 0.53, current_weight: 25,
        recommended_weight: 27, delta: 2, sample_count: 8,
        reasons: ["profit factor 1.8 우수"] },
    ],
    expected_effect: "비중 상향 후보: MOMENTUM, VWAP; 비중 하향 후보: GAP — 기대 profit factor 개선 가능(추정).",
    warning: null,
    requires_operator_approval: true,
    auto_apply_allowed: false,
    contains_secret: false,
    uses_account_balance: false,
    is_order_signal: false,
    is_live_authorization: false,
  },
  summary: {
    status: "RECOMMENDATION_ONLY", requires_operator_approval: true,
    auto_apply_allowed: false, is_order_signal: false, is_live_authorization: false,
    contains_secret: false,
  },
};

function _api(payload = _SAMPLE) {
  return { agentWeightRecommendation: vi.fn(async () => payload) };
}

describe("<WeightRecommendationCard>", () => {
  afterEach(cleanup);

  it("카드 + 자동 적용 안 됨 / 승인 전 변경 없음 / 주문 신호 아님 문구", async () => {
    render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("weight-recommendation-card")).toBeTruthy());
    expect(screen.getByTestId("wr-badges").textContent).toMatch(/자동 적용 안 됨/);
    const d = screen.getByTestId("wr-disclaimer").textContent;
    expect(d).toMatch(/자동 적용되지 않습니다/);
    expect(d).toMatch(/운영자 승인 전/);
    expect(d).toMatch(/주문 신호가 아니며/);
    expect(d).toMatch(/실제 계좌정보를 사용하지 않습니다/);
  });

  it("현재/추천 가중치 + 변화량 표시", async () => {
    render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("wr-strategy-MOMENTUM")).toBeTruthy());
    const t = screen.getByTestId("wr-strategy-MOMENTUM").textContent;
    expect(t).toMatch(/30/);    // 현재
    expect(t).toMatch(/35/);    // 추천
    expect(t).toMatch(/\+5/);   // 변화량
  });

  it("Momentum INCREASE / Gap DECREASE 액션 표시", async () => {
    render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("wr-strategy-MOMENTUM")).toBeTruthy());
    expect(screen.getByTestId("wr-strategy-MOMENTUM").textContent).toMatch(/상향/);
    expect(screen.getByTestId("wr-strategy-GAP").textContent).toMatch(/하향/);
  });

  it("추천 사유 + 기대효과 표시", async () => {
    render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("wr-reason-MOMENTUM")).toBeTruthy());
    expect(screen.getByTestId("wr-reason-MOMENTUM").textContent).toMatch(/profit factor 2\.2 우수/);
    expect(screen.getByTestId("wr-expected-effect").textContent).toMatch(/기대효과/);
  });

  it("ORB 표본 부족 표시", async () => {
    render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("wr-strategy-ORB")).toBeTruthy());
    expect(screen.getByTestId("wr-strategy-ORB").textContent).toMatch(/데이터 부족/);
  });

  it("표본 부족(NEEDS_MORE_DATA) 안내", async () => {
    const payload = { ..._SAMPLE,
      recommendation: { ..._SAMPLE.recommendation, status: "NEEDS_MORE_DATA" } };
    render(<WeightRecommendationCard apiClient={_api(payload)} />);
    await waitFor(() => expect(screen.getByTestId("wr-insufficient")).toBeTruthy());
    expect(screen.getByTestId("wr-insufficient").textContent).toMatch(/표본이 부족/);
  });

  it("warning 표시 (Agent 열세)", async () => {
    const payload = { ..._SAMPLE,
      recommendation: { ..._SAMPLE.recommendation,
        warning: "Agent Council 이 MOMENTUM 단독보다 낮습니다." } };
    render(<WeightRecommendationCard apiClient={_api(payload)} />);
    await waitFor(() => expect(screen.getByTestId("wr-warning")).toBeTruthy());
    expect(screen.getByTestId("wr-warning").textContent).toMatch(/MOMENTUM 단독보다 낮습니다/);
  });

  it("적용/자동 적용/실전/매수/매도 버튼 없음 + secret 표시 없음", async () => {
    const { container } = render(<WeightRecommendationCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("weight-recommendation-card")).toBeTruthy());
    // 버튼 0개 = 적용/자동 적용/실전/매수/매도 버튼 전부 없음 (가장 강한 보장).
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    // 실거래/매수/매도/계좌/secret 문구 자체가 노출되지 않음.
    for (const b of ["지금 매수", "지금 매도", "실거래 시작", "Place Order",
                     "LIVE 활성화", "app_secret", "account_no", "api_key"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
