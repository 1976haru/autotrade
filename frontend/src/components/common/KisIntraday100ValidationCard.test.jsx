/**
 * KIS-INTRADAY-100-VALIDATION-01 — KisIntraday100ValidationCard 단위 테스트.
 *
 * lock: user_final_judgement + 한 줄 결론 + 수집/품질/성과/Agent 표시 + empty/error
 * fallback + 매수/매도/실전/자동적용/승인 버튼 0개(새로고침/복사만) + input/textarea 0개
 * + secret 미표시 + 필수 disclaimer("실전 승인 아님"/"수익 보장 아님"/"KIS 주문 API 호출 0건").
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { KisIntraday100ValidationCard } from "./KisIntraday100ValidationCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    generated_at: "2026-05-25T01:00:00+00:00",
    data_source: "KIS_INTRADAY_DAILYCHART",
    requested_symbols: 100,
    collected_symbols: 100,
    collection_failed: 0,
    trading_day_count: 14,
    bar_size_minutes: 5.0,
    pass_count: 98,
    warn_count: 2,
    blocked_count: 0,
    pass_symbols: ["005930", "000660"],
    blocked_symbols: [],
    total_bars: 99000,
    total_trades: 7200,
    median_win_rate: 0.61,
    median_profit_factor: 1.85,
    median_expectancy: 120.0,
    median_mdd: 6.0,
    median_walk_forward_score: 0.0,
    agent_value_summary: "AGENT_UNDERPERFORMS",
    agent_helped_symbols: [],
    agent_hurt_symbols: ["005930"],
    agent_no_trade_symbols: [],
    stress_fail_count: 0,
    stress_overall: "PASS",
    developer_verdict: "RESEARCH_ONLY",
    user_final_judgement: "WORTH_MORE_RESEARCH",
    one_line_conclusion: "연구를 계속할 가치는 있지만 튜닝이 필요합니다.",
    paper_rehearsal_recommended: false,
    top_10_promising_symbols: [{ symbol: "000660", trades: 80, profit_factor: 2.1 }],
    excluded_symbols: [],
    next_actions: ["walk-forward 안정성 재확인", "Paper 모의 100건 + 28거래일 표본 축적"],
    do_not_auto_apply: true,
    is_live_authorization: false,
    broker_order_sent: false,
    order_created: false,
    is_order_signal: false,
    kis_order_api_called: false,
    contains_secret: false,
    no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { kisIntraday100ValidationLatest: vi.fn(async () => report) };
}

describe("<KisIntraday100ValidationCard>", () => {
  it("user_final_judgement + 한 줄 결론 표시", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-judgement");
    expect(screen.getByTestId("kis100-user-judgement").textContent).toContain("WORTH_MORE_RESEARCH");
    expect(screen.getByTestId("kis100-one-liner").textContent).toContain("튜닝이 필요");
  });

  it("수집 요약 표시 (요청/성공/거래일)", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api()} />);
    const c = await screen.findByTestId("kis100-collection");
    expect(c.textContent).toContain("100");
    expect(c.textContent).toContain("14");
  });

  it("품질 PASS + total_trades + 지표 표시", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-metrics");
    expect(screen.getByTestId("kis100-pass").textContent).toContain("98");
    expect(screen.getByTestId("kis100-trades").textContent).toContain("7200");
    expect(screen.getByTestId("kis100-metrics").textContent).toContain("WF");
  });

  it("Agent 효과 표시", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api()} />);
    expect((await screen.findByTestId("kis100-agent")).textContent).toContain("AGENT_UNDERPERFORMS");
  });

  it("상위 종목 표시", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api()} />);
    expect((await screen.findByTestId("kis100-top10")).textContent).toContain("000660");
  });

  it("PROMISING 이면 paper 권고 예", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api(_report({
      user_final_judgement: "PROMISING_FOR_PAPER_TEST",
      one_line_conclusion: "Paper 모의 리허설을 진행해볼 만한 가능성이 있습니다.",
      paper_rehearsal_recommended: true }))} />);
    await screen.findByTestId("kis100-judgement");
    expect(screen.getByTestId("kis100-paper-rec").textContent).toContain("예");
  });

  it("API 실패 fallback", async () => {
    const api = { kisIntraday100ValidationLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<KisIntraday100ValidationCard apiClient={api} />);
    expect(await screen.findByTestId("kis100-error")).toBeTruthy();
  });

  it("available=false empty fallback", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api(_report({
      available: false, user_final_judgement: "DATA_NOT_RELIABLE" }))} />);
    expect(await screen.findByTestId("kis100-empty")).toBeTruthy();
    expect(screen.queryByTestId("kis100-judgement")).toBeNull();
  });

  it("매수/매도/실전/자동적용/승인 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-judgement");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-judgement");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer (실전 승인 아님 / 수익 보장 아님 / KIS 주문 API 호출 0건)", async () => {
    const { container } = render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-judgement");
    const text = container.textContent;
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("수익 보장 아님");
    expect(text).toContain("KIS 주문 API");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<KisIntraday100ValidationCard apiClient={_api()} />);
    await screen.findByTestId("kis100-judgement");
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(container.textContent).not.toMatch(/\b\d{8}-\d{2}\b/);
  });

  it("DATA_NOT_RELIABLE verdict 표시 (available=true)", async () => {
    render(<KisIntraday100ValidationCard apiClient={_api(_report({
      user_final_judgement: "DATA_NOT_RELIABLE",
      one_line_conclusion: "데이터 신뢰성 부족으로 판단할 수 없습니다." }))} />);
    await screen.findByTestId("kis100-judgement");
    expect(screen.getByTestId("kis100-user-judgement").textContent).toContain("DATA_NOT_RELIABLE");
  });
});
