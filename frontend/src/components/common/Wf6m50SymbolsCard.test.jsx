/**
 * WF-6M-50SYMBOLS-01 — Wf6m50SymbolsCard 단위 테스트.
 *
 * lock: verdict/실전 가능성 + 자금곡선 + 등급 + 전략 생존 + empty/error fallback +
 * 매수/매도/실전/자동적용/승인 버튼 0개(새로고침/복사만) + input/textarea 0개 +
 * secret 미표시 + 필수 disclaimer.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Wf6m50SymbolsCard } from "./Wf6m50SymbolsCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    data_source: "KIS_INTRADAY_DAILYCHART_5M",
    symbols_count: 50,
    pass_symbols: 49,
    trading_days: 124,
    total_bars: 480000,
    bar_size_minutes: 5.0,
    enough_history: true,
    initial_capital: 10000000,
    final_equity: 10620000,
    total_return_pct: 6.2,
    daily_avg_trades: 4.1,
    daily_avg_return_pct: 0.05,
    weekly_avg_return_pct: 0.25,
    monthly_avg_return_pct: 1.03,
    win_rate: 0.54,
    payoff_ratio: 1.2,
    expectancy: 3400,
    profit_factor: 1.18,
    max_drawdown_pct: 12.4,
    worst_day_pnl: -180000,
    worst_consecutive_losses: 6,
    avg_hold_minutes: 95.0,
    skipped_max_positions: 5200,
    monthly_returns: { "2026-01": 1.2, "2026-02": -0.8 },
    equity_curve_points: 124,
    grades: { GO: 2, WATCH: 18, TUNE: 25, EXCLUDE: 4 },
    top_10: [{ symbol: "005930", net_pnl: 120000 }, { symbol: "000660", net_pnl: 90000 }],
    bottom_10: [{ symbol: "035720", net_pnl: -80000 }],
    exclude_recommended: ["035720"],
    agent_helped_symbols: ["005930"],
    agent_hurt_symbols: ["000660", "035420"],
    strategy_survival: { ORB: { expectancy: 0.1 } },
    strategy_alive: ["ORB", "VWAP"],
    strategy_dead: ["GAP"],
    council_better_than_best_single: false,
    regime_buckets: {},
    time_phase_buckets: {},
    median_walk_forward_score: 0.0,
    agent_value_summary: "AGENT_UNDERPERFORMS",
    final_verdict: "WORTH_MORE_RESEARCH",
    live_possibility: "튜닝 필요",
    strengths_top3: ["비용 후 양(+6.2%)", "승률 54%"],
    weaknesses_top3: ["walk-forward 0.0 (과최적화 의심)", "Agent 방해 > 도움"],
    agent_optimal_role: "위험 필터(진입 억제) 중심 — 나쁜 진입 차단 역할 권장",
    upgrade_directions: ["walk-forward 안정화 우선", "종목 선별 레이어 추가"],
    expansion_assessment: { "키움 확장": "멀티브로커 어댑터로 가능" },
    next_steps: ["walk-forward 안정화가 최우선"],
    reasons: ["walk-forward 0.0 < 40 — 과최적화 의심"],
    do_not_auto_apply: true,
    is_live_authorization: false,
    broker_order_sent: false,
    order_created: false,
    contains_secret: false,
    no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { wf6m50SymbolsLatest: vi.fn(async () => report) };
}

describe("<Wf6m50SymbolsCard>", () => {
  it("final_verdict + 실전 가능성 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    expect(screen.getByTestId("wf6m-final-verdict").textContent).toContain("WORTH_MORE_RESEARCH");
    expect(screen.getByTestId("wf6m-live-possibility").textContent).toContain("튜닝 필요");
  });

  it("1000만원 자금곡선 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api()} />);
    const c = await screen.findByTestId("wf6m-capital");
    expect(c.textContent).toContain("KRW");
    expect(screen.getByTestId("wf6m-return").textContent).toContain("6.2");
  });

  it("종목 등급 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-grades");
    expect(screen.getByTestId("wf6m-go").textContent).toContain("2");
  });

  it("전략 생존/사망 + Agent 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api()} />);
    const s = await screen.findByTestId("wf6m-strategies");
    expect(s.textContent).toContain("ORB");
    expect(s.textContent).toContain("GAP");
    expect(s.textContent).toContain("AGENT_UNDERPERFORMS");
  });

  it("Top/Bottom + 약점 + 업그레이드 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    expect(screen.getByTestId("wf6m-top10").textContent).toContain("005930");
    expect(screen.getByTestId("wf6m-weaknesses").textContent).toContain("walk-forward");
    expect(screen.getByTestId("wf6m-upgrades").textContent).toContain("walk-forward 안정화");
  });

  it("API 실패 fallback", async () => {
    const api = { wf6m50SymbolsLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Wf6m50SymbolsCard apiClient={api} />);
    expect(await screen.findByTestId("wf6m-error")).toBeTruthy();
  });

  it("available=false empty fallback", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("wf6m-empty")).toBeTruthy();
    expect(screen.queryByTestId("wf6m-verdict")).toBeNull();
  });

  it("매수/매도/실전/자동적용/승인 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer (Paper/Backtest / 실전 승인 아님 / 수익 보장 아님)", async () => {
    const { container } = render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    const text = container.textContent;
    expect(text).toContain("Paper/Backtest");
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("수익을 보장하지");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<Wf6m50SymbolsCard apiClient={_api()} />);
    await screen.findByTestId("wf6m-verdict");
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(container.textContent).not.toMatch(/\b\d{8}-\d{2}\b/);
  });

  it("NOT_RECOMMENDED verdict 표시", async () => {
    render(<Wf6m50SymbolsCard apiClient={_api(_report({
      final_verdict: "NOT_RECOMMENDED", live_possibility: "현재 상태로는 위험" }))} />);
    await screen.findByTestId("wf6m-verdict");
    expect(screen.getByTestId("wf6m-final-verdict").textContent).toContain("NOT_RECOMMENDED");
  });
});
