import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AgentLatestTile,
  formatRelativeAge,
  pickLatestChiefDecision,
} from "./AgentLatestTile";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { aiAgentDecisions: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => { backendApi.aiAgentDecisions.mockResolvedValue([]); });


describe("pickLatestChiefDecision", () => {
  it("picks ChiefTradingAgent if present", () => {
    const rows = [
      { id: 9, agent_name: "EntryTimingAgent", decision: "BUY" },
      { id: 7, agent_name: "ChiefTradingAgent", decision: "REJECT" },
      { id: 5, agent_name: "ExitTimingAgent",  decision: "HOLD" },
    ];
    expect(pickLatestChiefDecision(rows).id).toBe(7);
  });
  it("falls back to first row when no chief present", () => {
    const rows = [
      { id: 9, agent_name: "EntryTimingAgent", decision: "BUY" },
      { id: 5, agent_name: "ExitTimingAgent",  decision: "HOLD" },
    ];
    expect(pickLatestChiefDecision(rows).id).toBe(9);
  });
  it("returns null for empty/non-array", () => {
    expect(pickLatestChiefDecision([])).toBeNull();
    expect(pickLatestChiefDecision(null)).toBeNull();
  });
});


describe("formatRelativeAge", () => {
  const NOW = Date.UTC(2026, 4, 7, 12, 0, 0);
  it("returns dash for missing/invalid", () => {
    expect(formatRelativeAge(null, NOW)).toBe("—");
    expect(formatRelativeAge("nope", NOW)).toBe("—");
  });
  it("seconds / minutes / hours / days", () => {
    expect(formatRelativeAge("2026-05-07T11:59:30Z", NOW)).toBe("30초 전");
    expect(formatRelativeAge("2026-05-07T11:55:00Z", NOW)).toBe("5분 전");
    expect(formatRelativeAge("2026-05-07T09:00:00Z", NOW)).toBe("3시간 전");
    expect(formatRelativeAge("2026-05-05T12:00:00Z", NOW)).toBe("2일 전");
  });
});


describe("<AgentLatestTile>", () => {
  it("shows chief decision summary when data present", async () => {
    backendApi.aiAgentDecisions.mockResolvedValueOnce([
      { id: 1, agent_name: "ChiefTradingAgent",
        decision: "BUY", symbol: "005930", confidence: 75,
        reasons: ["entry+news", "vol_ok"],
        created_at: new Date().toISOString(), chain_id: "c1" },
    ]);
    const { findByText, getByTestId } = render(<AgentLatestTile />);
    await findByText(/Agent 최근 결정/);
    await waitFor(() => expect(getByTestId("agent-latest-decision").textContent).toBe("BUY"));
    await findByText("005930");
    await findByText(/conf 75/);
    await findByText(/entry\+news/);
  });

  it("shows empty state when no decisions", async () => {
    const { findByText } = render(<AgentLatestTile />);
    await findByText(/최근 Agent 결정 없음/);
  });

  it("shows error state when backend fails", async () => {
    backendApi.aiAgentDecisions.mockRejectedValueOnce(new Error("offline"));
    const { findByText } = render(<AgentLatestTile />);
    await findByText(/조회 실패: offline/);
  });

  it("jump button calls onJumpTab('ai')", async () => {
    backendApi.aiAgentDecisions.mockResolvedValueOnce([
      { id: 1, agent_name: "ChiefTradingAgent",
        decision: "HOLD", symbol: "000660", confidence: 40,
        created_at: new Date().toISOString(), chain_id: "c2" },
    ]);
    const onJumpTab = vi.fn();
    const { findByTestId } = render(<AgentLatestTile onJumpTab={onJumpTab} />);
    fireEvent.click(await findByTestId("agent-latest-jump"));
    expect(onJumpTab).toHaveBeenCalledWith("ai");
  });
});
