import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ExecutionRecommenderCard,
  defaultPrecheckHandler,
  defaultSubmitHandler,
} from "./ExecutionRecommenderCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    executionRecommenderRecommend: vi.fn(),
    executionRecommenderPrecheck: vi.fn(),
    executionRecommenderSubmit: vi.fn(),
  },
}));


const _PROPOSAL = {
  proposal_id: "abc123",
  symbol: "005930",
  side: "BUY",
  quantity: 10,
  order_type: "MARKET",
  limit_price: null,
  confidence: 72,
  quality_score: 80,
  supporting_reasons: ["breakout above 200ma", "volume spike"],
  opposing_reasons: ["RSI overbought"],
  risk_note: "공격적 진입 — 손절 엄수",
  target_price: 75000,
  stop_price: 68000,
  expected_reward: 50000,
  expected_risk: 20000,
  risk_reward_ratio: 2.5,
  strategy: "ai_assist:execution_recommender",
  model: "claude-opus-4-7",
  analysis_log_id: 42,
  market_regime: "TREND_UP",
  expires_at: "2026-12-31T00:00:00+00:00",
  created_at: "2026-05-09T12:00:00+00:00",
  is_order_intent: false,
  can_execute_order: false,
};

const _RESULT = {
  proposals: [_PROPOSAL],
  skipped: [{ symbol: "000660", reason: "confidence 20 < 40" }],
  auto_apply_allowed: false,
  is_order_signal: false,
  created_at: "2026-05-09T12:00:00+00:00",
  notice: "본 응답은 advisory 제안입니다.",
};


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.executionRecommenderPrecheck.mockResolvedValue({
    outcome: "APPROVED",
    reasons: [],
    warnings: [],
    risk_score: 10,
    blocked_by: null,
    required_action: null,
    evaluated_at: "2026-05-09T12:00:00+00:00",
    proposal_id: "abc123",
    notice: "사전검사는 audit row를 작성하지 않습니다.",
  });
  backendApi.executionRecommenderSubmit.mockResolvedValue({
    decision: "NEEDS_APPROVAL",
    reasons: [],
    audit_id: 100,
    approval_id: 50,
    permission_note: "AI Permission Gate ok",
    candidate_meta: {},
    proposal_id: "abc123",
    submitted_at: "2026-05-09T12:00:01+00:00",
    notice: "ai.assist.submit_candidate에 위임됩니다.",
  });
});


describe("<ExecutionRecommenderCard>", () => {
  it("renders '주문 아님 · 승인 필요' badge prominently", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    expect(getByTestId("exec-rec-not-order-badge").textContent)
      .toMatch(/주문 아님|승인 필요/);
  });

  it("renders disclaimer notice", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    const notice = getByTestId("exec-rec-notice");
    expect(notice.textContent).toMatch(/주문이 아닙니다/);
    expect(notice.textContent).toMatch(/audit row를 만들지 않/);
    expect(notice.textContent).toMatch(/RiskManager 재검증/);
  });

  it("renders proposal symbol, side, quantity, confidence", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    expect(getByTestId("exec-rec-symbol").textContent).toBe("005930");
    expect(getByTestId("exec-rec-side").textContent).toBe("BUY");
    expect(getByTestId("exec-rec-confidence").textContent).toContain("72");
  });

  it("renders R:R ratio with green color when >= 2", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    const rr = getByTestId("exec-rec-rr");
    expect(rr.textContent).toBe("2.50");
    expect(rr.getAttribute("style") || "").toMatch(/22c55e|34, 197, 94/i);
  });

  it("renders supporting and opposing reasons", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    expect(getByTestId("exec-rec-supporting").textContent)
      .toContain("breakout above 200ma");
    expect(getByTestId("exec-rec-opposing").textContent)
      .toContain("RSI overbought");
    expect(getByTestId("exec-rec-risk-note").textContent)
      .toContain("공격적 진입");
  });

  it("renders 위험 사전검사 + 승인 대기 후보로 보내기 buttons", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error=""
                                  onPrecheck={defaultPrecheckHandler}
                                  onSubmit={defaultSubmitHandler} />,
    );
    expect(getByTestId("exec-rec-precheck-btn").textContent)
      .toMatch(/위험 사전검사/);
    expect(getByTestId("exec-rec-submit-btn").textContent)
      .toMatch(/승인 대기 후보로 보내기/);
  });

  it("does NOT render direct order buttons (매수 실행 / 매도 실행 / Place Order)", () => {
    const { container } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error="" />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      // 직접 주문 라벨 금지 — 본 카드는 *제안*만.
      expect(text).not.toMatch(/매수 실행|매도 실행|즉시 주문|Place Order|Submit Order/i);
      expect(text).not.toMatch(/주문 발생|주문 보내기|broker.*order/i);
    }
  });

  it("clicking 위험 사전검사 calls onPrecheck and renders result", async () => {
    const { getByTestId, findByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error=""
                                  onPrecheck={defaultPrecheckHandler}
                                  onSubmit={defaultSubmitHandler} />,
    );
    fireEvent.click(getByTestId("exec-rec-precheck-btn"));
    await waitFor(() =>
      expect(backendApi.executionRecommenderPrecheck).toHaveBeenCalledWith(_PROPOSAL),
    );
    const result = await findByTestId("exec-rec-precheck-result");
    expect(result.textContent).toContain("APPROVED");
    expect(result.textContent).toMatch(/audit row.*작성하지 않/);
  });

  it("clicking 승인 대기 후보로 보내기 calls onSubmit and renders approval id", async () => {
    const { getByTestId, findByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error=""
                                  onPrecheck={defaultPrecheckHandler}
                                  onSubmit={defaultSubmitHandler} />,
    );
    fireEvent.click(getByTestId("exec-rec-submit-btn"));
    await waitFor(() =>
      expect(backendApi.executionRecommenderSubmit).toHaveBeenCalledWith(_PROPOSAL),
    );
    const result = await findByTestId("exec-rec-submit-result");
    expect(result.textContent).toContain("NEEDS_APPROVAL");
    expect(result.textContent).toContain("#50");
    expect(result.textContent).toContain("결재 탭");
  });

  it("renders error label when handler throws", async () => {
    backendApi.executionRecommenderPrecheck.mockRejectedValue(new Error("boom"));
    const { getByTestId, findByTestId } = render(
      <ExecutionRecommenderCard result={_RESULT}
                                  loading={false} error=""
                                  onPrecheck={defaultPrecheckHandler}
                                  onSubmit={defaultSubmitHandler} />,
    );
    fireEvent.click(getByTestId("exec-rec-precheck-btn"));
    const errEl = await findByTestId("exec-rec-error");
    expect(errEl.textContent).toContain("사전검사 실패");
    expect(errEl.textContent).toContain("boom");
  });

  it("renders empty state when no proposals and only skipped", () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard
        result={{ proposals: [], skipped: [{ symbol: "x", reason: "low conf" }],
                  auto_apply_allowed: false, is_order_signal: false }}
        loading={false} error="" />,
    );
    const empty = getByTestId("exec-rec-empty");
    expect(empty.textContent).toMatch(/추천 후보가 없습니다/);
  });

  it("shows loading state without result", () => {
    const { getByText } = render(
      <ExecutionRecommenderCard result={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows friendly fallback on load error", () => {
    const { getByTestId, container } = render(
      <ExecutionRecommenderCard result={null} loading={false} error="boom" />,
    );
    const err = getByTestId("exec-rec-load-error");
    expect(err.textContent).toMatch(/제안 데이터/);
    expect(container.textContent).not.toContain("boom");
  });

  it("renders nothing when result is null and no loading/error", () => {
    const { container } = render(
      <ExecutionRecommenderCard result={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("default handlers wire to backendApi", async () => {
    await defaultPrecheckHandler(_PROPOSAL);
    expect(backendApi.executionRecommenderPrecheck).toHaveBeenCalledWith(_PROPOSAL);
    await defaultSubmitHandler(_PROPOSAL);
    expect(backendApi.executionRecommenderSubmit).toHaveBeenCalledWith(_PROPOSAL);
  });
});
