import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AiExecutionPolicyCard, useAiExecutionPolicy } from "./AiExecutionPolicyCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { aiExecutionPolicy: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });

const _disabledPolicy = {
  enable_ai_execution: false,
  enable_live_trading: false,
  is_canary_mode: true,
  min_confidence: 80,
  min_quality_score: 70,
  require_explanation: true,
  require_exit_plan: true,
  max_notional_per_order: 100000,
  symbol_whitelist: [],
  window_start_hour_kst: 10,
  window_end_hour_kst: 14,
  max_orders_per_day: 3,
  live_ai_execution_disabled: true,
  canary_note: "AI execution canary only; no broker order sent",
  notice: "AI API Key는 주문 권한이 아닙니다.",
};

beforeEach(() => {
  backendApi.aiExecutionPolicy.mockResolvedValue(_disabledPolicy);
});


describe("<AiExecutionPolicyCard>", () => {
  it("shows '비활성 (기본값)' status badge by default", () => {
    const { getByTestId } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    expect(getByTestId("ai-execution-status-badge").textContent)
      .toContain("비활성");
  });

  it("shows the disabled disclaimer when defaults apply", () => {
    const { getByTestId } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    const disclaimer = getByTestId("ai-execution-disabled-disclaimer");
    expect(disclaimer.textContent).toContain("AI 자동 실행은 기본 비활성화");
    expect(disclaimer.textContent).toContain("활성화 토글이 의도적으로 제공되지 않습니다");
  });

  it("does NOT render any toggle / start button (read-only invariant)", () => {
    const { container } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    // 본 read-only 카드의 핵심 invariant: 어떤 button / input / select도 없다.
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
    // "비활성화" disclaimer는 허용 — "활성화 토글" 같은 버튼 라벨이 없는지만 확인.
    expect(container.textContent).not.toMatch(/시작|Enable AI Execution|자동매매 시작/);
  });

  it("shows canary badge + disclaimer when opted-in but canary still on", () => {
    const optedIn = {
      ..._disabledPolicy,
      enable_ai_execution: true,
      enable_live_trading: true,
      is_canary_mode: true,
      live_ai_execution_disabled: false,
    };
    const { getByTestId, queryByTestId } = render(
      <AiExecutionPolicyCard policy={optedIn} loading={false} error="" />,
    );
    expect(getByTestId("ai-execution-status-badge").textContent).toContain("canary");
    const canary = getByTestId("ai-execution-canary-disclaimer");
    expect(canary.textContent).toContain("CANARY_ONLY");
    // disabled disclaimer should NOT appear when opted-in.
    expect(queryByTestId("ai-execution-disabled-disclaimer")).toBeNull();
  });

  it("displays the empty whitelist with safety hint", () => {
    const { container } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    expect(container.textContent).toContain("모든 종목 차단");
  });

  it("formats max_notional_per_order with thousands separator", () => {
    const { container } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    // 100,000원 — toLocaleString.
    expect(container.textContent).toContain("100,000원");
  });

  it("formats execution window hours as HH:00 — HH:00", () => {
    const { container } = render(
      <AiExecutionPolicyCard policy={_disabledPolicy} loading={false} error="" />,
    );
    expect(container.textContent).toContain("10:00 — 14:00");
  });

  it("shows loading state without policy", () => {
    const { getByText } = render(
      <AiExecutionPolicyCard policy={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows error state when error is set", () => {
    const { getByTestId } = render(
      <AiExecutionPolicyCard policy={null} loading={false} error="boom" />,
    );
    expect(getByTestId("ai-execution-policy-error").textContent).toContain("boom");
  });

  it("renders nothing when policy null and not loading/error", () => {
    const { container } = render(
      <AiExecutionPolicyCard policy={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });
});


describe("useAiExecutionPolicy integration", () => {
  it("fetches policy on mount and renders into card", async () => {
    function Probe() {
      const r = useAiExecutionPolicy();
      return <AiExecutionPolicyCard
        policy={r.policy} loading={r.loading} error={r.error}
      />;
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.aiExecutionPolicy).toHaveBeenCalled());
    const badge = await findByTestId("ai-execution-status-badge");
    expect(badge.textContent).toContain("비활성");
  });
});
