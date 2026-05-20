import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentStrategyChoiceCard } from "./AgentStrategyChoiceCard";
import { MarketPhase } from "../../utils/marketHours";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    engineStatus:   vi.fn(),
    engineRegistry: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


describe("<AgentStrategyChoiceCard>", () => {
  beforeEach(() => {
    backendApi.engineStatus.mockReset();
    backendApi.engineRegistry.mockReset();
  });
  afterEach(cleanup);

  it("renders loading then card", async () => {
    backendApi.engineStatus.mockResolvedValue({});
    backendApi.engineRegistry.mockResolvedValue([]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    expect(getByTestId("agent-strategy-choice-card-loading")).toBeTruthy();
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
  });

  it("renders the four featured strategies as chips", async () => {
    backendApi.engineStatus.mockResolvedValue({});
    backendApi.engineRegistry.mockResolvedValue([]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
    expect(getByTestId("agent-strategy-chip-volume_breakout")).toBeTruthy();
    expect(getByTestId("agent-strategy-chip-pullback_rebreak")).toBeTruthy();
    expect(getByTestId("agent-strategy-chip-vwap_strategy")).toBeTruthy();
    expect(getByTestId("agent-strategy-chip-orb_vwap")).toBeTruthy();
  });

  it("highlights the active strategy when status.strategies is set", async () => {
    backendApi.engineStatus.mockResolvedValue({
      strategies: ["volume_breakout"],
      regime: "trending_up",
    });
    backendApi.engineRegistry.mockResolvedValue([
      { name: "volume_breakout", required_regime: "trending_up" },
      { name: "pullback_rebreak", required_regime: "trending_up" },
    ]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
    expect(getByTestId("agent-strategy-chip-volume_breakout")
           .getAttribute("data-selected")).toBe("true");
    expect(getByTestId("agent-strategy-chip-pullback_rebreak")
           .getAttribute("data-selected")).toBe("false");
  });

  it("highlights active strategy when status.active_strategy is a string", async () => {
    backendApi.engineStatus.mockResolvedValue({
      active_strategy: "vwap_strategy",
    });
    backendApi.engineRegistry.mockResolvedValue([{ name: "vwap_strategy" }]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
    expect(getByTestId("agent-strategy-chip-vwap_strategy")
           .getAttribute("data-selected")).toBe("true");
  });

  it("shows regime text when present", async () => {
    backendApi.engineStatus.mockResolvedValue({ regime: "TREND_UP" });
    backendApi.engineRegistry.mockResolvedValue([]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
    expect(getByTestId("agent-strategy-choice-card-regime").textContent)
      .toContain("TREND_UP");
  });

  it("shows 'no selection' rationale when nothing active", async () => {
    backendApi.engineStatus.mockResolvedValue({});
    backendApi.engineRegistry.mockResolvedValue([]);
    const { getByTestId } = render(<AgentStrategyChoiceCard />);
    await waitFor(() => getByTestId("agent-strategy-choice-card"));
    expect(getByTestId("agent-strategy-choice-card-rationale").textContent)
      .toContain("선택된 전략이 아직 없습니다");
  });

  it("shows operator-only notice — no toggle/order buttons", async () => {
    backendApi.engineStatus.mockResolvedValue({});
    backendApi.engineRegistry.mockResolvedValue([]);
    const { container } = render(<AgentStrategyChoiceCard />);
    await waitFor(() =>
      expect(container.querySelector('[data-testid="agent-strategy-choice-card"]')).toBeTruthy(),
    );
    expect(container.querySelector('button[data-testid*="activate"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="execute"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="place"]')).toBeNull();
  });

  it("does not expose 'Failed to fetch' raw text (정규장 OPEN 한정)", async () => {
    backendApi.engineStatus.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), {}),
    );
    backendApi.engineRegistry.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), {}),
    );
    const { getByTestId } = render(
      <AgentStrategyChoiceCard marketPhase={MarketPhase.OPEN} />,
    );
    await waitFor(() => getByTestId("agent-strategy-choice-card-error"));
    expect(getByTestId("agent-strategy-choice-card-error").textContent)
      .not.toContain("Failed to fetch");
  });

  // fix/market-closed-state-distinction ─────────────────────────────────────

  it("CLOSED + fetch 실패 → 'AI 전략 선택 조회 실패' 대신 market-closed 안내", async () => {
    backendApi.engineStatus.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.engineRegistry.mockRejectedValue(new Error("Failed to fetch"));
    const { findByTestId, queryByText } = render(
      <AgentStrategyChoiceCard marketPhase={MarketPhase.CLOSED} />,
    );
    const notice = await findByTestId("agent-strategy-choice-card-market-closed");
    expect(notice.textContent).toContain("장 종료");
    expect(queryByText("AI 전략 선택 조회 실패")).toBeNull();
  });

  it("WEEKEND + fetch 실패 → '주말 휴장' 안내", async () => {
    backendApi.engineStatus.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.engineRegistry.mockRejectedValue(new Error("Failed to fetch"));
    const { findByTestId, queryByText } = render(
      <AgentStrategyChoiceCard marketPhase={MarketPhase.WEEKEND} />,
    );
    const notice = await findByTestId("agent-strategy-choice-card-market-closed");
    expect(notice.textContent).toContain("주말 휴장");
    expect(queryByText("AI 전략 선택 조회 실패")).toBeNull();
  });

  it("PRE_OPEN + fetch 실패 → '장 시작 전' 안내", async () => {
    backendApi.engineStatus.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.engineRegistry.mockRejectedValue(new Error("Failed to fetch"));
    const { findByTestId, queryByText } = render(
      <AgentStrategyChoiceCard marketPhase={MarketPhase.PRE_OPEN} />,
    );
    const notice = await findByTestId("agent-strategy-choice-card-market-closed");
    expect(notice.textContent).toContain("장 시작 전");
    expect(queryByText("AI 전략 선택 조회 실패")).toBeNull();
  });
});
