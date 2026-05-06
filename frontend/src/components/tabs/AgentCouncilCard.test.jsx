import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentCouncilCard } from "./AgentCouncilCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { aiAgentDecisions: vi.fn() },
}));

const sampleChain = [
  {
    id: 1,  agent_name: "ChiefTradingAgent", symbol: "005930",
    decision: "BUY", confidence: 70, reasons: ["entry+news"],
    chain_id: "chain-A", created_at: "2026-05-07T01:23:45",
  },
  {
    id: 2,  agent_name: "MarketRegimeAgent", symbol: "005930",
    decision: "INFO", confidence: 50, reasons: ["trending_up"],
    chain_id: "chain-A", created_at: "2026-05-07T01:23:45",
  },
  {
    id: 3,  agent_name: "EntryTimingAgent", symbol: "005930",
    decision: "BUY", confidence: 60, reasons: ["close>prev"],
    chain_id: "chain-A", created_at: "2026-05-07T01:23:45",
  },
];

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => { backendApi.aiAgentDecisions.mockResolvedValue(sampleChain); });

describe("AgentCouncilCard", () => {
  it("loads decisions on mount and groups by chain", async () => {
    const { findByText, getByText } = render(<AgentCouncilCard />);
    await findByText(/Agent Council 결정/);
    await waitFor(() => expect(backendApi.aiAgentDecisions).toHaveBeenCalledWith(50));
    // chief decision (BUY) is the visible summary, plus item count.
    await findByText("BUY");
    getByText(/3개 결정/);
  });

  it("expands chain to show all member rows on click", async () => {
    const { findByText, queryByText } = render(<AgentCouncilCard />);
    const summary = await findByText(/3개 결정/);
    expect(queryByText("MarketRegimeAgent")).toBeNull();
    fireEvent.click(summary.parentElement);
    await findByText("MarketRegimeAgent");
    await findByText("EntryTimingAgent");
  });

  it("renders empty state when no decisions", async () => {
    backendApi.aiAgentDecisions.mockResolvedValueOnce([]);
    const { findByText } = render(<AgentCouncilCard />);
    await findByText(/아직 기록된 Agent 결정이 없습니다/);
  });

  it("renders error message when backend fails", async () => {
    backendApi.aiAgentDecisions.mockRejectedValueOnce(new Error("boom"));
    const { findByText } = render(<AgentCouncilCard />);
    await findByText(/Agent 결정 조회 실패: boom/);
  });

  it("refresh button re-queries backend", async () => {
    const { findByText, getByText } = render(<AgentCouncilCard />);
    await findByText("BUY");
    expect(backendApi.aiAgentDecisions).toHaveBeenCalledTimes(1);
    fireEvent.click(getByText(/새로고침/));
    await waitFor(() => expect(backendApi.aiAgentDecisions).toHaveBeenCalledTimes(2));
  });
});
