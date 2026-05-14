/**
 * #83 통합 테스트 — displayName 적용 추가 4개 UI.
 *
 * - Approvals.jsx _OrderSummary AI hero 줄
 * - ApprovalQueue.jsx ApprovalProposalSummary + ApproveConfirmSummary
 * - AgentMemoryCard.jsx _MemoryRow + _MemoryDetail
 * - ExecutionRecommenderCard.jsx _ProposalRow 전략 필드
 *
 * 캐시 module-level — 테스트마다 reset.
 */

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalProposalSummary, ApproveConfirmSummary } from "./ApprovalQueue";
import { ExecutionRecommenderCard } from "./ExecutionRecommenderCard";
import { AgentMemoryCard } from "./AgentMemoryCard";
import { _resetStrategyDisplayLookupForTests } from "../../utils/strategyNames";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    engineBeginnerRegistry: vi.fn(),
    memorySearch:           vi.fn(),
    memoryArchive:          vi.fn(),
    memoryCreate:           vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


const _LOOKUP = [
  { strategy_id: "sma_crossover",   display_name: "단기/장기 이동평균 교차" },
  { strategy_id: "rsi_reversion",   display_name: "RSI 과매도/과매수 회복" },
  { strategy_id: "vwap_strategy",   display_name: "VWAP 평균 회귀" },
  { strategy_id: "orb_vwap",        display_name: "ORB + VWAP 돌파" },
  { strategy_id: "volume_breakout", display_name: "거래량 급증 돌파" },
  { strategy_id: "pullback_rebreak", display_name: "눌림목 재돌파" },
];


beforeEach(() => {
  _resetStrategyDisplayLookupForTests();
  backendApi.engineBeginnerRegistry.mockReset();
  backendApi.engineBeginnerRegistry.mockResolvedValue(_LOOKUP);
  backendApi.memorySearch.mockReset();
  backendApi.memorySearch.mockResolvedValue({ total: 0, items: [] });
});

afterEach(() => {
  cleanup();
  _resetStrategyDisplayLookupForTests();
});


// ---------- ApprovalQueue ----------


function _approval(overrides = {}) {
  return {
    id: 100,
    symbol: "005930",
    side: "BUY",
    quantity: 10,
    order_type: "MARKET",
    limit_price: null,
    mode: "LIVE_MANUAL_APPROVAL",
    status: "PENDING",
    created_at: new Date().toISOString(),
    requested_by_ai: true,
    strategy: "sma_crossover",
    signal_confidence: 75,
    request_source: "AI",
    request_source_label: "AI 제안",
    reasons: [],
    ai_decision_meta: { source: "AI_ASSIST" },
    ...overrides,
  };
}


describe("ApprovalQueue — proposal strategy chip", () => {
  it("displayName + (internal id) 함께", async () => {
    const { getByTestId } = render(
      <ApprovalProposalSummary approval={_approval()} />,
    );
    await waitFor(() => {
      const chip = getByTestId("proposal-strategy");
      expect(chip.textContent).toContain("단기/장기 이동평균 교차");
      expect(chip.textContent).toContain("(sma_crossover)");
      expect(chip.getAttribute("data-internal-id")).toBe("sma_crossover");
    });
  });

  it("미등록 id 는 raw 그대로", async () => {
    const { getByTestId } = render(
      <ApprovalProposalSummary approval={_approval({ strategy: "custom" })} />,
    );
    await waitFor(() => {
      const chip = getByTestId("proposal-strategy");
      expect(chip.textContent.trim()).toBe("custom");
      expect(chip.getAttribute("data-internal-id")).toBe("custom");
    });
  });
});


describe("ApprovalQueue — approve confirm summary", () => {
  it("strategy 줄에 displayName + (internal id)", async () => {
    const { getByTestId } = render(
      <ApproveConfirmSummary approval={_approval({ strategy: "vwap_strategy" })}
                              action="approve" />,
    );
    await waitFor(() => {
      const box = getByTestId("approve-confirm-summary");
      expect(box.textContent).toContain("VWAP 평균 회귀");
      expect(box.textContent).toContain("(vwap_strategy)");
    });
  });
});


// ---------- ExecutionRecommenderCard ----------


describe("ExecutionRecommenderCard — proposal 전략 필드", () => {
  const _PROPOSAL = {
    proposal_id: "p-1",
    symbol: "005930", side: "BUY", quantity: 10, order_type: "MARKET",
    limit_price: null, confidence: 75,
    expected_reward: 50000, expected_risk: 20000,
    strategy: "rsi_reversion",
    reasoning: ["RSI 회복"],
    risks: [],
    auto_apply_allowed: false,
    is_order_intent: false,
    can_execute_order: false,
    submission_mode_required: "LIVE_AI_ASSIST",
    expires_at: new Date(Date.now() + 60_000).toISOString(),
  };

  const _noop = () => Promise.resolve();

  it("displayName + (internal id) 함께", async () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard
        result={{ proposals: [_PROPOSAL], skipped: [] }}
        onPrecheck={_noop} onSubmit={_noop}
      />,
    );
    await waitFor(() => {
      const cell = getByTestId("exec-rec-strategy");
      expect(cell.textContent).toContain("RSI 과매도/과매수 회복");
      expect(cell.textContent).toContain("(rsi_reversion)");
      expect(cell.getAttribute("data-internal-id")).toBe("rsi_reversion");
    });
  });

  it("미등록 id 는 raw 그대로", async () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard
        result={{ proposals: [{ ..._PROPOSAL, strategy: "custom_x" }], skipped: [] }}
        onPrecheck={_noop} onSubmit={_noop}
      />,
    );
    await waitFor(() => {
      const cell = getByTestId("exec-rec-strategy");
      expect(cell.textContent.trim()).toBe("custom_x");
    });
  });

  it("strategy 없으면 '—' 표시 (기존 동작 유지)", async () => {
    const { getByTestId } = render(
      <ExecutionRecommenderCard
        result={{ proposals: [{ ..._PROPOSAL, strategy: null }], skipped: [] }}
        onPrecheck={_noop} onSubmit={_noop}
      />,
    );
    await waitFor(() => {
      expect(getByTestId("exec-rec-strategy").textContent.trim()).toBe("—");
    });
  });
});


// ---------- AgentMemoryCard ----------


describe("AgentMemoryCard — memory row strategy", () => {
  it("memory row 에 displayName + (internal id)", async () => {
    backendApi.memorySearch.mockResolvedValue({
      total: 1,
      items: [{
        id: 7,
        title: "전략 변경 이력",
        memory_type: "strategy_research",
        severity: "INFO",
        strategy: "orb_vwap",
        symbol: "005930",
        mode: "PAPER",
        content: "ORB 파라미터 조정",
        tags: [],
        archived: false,
        created_at: new Date().toISOString(),
      }],
    });
    const { container } = render(<AgentMemoryCard />);
    await waitFor(() => {
      const text = container.textContent || "";
      expect(text).toContain("ORB + VWAP 돌파");
      expect(text).toContain("(orb_vwap)");
    });
  });

  it("미등록 strategy 는 raw 그대로", async () => {
    backendApi.memorySearch.mockResolvedValue({
      total: 1,
      items: [{
        id: 8,
        title: "메모",
        memory_type: "operator_note",
        severity: "INFO",
        strategy: "weird_strategy_id",
        symbol: null,
        mode: null,
        content: "메모 내용",
        tags: [],
        archived: false,
        created_at: new Date().toISOString(),
      }],
    });
    const { container } = render(<AgentMemoryCard />);
    await waitFor(() => {
      const text = container.textContent || "";
      expect(text).toContain("weird_strategy_id");
    });
  });
});


// ---------- 가짜 전략명 0건 invariant (4개 UI 공통) ----------


describe("invariant — 4개 추가 UI 가짜 전략명 0건", () => {
  it("ApprovalProposalSummary 텍스트에 hype 패턴 없음", async () => {
    const { container } = render(
      <ApprovalProposalSummary approval={_approval()} />,
    );
    await waitFor(() => {
      const text = container.textContent || "";
      for (const banned of [
        "골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프",
        "황금알", "100% 승률",
        "guaranteed", "magic strategy",
      ]) {
        expect(text.includes(banned)).toBe(false);
      }
    });
  });

  it("ApproveConfirmSummary 텍스트에 hype 패턴 없음", async () => {
    const { container } = render(
      <ApproveConfirmSummary approval={_approval()} action="approve" />,
    );
    await waitFor(() => {
      const text = container.textContent || "";
      for (const banned of ["골든브릿지", "100% 승률", "guaranteed"]) {
        expect(text.includes(banned)).toBe(false);
      }
    });
  });
});
