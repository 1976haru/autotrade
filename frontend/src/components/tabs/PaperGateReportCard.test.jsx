import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PaperGateReportCard } from "./PaperGateReportCard";

const _SAMPLE = {
  report_id: "pgr-abc123",
  created_at: "2026-05-23T00:00:00+00:00",
  trading_days: 15,
  period: { start_date: "2026-05-01", end_date: "2026-05-22" },
  sample: {
    total_decisions: 140, total_orders: 120, filled_orders: 118,
    evaluated_trades: 118, meets_100_sample: true, meets_min_trading_days: true,
  },
  performance: {
    win_rate: 0.68, average_return: 0.55, average_win: 0.9, average_loss: -0.4,
    payoff_ratio: 2.25, profit_factor: 1.9, max_drawdown: 0.08,
    max_consecutive_losses: 2, expectancy: 0.4,
  },
  strategy_performance: {
    strategies: [
      { strategy: "MOMENTUM", evaluated_count: 60, win_rate: 0.7,
        profit_factor: 2.1, max_drawdown: 0.06 },
      { strategy: "VWAP", evaluated_count: 40, win_rate: 0.65,
        profit_factor: 1.7, max_drawdown: 0.1 },
      { strategy: "AGENT_COUNCIL", evaluated_count: 118, win_rate: 0.68,
        profit_factor: 1.9, max_drawdown: 0.08 },
    ],
  },
  blocked_reasons_top: [
    { reason_code: "NO_STRATEGY_SIGNAL", count: 14 },
    { reason_code: "PRICE_STALE", count: 6 },
  ],
  order_quality: {
    by_order_status: { FILLED: 118, REJECTED: 2 },
    avg_latency_ms: 312, avg_slippage_bps: 6.5, rejected_count: 2, partial_fill_count: 1,
  },
  sell_reason_performance: {
    STOP_LOSS: { count: 8, evaluated: 8, average_return: 0.6, win_rate: 1.0 },
  },
  review_summary: { by_grade: { GOOD: 80, BAD: 38 }, by_tag: { GOOD_DECISION: 80 } },
  portfolio_integrity: { checked: 120, mismatches: 0, status: "OK" },
  readiness: {
    grade: "READY_FOR_SMALL_LIVE_CANARY_REVIEW", can_review_live_canary: true,
    reasons: ["소액 실전(canary) 검토 가능 — 단, 별도 수동 승인 + Live 자금 검토 필요."],
  },
  required_actions: ["소액 실전(canary) 검토 가능 — 단, 별도 수동 승인 + Live 자금 검토 필요."],
  contains_secret: false,
  uses_real_account_balance: false,
  auto_live_promotion: false,
  is_order_signal: false,
  is_live_authorization: false,
};

function _api(payload = _SAMPLE) {
  return { agentPaperGateReport: vi.fn(async () => payload) };
}

describe("<PaperGateReportCard>", () => {
  afterEach(cleanup);

  it("최종 등급 + 표본 + 100건 충족 표시", async () => {
    render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("pgr-grade")).toBeTruthy());
    expect(screen.getByTestId("pgr-grade").textContent).toMatch(/READY_FOR_SMALL_LIVE_CANARY_REVIEW/);
    const s = screen.getByTestId("pgr-sample").textContent;
    expect(s).toMatch(/평가 118건/);
    expect(s).toMatch(/100건 기준 충족/);
  });

  it("승률 / profit factor / MDD / 연속손실 표시", async () => {
    render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("pgr-performance")).toBeTruthy());
    const t = screen.getByTestId("pgr-performance").textContent;
    expect(t).toMatch(/68\.0%/);   // win_rate
    expect(t).toMatch(/1\.90/);    // profit_factor
    expect(t).toMatch(/8\.0%/);    // mdd
    expect(t).toMatch(/2회/);      // 연속손실
  });

  it("전략별 성과 / 매수불가 사유 TOP / 주문품질 / 포트폴리오 정합성 표시", async () => {
    render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("pgr-strategy-MOMENTUM")).toBeTruthy());
    expect(screen.getByTestId("pgr-blocked").textContent).toMatch(/NO_STRATEGY_SIGNAL/);
    expect(screen.getByTestId("pgr-quality").textContent).toMatch(/거절 2/);
    expect(screen.getByTestId("pgr-integrity").textContent).toMatch(/OK/);
  });

  it("실전 전환 전 필수 보완 표시", async () => {
    render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("pgr-required-actions")).toBeTruthy());
    expect(screen.getByTestId("pgr-required-actions").textContent).toMatch(/수동 승인/);
  });

  it("수익 보장 아님 / 실제 계좌 아님 / 100건 미만 검토 불가 문구", async () => {
    render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("pgr-disclaimer")).toBeTruthy());
    const d = screen.getByTestId("pgr-disclaimer").textContent;
    expect(d).toMatch(/실제 계좌 성과가 아닙니다/);
    expect(d).toMatch(/100건 미만이면 실전 전환 검토 불가/);
    expect(d).toMatch(/수익을 보장하지 않습니다/);
  });

  it("100건 미만이면 canary 불가 표시", async () => {
    const payload = {
      ..._SAMPLE,
      sample: { ..._SAMPLE.sample, evaluated_trades: 40, meets_100_sample: false },
      readiness: { grade: "INSUFFICIENT_SAMPLE", can_review_live_canary: false, reasons: [] },
    };
    render(<PaperGateReportCard apiClient={_api(payload)} />);
    await waitFor(() => expect(screen.getByTestId("pgr-canary")).toBeTruthy());
    expect(screen.getByTestId("pgr-canary").textContent).toMatch(/아니오/);
    expect(screen.getByTestId("pgr-grade").textContent).toMatch(/INSUFFICIENT_SAMPLE/);
  });

  it("LIVE 전환/실전 승인/자동매매 ON/매수·매도 버튼 없음 + secret 표시 없음", async () => {
    const { container } = render(<PaperGateReportCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("paper-gate-report-card")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const b of ["실전 전환 시작", "LIVE 전환", "실전 승인", "자동매매 ON",
                     "지금 매수", "지금 매도", "Place Order",
                     "app_secret", "account_no", "api_key", "수익 보장"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
