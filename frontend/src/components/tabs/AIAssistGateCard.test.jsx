import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AIAssistGateCard } from "./AIAssistGateCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    aiAssistGateEvaluate: vi.fn(),
  },
}));


const _PASS_RESULT = {
  verdict: "PASS",
  failed_criteria: [],
  cautions: [],
  failure_reason_tags: {
    operator_rejected: 25,
    risk_limit: 20,
    data_stale: 18,
  },
  metrics: {
    proposal_count: 150,
    approved_proposals: 80,
    risk_rejection_rate: 0.2,
    operator_rejection_rate: 0.2,
    approved_loss_rate: 0.375,
    confidence_calibration: 0.75,
    ai_decision_audit_drift: 0,
    emergency_stops_in_period: 0,
  },
};


const _FAIL_RESULT = {
  verdict: "FAIL",
  failed_criteria: [
    "AI 제안 30건 < 100건 — 표본 부족.",
    "승인 제안 expectancy -50.00 ≤ 0.",
  ],
  cautions: [],
  failure_reason_tags: {
    operator_rejected: 20,
    data_stale: 10,
  },
  metrics: {
    proposal_count: 30,
    approved_proposals: 5,
    risk_rejection_rate: 0.6,
    operator_rejection_rate: 0.3,
    approved_loss_rate: 0.8,
    confidence_calibration: 0.3,
    ai_decision_audit_drift: 0,
    emergency_stops_in_period: 0,
  },
};


afterEach(cleanup);


describe("AIAssistGateCard", () => {
  it("PASS 스냅샷에서 '다음 검증 단계 가능' 배지 노출", () => {
    const { getByTestId } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const badge = getByTestId("ai-assist-verdict-PASS");
    expect(badge.textContent).toBe("다음 검증 단계 가능");
  });

  it("FAIL 스냅샷에서 미충족 기준 + 실패 사유 태그 노출", () => {
    const { getByTestId } = render(
      <AIAssistGateCard resultOverride={_FAIL_RESULT} />,
    );
    expect(getByTestId("ai-assist-verdict-FAIL")).toBeTruthy();
    expect(getByTestId("ai-assist-failed-list")).toBeTruthy();
    expect(getByTestId("ai-assist-failure-tags")).toBeTruthy();
  });

  it("고지 문구 (투자 조언이 아니라 + LIVE_AI_EXECUTION 자동 허가가 아니라) 영구 노출", () => {
    const { getByTestId, rerender } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    let disc = getByTestId("ai-assist-disclaimer").textContent;
    expect(disc).toContain("투자 조언이 아니라");
    expect(disc).toContain("LIVE_AI_EXECUTION 자동 허가가 아니라");

    rerender(<AIAssistGateCard resultOverride={_FAIL_RESULT} />);
    disc = getByTestId("ai-assist-disclaimer").textContent;
    expect(disc).toContain("투자 조언이 아니라");
  });

  it("AI 자동매매 / LIVE_AI_EXECUTION 활성화 라벨 버튼 0개", () => {
    const { container } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "AI 자동매매 시작",
        "AI 자동매매 활성화",
        "LIVE_AI_EXECUTION 활성화",
        "ENABLE_AI_EXECUTION",
        "AI 자동 실행",
        "Place Order",
        "주문 실행",
        "실거래 활성화",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("BUY/SELL/HOLD 같은 주문 신호 문구 0건", () => {
    const { container } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("평가 버튼 라벨이 'AI Assist 품질 평가'", () => {
    const { getByTestId } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const btn = getByTestId("ai-assist-evaluate-btn");
    expect(btn.textContent.trim()).toBe("AI Assist 품질 평가");
  });

  it("핵심 메트릭이 카드에 명확히 노출 (제안 수 / 거절율 / 손실율 / calibration)", () => {
    const { getByTestId } = render(
      <AIAssistGateCard resultOverride={_PASS_RESULT} />,
    );
    const metrics = getByTestId("ai-assist-metrics");
    expect(metrics.textContent).toContain("150");        // proposal_count
    expect(metrics.textContent).toContain("80");         // approved_proposals
    expect(metrics.textContent).toContain("20.0 %");     // risk_rejection_rate
    expect(metrics.textContent).toContain("0.75");       // confidence_calibration
  });
});
