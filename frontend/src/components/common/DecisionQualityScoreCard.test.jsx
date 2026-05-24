/**
 * #51 / 6-06 — DecisionQualityScoreCard 단위 테스트.
 *
 * lock: quality_score/grade/breakdown/penalties 표시 + quality 낮으면 HOLD 문구 +
 * 자동 적용 안 됨 문구 + 주문/실전/승인 버튼 0개 + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { DecisionQualityScoreCard } from "./DecisionQualityScoreCard";

afterEach(cleanup);

const _Q_OK = {
  enhanced_quality_score: 82, quality_grade: "B", min_quality: 60,
  breakdown: { signal_consistency: 85, data_reliability: 70, risk: 100, regime_fit: 90, exit_plan: 90 },
  penalties: { feedback_penalty: 0 }, should_hold: false, reason_code: null,
  final_action_input: "BUY", is_order_signal: false, is_live_authorization: false,
  contains_secret: false,
};

const _Q_LOW = {
  enhanced_quality_score: 42, quality_grade: "F", min_quality: 60,
  breakdown: { signal_consistency: 28, data_reliability: 40, risk: 55, regime_fit: 30, exit_plan: 20 },
  penalties: { feedback_penalty: 10 }, should_hold: true, reason_code: "QUALITY_SCORE_LOW_HOLD",
  final_action_input: "BUY", is_order_signal: false, is_live_authorization: false,
  contains_secret: false,
};

describe("<DecisionQualityScoreCard>", () => {
  it("quality_score + grade 표시", async () => {
    render(<DecisionQualityScoreCard quality={_Q_OK} />);
    const t = (await screen.findByTestId("quality-grade")).textContent;
    expect(t).toContain("82");
    expect(t).toContain("B");
  });

  it("breakdown 표시", async () => {
    render(<DecisionQualityScoreCard quality={_Q_OK} />);
    expect((await screen.findByTestId("quality-breakdown-signal_consistency")).textContent).toContain("85");
    expect(screen.getByTestId("quality-breakdown-data_reliability").textContent).toContain("70");
    expect(screen.getByTestId("quality-breakdown-risk").textContent).toContain("100");
    expect(screen.getByTestId("quality-breakdown-regime_fit").textContent).toContain("90");
    expect(screen.getByTestId("quality-breakdown-exit_plan").textContent).toContain("90");
  });

  it("penalties 표시", async () => {
    render(<DecisionQualityScoreCard quality={_Q_LOW} />);
    expect((await screen.findByTestId("quality-penalties")).textContent).toContain("10");
  });

  it("quality 낮으면 HOLD 문구 표시", async () => {
    render(<DecisionQualityScoreCard quality={_Q_LOW} />);
    const t = (await screen.findByTestId("quality-hold")).textContent;
    expect(t).toContain("BUY 보류");
    expect(t).toContain("QUALITY_SCORE_LOW_HOLD");
  });

  it("강등 없음 안내 (정상)", async () => {
    render(<DecisionQualityScoreCard quality={_Q_OK} />);
    expect((await screen.findByTestId("quality-ok")).textContent).toContain("강등 없음");
  });

  it("자동 적용 안 됨/주문 아님 문구", async () => {
    render(<DecisionQualityScoreCard quality={_Q_OK} />);
    const intro = (await screen.findByTestId("quality-intro")).textContent;
    expect(intro).toContain("자동 적용되지 않");
    expect(intro).toContain("주문 버튼이 아니며");
    const footer = screen.getByTestId("quality-footer").textContent;
    expect(footer).toContain("실전 전환을 허가하지 않습니다");
  });

  it("주문/실전/승인 버튼 0개, 입력 form 0개", async () => {
    const { container } = render(<DecisionQualityScoreCard quality={_Q_OK} />);
    await screen.findByTestId("quality-score");
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
    for (const f of ["지금 매수", "지금 매도", "Place Order", "실거래 시작", "승인하기"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<DecisionQualityScoreCard quality={_Q_OK} />);
    await screen.findByTestId("quality-score");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("apiClient 로 최신 episode 기반 fetch (quality prop 없음)", async () => {
    const api = {
      agentDecisionEpisodes: vi.fn(async () => ({ episodes: [{ council: { final_action: "BUY" } }] })),
      agentDecisionQuality: vi.fn(async () => _Q_OK),
    };
    render(<DecisionQualityScoreCard apiClient={api} />);
    expect((await screen.findByTestId("quality-grade")).textContent).toContain("82");
    expect(api.agentDecisionQuality).toHaveBeenCalled();
  });
});
