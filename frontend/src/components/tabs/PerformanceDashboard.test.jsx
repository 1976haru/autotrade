import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PerformanceDashboard } from "./PerformanceDashboard";

const _SAMPLE = {
  status: "OK",
  total_episodes: 8,
  evaluated_episodes: 6,
  strategies: [
    { strategy: "ORB", decision_count: 8, buy_vote_count: 3, sell_vote_count: 1,
      hold_vote_count: 4, selected_count: 2, order_count: 2, filled_count: 2,
      evaluated_count: 2, win_rate: 0.5, average_return: 0.2, average_win: 1.0,
      average_loss: -0.6, payoff_ratio: 1.67, profit_factor: 1.2, max_loss: -0.6,
      max_drawdown: 0.1, expectancy: 0.2, max_consecutive_losses: 1 },
    { strategy: "MOMENTUM", decision_count: 8, buy_vote_count: 4, sell_vote_count: 2,
      hold_vote_count: 2, selected_count: 4, order_count: 4, filled_count: 3,
      evaluated_count: 4, win_rate: 0.75, average_return: 0.9, average_win: 1.4,
      average_loss: -0.5, payoff_ratio: 2.8, profit_factor: 1.9, max_loss: -0.5,
      max_drawdown: 0.08, expectancy: 0.6, max_consecutive_losses: 1 },
    { strategy: "GAP", decision_count: 8, buy_vote_count: 1, sell_vote_count: 3,
      hold_vote_count: 4, selected_count: 1, order_count: 1, filled_count: 1,
      evaluated_count: 1, win_rate: 0.0, average_return: -0.4, average_win: 0.0,
      average_loss: -0.4, payoff_ratio: null, profit_factor: 0.0, max_loss: -0.4,
      max_drawdown: 0.4, expectancy: -0.4, max_consecutive_losses: 1 },
    { strategy: "VWAP", decision_count: 8, buy_vote_count: 2, sell_vote_count: 2,
      hold_vote_count: 4, selected_count: 3, order_count: 3, filled_count: 3,
      evaluated_count: 3, win_rate: 0.67, average_return: 0.5, average_win: 1.1,
      average_loss: -0.7, payoff_ratio: 1.57, profit_factor: 1.4, max_loss: -0.7,
      max_drawdown: 0.12, expectancy: 0.3, max_consecutive_losses: 1 },
    { strategy: "AGENT_COUNCIL", decision_count: 8, buy_vote_count: 0,
      sell_vote_count: 0, hold_vote_count: 0, selected_count: 6, order_count: 6,
      filled_count: 5, evaluated_count: 6, win_rate: 0.67, average_return: 0.7,
      average_win: 1.3, average_loss: -0.55, payoff_ratio: 2.36, profit_factor: 2.1,
      max_loss: -0.55, max_drawdown: 0.1, expectancy: 0.5, max_consecutive_losses: 1 },
  ],
  by_risk_profile: {
    BALANCED: { decision_count: 4, trade_count: 4, win_rate: 0.75,
                average_return: 0.8, profit_factor: 2.0, max_loss: -0.5 },
    AGGRESSIVE: { decision_count: 2, trade_count: 2, win_rate: 0.5,
                  average_return: 0.1, profit_factor: 1.1, max_loss: -0.6 },
  },
  by_market_regime: {
    TREND_UP: { decision_count: 4, trade_count: 4, win_rate: 0.75,
                average_return: 0.9, profit_factor: 2.2, max_loss: -0.4 },
  },
  by_time_phase: {
    MORNING: { decision_count: 3, trade_count: 3, win_rate: 0.67,
               average_return: 0.6, profit_factor: 1.8, max_loss: -0.5 },
    CLOSING: { decision_count: 3, trade_count: 3, win_rate: 0.67,
               average_return: 0.5, profit_factor: 1.5, max_loss: -0.6 },
  },
  agent_vs_single: {
    agent_outperforms_best_single: true, best_single_strategy: "MOMENTUM",
    best_single_profit_factor: 1.9, agent_profit_factor: 2.1, agent_edge: 0.2,
    warning: null,
  },
  contains_secret: false,
  uses_account_balance: false,
  is_order_signal: false,
  is_live_authorization: false,
};

function _api(payload = _SAMPLE) {
  return { agentStrategyPerformance: vi.fn(async () => payload) };
}

describe("<PerformanceDashboard>", () => {
  afterEach(cleanup);

  it("카드 + 분석용 배지 + 실 계좌 아님 문구", async () => {
    render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("performance-dashboard")).toBeTruthy());
    expect(screen.getByTestId("perf-badges").textContent).toMatch(/주문 신호 아님/);
    const d = screen.getByTestId("perf-disclaimer").textContent;
    expect(d).toMatch(/실제 계좌 잔고가 아닙니다/);
    expect(d).toMatch(/Paper Gate/);
  });

  it("5개 전략 행 표시 (ORB/Momentum/Gap/VWAP/Agent Council)", async () => {
    render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("perf-strategy-ORB")).toBeTruthy());
    for (const s of ["ORB", "MOMENTUM", "GAP", "VWAP", "AGENT_COUNCIL"]) {
      expect(screen.getByTestId(`perf-strategy-${s}`)).toBeTruthy();
    }
  });

  it("승률 / profit factor / MDD / 주문·체결 수 표시", async () => {
    render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("perf-strategy-MOMENTUM")).toBeTruthy());
    const t = screen.getByTestId("perf-strategy-MOMENTUM").textContent;
    expect(t).toMatch(/75\.0%/);   // win_rate
    expect(t).toMatch(/1\.90/);    // profit_factor
    expect(t).toMatch(/8\.0%/);    // mdd 0.08
    // decision 8 / selected 4 / order 4 / filled 3 → 셀 순서대로 렌더.
    expect(t).toMatch(/8443/);     // order_count(4) + filled_count(3) 포함.
  });

  it("Agent Council vs 단일 전략 비교 표시", async () => {
    render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("perf-agent-vs-single")).toBeTruthy());
    const t = screen.getByTestId("perf-agent-vs-single").textContent;
    expect(t).toMatch(/Agent Council/);
    expect(t).toMatch(/MOMENTUM/);
    expect(t).toMatch(/우수/);
  });

  it("Agent 열세 시 경고 문구", async () => {
    const payload = {
      ..._SAMPLE,
      agent_vs_single: {
        agent_outperforms_best_single: false, best_single_strategy: "MOMENTUM",
        best_single_profit_factor: 1.9, agent_profit_factor: 1.2, agent_edge: -0.7,
        warning: "Agent Council profit factor(1.2)가 MOMENTUM 단독(1.9)보다 낮습니다.",
      },
    };
    render(<PerformanceDashboard apiClient={_api(payload)} />);
    await waitFor(() => expect(screen.getByTestId("perf-agent-vs-single")).toBeTruthy());
    expect(screen.getByTestId("perf-agent-vs-single").textContent).toMatch(/낮습니다/);
  });

  it("risk_profile / market_regime / 시간대 버킷 표시", async () => {
    render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("perf-by-risk")).toBeTruthy());
    expect(screen.getByTestId("perf-bucket-BALANCED")).toBeTruthy();
    expect(screen.getByTestId("perf-bucket-TREND_UP")).toBeTruthy();
    expect(screen.getByTestId("perf-bucket-MORNING")).toBeTruthy();
    expect(screen.getByTestId("perf-bucket-CLOSING")).toBeTruthy();
  });

  it("데이터 부족 안내", async () => {
    const payload = { ..._SAMPLE, status: "INSUFFICIENT_DATA",
                      total_episodes: 1, evaluated_episodes: 0 };
    render(<PerformanceDashboard apiClient={_api(payload)} />);
    await waitFor(() => expect(screen.getByTestId("perf-insufficient")).toBeTruthy());
    expect(screen.getByTestId("perf-insufficient").textContent).toMatch(/성과 데이터가 부족/);
  });

  it("secret/account 표시 없음 + 실전/매수/매도 버튼 없음", async () => {
    const { container } = render(<PerformanceDashboard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("performance-dashboard")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const b of ["app_secret", "account_no", "api_key", "Place Order",
                     "실거래 시작", "지금 매수", "지금 매도", "LIVE 활성화"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
