/**
 * #82 통합 테스트 — displayName + (internal_id) 가 핵심 UI에서 *함께* 노출되는지.
 *
 * - OrderAuditRow strategy badge
 * - BacktestStrategyMiniTable 셀
 * - BacktestExtremesSummary best/worst
 * - AgentStatsCard per-strategy 행
 *
 * 캐시 module-level — 테스트마다 reset 필요.
 */

import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OrderAuditRow, BacktestStrategyMiniTable, BacktestExtremesSummary } from "./AuditLog";
import { AgentStatsCard } from "./AgentStatsCard";
import { _resetStrategyDisplayLookupForTests } from "../../utils/strategyNames";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    engineBeginnerRegistry: vi.fn(),
    aiAgentStats: vi.fn(),
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
});

afterEach(() => {
  cleanup();
  _resetStrategyDisplayLookupForTests();
});


// ---------- OrderAuditRow ----------


function _orderRow(strategy = "sma_crossover") {
  return {
    id: 1, created_at: new Date().toISOString(),
    symbol: "005930", side: "BUY", quantity: 10, order_type: "MARKET",
    latest_price: 70000, decision: "APPROVED",
    reasons: [], requested_by_ai: false, executed: true,
    filled_quantity: 10, broker_status: "FILLED",
    mode: "PAPER", strategy,
  };
}


describe("OrderAuditRow strategy badge", () => {
  it("lookup 적용 후 displayName + (internal id) 함께 표시", async () => {
    const { getByTestId } = render(
      <OrderAuditRow r={_orderRow("sma_crossover")} />,
    );
    await waitFor(() => {
      const badge = getByTestId("strategy-badge");
      expect(badge.textContent).toContain("단기/장기 이동평균 교차");
      expect(badge.textContent).toContain("(sma_crossover)");
      expect(badge.getAttribute("data-internal-id")).toBe("sma_crossover");
    });
  });

  it("미등록 internal id 는 raw 그대로 (graceful fallback)", async () => {
    const { getByTestId } = render(
      <OrderAuditRow r={_orderRow("custom_unknown")} />,
    );
    await waitFor(() => {
      const badge = getByTestId("strategy-badge");
      expect(badge.textContent.trim()).toBe("custom_unknown");
      expect(badge.getAttribute("data-internal-id")).toBe("custom_unknown");
    });
  });

  it("lookup 부재 (네트워크 실패) 시 internal id 그대로 — 깨지지 않음", async () => {
    backendApi.engineBeginnerRegistry.mockRejectedValue(new Error("net"));
    const { getByTestId } = render(
      <OrderAuditRow r={_orderRow("rsi_reversion")} />,
    );
    // 즉시 internal id 로 fallback (캐시가 비어 있으므로 첫 렌더 = raw).
    const badge = getByTestId("strategy-badge");
    expect(badge.textContent).toContain("rsi_reversion");
  });
});


// ---------- BacktestStrategyMiniTable ----------


describe("BacktestStrategyMiniTable", () => {
  const _runs = [
    { id: 1, strategy: "sma_crossover", total_pnl:  100000, win_count: 6, loss_count: 4 },
    { id: 2, strategy: "sma_crossover", total_pnl: -50000,  win_count: 3, loss_count: 5 },
    { id: 3, strategy: "vwap_strategy", total_pnl:  200000, win_count: 7, loss_count: 3 },
    { id: 4, strategy: "vwap_strategy", total_pnl:  120000, win_count: 5, loss_count: 4 },
  ];

  it("displayName + (internal id) 함께 셀에 표시", async () => {
    const { getByTestId } = render(
      <BacktestStrategyMiniTable items={_runs} />,
    );
    await waitFor(() => {
      const sma = getByTestId("backtest-strategy-cell-sma_crossover");
      expect(sma.textContent).toContain("단기/장기 이동평균 교차");
      expect(sma.textContent).toContain("(sma_crossover)");
      expect(sma.getAttribute("data-internal-id")).toBe("sma_crossover");
    });
  });

  it("미등록 strategy 는 raw 그대로", async () => {
    const items = [
      { id: 1, strategy: "fancy_new", total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 2, strategy: "fancy_new", total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 3, strategy: "another",   total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 4, strategy: "another",   total_pnl: 1, win_count: 1, loss_count: 0 },
    ];
    const { getByTestId } = render(
      <BacktestStrategyMiniTable items={items} />,
    );
    await waitFor(() => {
      const cell = getByTestId("backtest-strategy-cell-fancy_new");
      expect(cell.textContent.trim()).toBe("fancy_new");
    });
  });
});


// ---------- BacktestExtremesSummary ----------


describe("BacktestExtremesSummary best/worst", () => {
  it("best/worst 행에 displayName + (internal id)", async () => {
    const items = [
      { id: 1, strategy: "sma_crossover", total_pnl:  500000 },
      { id: 2, strategy: "rsi_reversion", total_pnl: -100000 },
    ];
    const { getByTestId } = render(
      <BacktestExtremesSummary items={items} />,
    );
    await waitFor(() => {
      const best = getByTestId("backtest-extremes-best");
      expect(best.textContent).toContain("단기/장기 이동평균 교차");
      expect(best.textContent).toContain("(sma_crossover)");

      const worst = getByTestId("backtest-extremes-worst");
      expect(worst.textContent).toContain("RSI 과매도/과매수 회복");
      expect(worst.textContent).toContain("(rsi_reversion)");
    });
  });
});


// ---------- AgentStatsCard per-strategy ----------


describe("AgentStatsCard per-strategy 행", () => {
  it("displayName + (internal id) 표시 + data-internal-id 보존", async () => {
    backendApi.aiAgentStats.mockResolvedValue({
      total_decisions: 100, approved: 60, rejected: 40,
      confidence_histogram: { "0-25": 5, "25-50": 20, "50-75": 50, "75-100": 25 },
      per_strategy: [
        { strategy: "sma_crossover", total: 50, approval_rate: 0.7,
          wins: 30, losses: 15, realized_pnl: 500000 },
        { strategy: "orb_vwap", total: 30, approval_rate: 0.6,
          wins: 18, losses: 10, realized_pnl: -100000 },
      ],
    });
    let utils;
    await act(async () => {
      utils = render(<AgentStatsCard />);
    });
    const { container } = utils;
    await waitFor(() => {
      const rows = container.querySelectorAll("[data-internal-id]");
      expect(rows.length).toBeGreaterThanOrEqual(2);
      const text = container.textContent || "";
      expect(text).toContain("단기/장기 이동평균 교차");
      expect(text).toContain("(sma_crossover)");
      expect(text).toContain("ORB + VWAP 돌파");
      expect(text).toContain("(orb_vwap)");
    });
  });
});


// ---------- invariant — 가짜 전략명 0건 ----------


describe("invariant — 가짜 전략명 0건", () => {
  it("OrderAuditRow / BacktestMiniTable 출력에 외부 hype 전략명 없음", async () => {
    const items = [
      { id: 1, strategy: "sma_crossover", total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 2, strategy: "sma_crossover", total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 3, strategy: "vwap_strategy", total_pnl: 1, win_count: 1, loss_count: 0 },
      { id: 4, strategy: "vwap_strategy", total_pnl: 1, win_count: 1, loss_count: 0 },
    ];
    const { container } = render(<BacktestStrategyMiniTable items={items} />);
    await waitFor(() => {
      const text = container.textContent || "";
      for (const banned of [
        "골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프",
        "황금알", "100% 승률",
        "guaranteed", "magic strategy", "secret formula",
      ]) {
        expect(text.includes(banned)).toBe(false);
      }
    });
  });
});
