import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AgentDecisionSummaryCard,
  summarizeAgentRows,
} from "./AgentDecisionSummaryCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { aiAgentDecisionsSummary: vi.fn() },
}));

const _SUMMARY = {
  total_decisions: 20,
  total_chains:    2,
  by_agent: {
    ChiefTradingAgent: { BUY: 1, REJECT: 1 },
    EntryTimingAgent:  { BUY: 1, HOLD: 1 },
    ExitTimingAgent:   { HOLD: 2 },
    NewsTrendAgent:    { INFO: 2 },
  },
  recent_chains: [
    { chain_id: "abcd-1234", decision: "BUY",    symbol: "005930",
      confidence: 70, created_at: "2026-05-07T01:00:00" },
    { chain_id: "ef01-5678", decision: "REJECT", symbol: "000660",
      confidence: 40, created_at: "2026-05-07T00:30:00" },
  ],
};

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.aiAgentDecisionsSummary.mockResolvedValue({
    total_decisions: 0, total_chains: 0, by_agent: {}, recent_chains: [],
  });
});


describe("summarizeAgentRows", () => {
  it("returns rows sorted by total desc", () => {
    const rows = summarizeAgentRows({
      A: { BUY: 1 },
      B: { BUY: 5, HOLD: 2 },
      C: { HOLD: 3 },
    });
    expect(rows[0].agent).toBe("B");
    expect(rows[0].total).toBe(7);
    expect(rows.map((r) => r.agent)).toEqual(["B", "C", "A"]);
  });
  it("handles empty / non-object inputs", () => {
    expect(summarizeAgentRows(null)).toEqual([]);
    expect(summarizeAgentRows({})).toEqual([]);
  });
});


describe("<AgentDecisionSummaryCard>", () => {
  it("loads summary on mount and renders totals + per-agent rows", async () => {
    backendApi.aiAgentDecisionsSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId, findByText } = render(<AgentDecisionSummaryCard />);
    await findByText(/Agent 결정 분포/);
    const totals = await findByTestId("agent-summary-totals");
    expect(totals.textContent).toContain("20");
    expect(totals.textContent).toContain("2");
    await findByTestId("agent-summary-row-ChiefTradingAgent");
    await findByTestId("agent-summary-row-EntryTimingAgent");
  });

  it("renders empty body when no decisions yet", async () => {
    const { findByText } = render(<AgentDecisionSummaryCard />);
    await findByText(/아직 누적 결정 없음/);
  });

  it("renders error state on backend failure", async () => {
    backendApi.aiAgentDecisionsSummary.mockRejectedValueOnce(new Error("offline"));
    const { findByText } = render(<AgentDecisionSummaryCard />);
    await findByText(/Agent 요약 조회 실패: offline/);
  });

  it("renders decision badges with counts", async () => {
    backendApi.aiAgentDecisionsSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId } = render(<AgentDecisionSummaryCard />);
    const chief = await findByTestId("agent-summary-row-ChiefTradingAgent");
    expect(chief.textContent).toContain("BUY 1");
    expect(chief.textContent).toContain("REJECT 1");
  });

  it("renders recent chain pins when present", async () => {
    backendApi.aiAgentDecisionsSummary.mockResolvedValueOnce(_SUMMARY);
    const { findByTestId } = render(<AgentDecisionSummaryCard />);
    await findByTestId("agent-summary-chain-abcd-1234");
    await findByTestId("agent-summary-chain-ef01-5678");
  });

  it("refresh button re-queries", async () => {
    const { findByText } = render(<AgentDecisionSummaryCard />);
    await findByText(/Agent 결정 분포/);
    await waitFor(() => expect(backendApi.aiAgentDecisionsSummary).toHaveBeenCalledTimes(1));
    fireEvent.click(await findByText(/새로고침/));
    await waitFor(() => expect(backendApi.aiAgentDecisionsSummary).toHaveBeenCalledTimes(2));
  });
});
