/**
 * #49 / 6-04 — PerformanceMetricsSummary 단위 테스트.
 *
 * lock: 전략별 승률/손익비/PF/MDD/expectancy + 체결 실패율/거절률/부분체결률 +
 * 차단 사유 TOP + Agent Council vs best single + 표시 전용 문구 + 버튼/입력 0개.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PerformanceMetricsSummary } from "./PerformanceMetricsSummary";

afterEach(cleanup);

const _PERF = {
  status: "OK",
  strategies: [
    { strategy: "ORB", win_rate: 0.55, payoff_ratio: 1.8, profit_factor: 1.6,
      max_drawdown: 0.08, expectancy: 1200 },
    { strategy: "AGENT_COUNCIL", win_rate: 0.61, payoff_ratio: 2.1, profit_factor: 1.9,
      max_drawdown: 0.05, expectancy: 1500 },
  ],
  agent_vs_single: {
    agent_outperforms_best_single: true, best_single_strategy: "ORB",
    best_single_profit_factor: 1.6, agent_profit_factor: 1.9, agent_edge: 0.3,
  },
};

const _OQ = {
  status: "OK", order_count: 30, order_failure_rate: 0.1, rejected_rate: 0.07,
  partial_fill_rate: 0.13, fill_rate: 0.8, avg_slippage_bps: 18.0,
  blocked_reasons_top: [
    { reason: "INSUFFICIENT_CASH", count: 4 },
    { reason: "RISK_FLAGS_EXCEEDED", count: 2 },
  ],
};

function _api({ perf = _PERF, oq = _OQ } = {}) {
  return {
    agentStrategyPerformance: vi.fn(async () => perf),
    agentOrderQualityMetrics: vi.fn(async () => oq),
  };
}

describe("<PerformanceMetricsSummary>", () => {
  it("전략별 승률/손익비/PF/MDD/expectancy 표시", async () => {
    render(<PerformanceMetricsSummary apiClient={_api()} />);
    await screen.findByTestId("metrics-strategy-ORB");
    expect(screen.getByTestId("metrics-winrate-ORB").textContent).toContain("55");
    expect(screen.getByTestId("metrics-payoff-ORB").textContent).toContain("1.80");
    expect(screen.getByTestId("metrics-pf-ORB").textContent).toContain("1.60");
    expect(screen.getByTestId("metrics-mdd-ORB").textContent).toContain("8");
    expect(screen.getByTestId("metrics-expectancy-ORB").textContent).toContain("1200");
  });

  it("체결 실패율/거절률/부분체결률 표시", async () => {
    render(<PerformanceMetricsSummary apiClient={_api()} />);
    expect((await screen.findByTestId("metrics-failure-rate")).textContent).toContain("10");
    expect(screen.getByTestId("metrics-rejected-rate").textContent).toContain("7");
    expect(screen.getByTestId("metrics-partial-rate").textContent).toContain("13");
  });

  it("차단 사유 TOP 표시", async () => {
    render(<PerformanceMetricsSummary apiClient={_api()} />);
    expect((await screen.findByTestId("metrics-blocked-INSUFFICIENT_CASH")).textContent)
      .toContain("INSUFFICIENT_CASH");
    expect(screen.getByTestId("metrics-blocked-RISK_FLAGS_EXCEEDED").textContent).toContain("2건");
  });

  it("Agent Council vs best single 표시", async () => {
    render(<PerformanceMetricsSummary apiClient={_api()} />);
    const t = (await screen.findByTestId("metrics-agent-vs-single")).textContent;
    expect(t).toContain("Agent Council");
    expect(t).toContain("ORB");
  });

  it("표시 전용/수익 보장 아님 문구", async () => {
    render(<PerformanceMetricsSummary apiClient={_api()} />);
    const intro = (await screen.findByTestId("metrics-intro")).textContent;
    expect(intro).toContain("분석/표시 전용");
    expect(intro).toContain("수익을 보장하지 않습니다");
  });

  it("blocked 비어있으면 안내", async () => {
    render(<PerformanceMetricsSummary apiClient={_api({ oq: { ..._OQ, blocked_reasons_top: [] } })} />);
    expect((await screen.findByTestId("metrics-blocked-empty")).textContent).toContain("기록 없음");
  });

  it("주문/실전/승인 버튼 0개, 입력 form 0개", async () => {
    const { container } = render(<PerformanceMetricsSummary apiClient={_api()} />);
    await screen.findByTestId("metrics-strategy-ORB");
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
    for (const f of ["지금 매수", "지금 매도", "Place Order", "실거래 시작", "승인하기"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<PerformanceMetricsSummary apiClient={_api()} />);
    await screen.findByTestId("metrics-strategy-ORB");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });
});
