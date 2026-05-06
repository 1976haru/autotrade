import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStatsCard } from "./AgentStatsCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { aiAgentStats: vi.fn() },
}));

const sampleStats = {
  lookback_days: 7,
  total_proposals: 12,
  approved: 8,
  rejected: 3,
  needs_approval: 1,
  approval_rate: 0.7272727272727273,
  avg_confidence: 67.5,
  top_rejection_reasons: { confidence: 2, notional: 1 },
  per_strategy: [
    { strategy: "ai_orb", total: 6, approval_rate: 0.8,
      wins: 3, losses: 1, realized_pnl: 12000 },
    { strategy: "ai_rsi", total: 4, approval_rate: 0.5,
      wins: 1, losses: 2, realized_pnl: -3500 },
  ],
  confidence_histogram: { "0-25": 1, "25-50": 2, "50-75": 5, "75-100": 4 },
  confidence_histogram_missing: 0,
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => { backendApi.aiAgentStats.mockResolvedValue(sampleStats); });

describe("AgentStatsCard", () => {
  it("loads stats on mount with default 7-day lookback", async () => {
    const { findByText } = render(<AgentStatsCard />);
    await findByText(/AI Agent 통계/);
    await waitFor(() => expect(backendApi.aiAgentStats).toHaveBeenCalledWith(7));
    await findByText("12");           // total_proposals
    await findByText("73%");          // approval_rate rounded
    await findByText("68");           // avg_confidence rounded
  });

  it("changing lookback re-queries with new value", async () => {
    const { findByText, getByText } = render(<AgentStatsCard />);
    await findByText("12");
    fireEvent.click(getByText("30일"));
    await waitFor(() => expect(backendApi.aiAgentStats).toHaveBeenLastCalledWith(30));
  });

  it("renders per-strategy rows", async () => {
    const { findByText, getByText } = render(<AgentStatsCard />);
    await findByText("ai_orb");
    getByText("ai_rsi");
    getByText("12000");
    getByText("-3500");
  });

  it("renders rejection reasons section", async () => {
    const { findByText, getByText } = render(<AgentStatsCard />);
    await findByText(/거부 사유/);
    getByText("confidence");
    getByText("notional");
  });

  it("renders error message when backend fails", async () => {
    backendApi.aiAgentStats.mockRejectedValueOnce(new Error("network"));
    const { findByText } = render(<AgentStatsCard />);
    await findByText(/Agent 통계 조회 실패: network/);
  });

  it("renders avg_confidence as dash when null", async () => {
    backendApi.aiAgentStats.mockResolvedValueOnce({
      ...sampleStats, avg_confidence: null,
    });
    const { findAllByText } = render(<AgentStatsCard />);
    const dashes = await findAllByText("—");
    expect(dashes.length).toBeGreaterThan(0);
  });
});
