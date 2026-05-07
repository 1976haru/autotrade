import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentDecisionHero } from "./AgentDecisionHero";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    aiAgentDecisions:  vi.fn(),
    marketRegime:      vi.fn(),
    preMarketBrief:    vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


describe("<AgentDecisionHero>", () => {
  beforeEach(() => {
    backendApi.aiAgentDecisions.mockReset();
    backendApi.marketRegime.mockReset();
    backendApi.preMarketBrief.mockReset();
  });
  afterEach(cleanup);

  it("renders chief decision with confidence and reasons", async () => {
    backendApi.aiAgentDecisions.mockResolvedValue([
      {
        agent_name: "ChiefTradingAgent", decision: "BUY", confidence: 78,
        symbol: "005930",
        reasons: ["chief:entry_buy", "trend_up:+2.5%", "regime fits sma_crossover"],
      },
      {
        agent_name: "EntryTimingAgent", decision: "BUY", confidence: 70,
        reasons: ["close_up"],
      },
    ]);
    backendApi.marketRegime.mockResolvedValue({
      regime: "TREND_UP", trade_permission: "ALLOW", risk_multiplier: 1.0,
    });
    backendApi.preMarketBrief.mockResolvedValue({
      readiness_label: "READY", readiness_score: 80,
    });
    const { getByTestId } = render(<AgentDecisionHero />);
    await waitFor(() => {
      expect(getByTestId("agent-decision-hero")).toBeTruthy();
    });
    expect(getByTestId("agent-hero-decision").textContent).toContain("BUY");
    expect(getByTestId("agent-hero-symbol").textContent).toContain("005930");
    expect(getByTestId("agent-hero-confidence").textContent).toContain("78");
    expect(getByTestId("agent-hero-regime").textContent).toContain("TREND_UP");
    expect(getByTestId("agent-hero-readiness").textContent).toContain("READY");
    const reasons = getByTestId("agent-hero-reasons");
    expect(reasons.children.length).toBe(3);
  });

  it("shows EmptyState when there is no chief decision yet", async () => {
    backendApi.aiAgentDecisions.mockResolvedValue([]);
    backendApi.marketRegime.mockResolvedValue({});
    backendApi.preMarketBrief.mockResolvedValue({});
    const { findByText } = render(<AgentDecisionHero />);
    expect(await findByText("최근 Agent 판단 없음")).toBeTruthy();
  });

  it("shows ErrorState with friendly message on backend failure", async () => {
    backendApi.aiAgentDecisions.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.marketRegime.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.preMarketBrief.mockRejectedValue(new Error("Failed to fetch"));
    const { findByText, queryByText } = render(<AgentDecisionHero />);
    expect(await findByText("Agent 판단 조회 실패")).toBeTruthy();
    // raw "Failed to fetch" 문구는 노출되지 않아야 함
    expect(queryByText(/Failed to fetch/)).toBeNull();
  });

  it("falls back to neutral metadata for unknown decision strings", async () => {
    backendApi.aiAgentDecisions.mockResolvedValue([
      { agent_name: "ChiefTradingAgent", decision: "UNKNOWN_NEW",
        confidence: 50, reasons: ["x"] },
    ]);
    backendApi.marketRegime.mockResolvedValue({});
    backendApi.preMarketBrief.mockResolvedValue({});
    const { findByTestId } = render(<AgentDecisionHero />);
    const badge = await findByTestId("agent-hero-decision");
    expect(badge.textContent).toContain("UNKNOWN_NEW");
  });
});
