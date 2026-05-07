import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OperatingLoopCard } from "./OperatingLoopCard";


// 223: backendApi mock — deterministic output 검증. 빈 응답 / 에러 분기 모두.
vi.mock("../../services/backend/client", () => ({
  backendApi: {
    operatingLoopStatus: vi.fn(),
    preMarketBrief:      vi.fn(),
    intradaySummary:     vi.fn(),
    postMarketReview:    vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


describe("<OperatingLoopCard>", () => {
  beforeEach(() => {
    backendApi.operatingLoopStatus.mockReset();
    backendApi.preMarketBrief.mockReset();
    backendApi.intradaySummary.mockReset();
    backendApi.postMarketReview.mockReset();
  });
  afterEach(cleanup);

  it("renders stage + readiness from backend", async () => {
    backendApi.operatingLoopStatus.mockResolvedValue({
      stage: "intraday",
      stages: ["pre_market", "market_open_watch", "intraday", "position_monitor", "post_market"],
    });
    backendApi.preMarketBrief.mockResolvedValue({
      market_risk_level: "MEDIUM",
      readiness_label: "READY",
      readiness_score: 70,
      operator_summary: ["오늘 자동운용 READY", "위험도 MEDIUM", "손실 한도 1,000,000원"],
    });
    backendApi.intradaySummary.mockResolvedValue({
      candidates_evaluated: 7, virtual_orders_made: 2, rejected_signals: 5,
    });
    backendApi.postMarketReview.mockResolvedValue({
      total_decisions: 12, agent_score_delta: 25,
    });

    const { getByTestId } = render(<OperatingLoopCard />);
    await waitFor(() => {
      expect(getByTestId("operating-loop-stage")).toBeTruthy();
    });
    expect(getByTestId("operating-loop-stage").textContent).toContain("장중 판단");
    expect(getByTestId("operating-loop-brief").textContent).toContain("READY");
    expect(getByTestId("operating-loop-intraday").textContent).toContain("7");
    expect(getByTestId("operating-loop-review").textContent).toContain("+25");
  });

  it("renders error message when backend fails", async () => {
    backendApi.operatingLoopStatus.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.preMarketBrief.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.intradaySummary.mockRejectedValue(new Error("Failed to fetch"));
    backendApi.postMarketReview.mockRejectedValue(new Error("Failed to fetch"));
    const { findByTestId, queryByText } = render(<OperatingLoopCard />);
    const err = await findByTestId("operating-loop-error");
    expect(err.textContent).toContain("Agent 흐름 조회 실패");
    // 233 (UI-005): raw 'Failed to fetch'는 사용자에게 노출되지 않아야 한다.
    expect(queryByText(/Failed to fetch/)).toBeNull();
  });
});
