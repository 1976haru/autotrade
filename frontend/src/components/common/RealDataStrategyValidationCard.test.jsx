/**
 * REAL-DATA-STRATEGY-01 — RealDataStrategyValidationCard 단위 테스트.
 *
 * lock: real_data_used + sample fixture 경고 + scores + verdict + favorable/dangerous +
 * agent helped/hurt + next steps + error/empty fallback + 자동적용/실전/매수/매도/승인
 * 버튼 0개 (새로고침/복사만) + input/textarea 0개 + secret 미표시 + 필수 disclaimer.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RealDataStrategyValidationCard } from "./RealDataStrategyValidationCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    generated_at: "2026-05-24T01:00:00+00:00",
    data_source: "CSV_REAL_FIXTURE",
    real_data_used: true,
    sample_fixture_only: false,
    symbols_count: 1,
    bars_count: 130,
    days_count: 130,
    trades_count: 12,
    quality: { status: "OK", reasons: [] },
    overall_verdict: "CAUTIOUS_CANDIDATE",
    overall_score: 62.5,
    backtest_score: 70.0,
    walk_forward_score: 55.0,
    stress_score: 80.0,
    agent_value_score: 65.0,
    data_sufficiency_score: 50.0,
    agent_value_verdict: "AGENT_ADDS_VALUE",
    paper_sample_class: "PAPER_NO_TRADES_YET",
    favorable_conditions: ["uptrend(win_rate=0.62)"],
    dangerous_conditions: ["downtrend(win_rate=0.35)"],
    strategy_strengths: ["백테스트 핵심 지표 양호"],
    strategy_weaknesses: [],
    agent_helped_where: ["Agent Council 이 단일 전략 대비 수익/리스크 개선"],
    agent_hurt_where: [],
    tuning_candidates: [],
    next_steps: ["Paper 모의 운영으로 100건 + 28거래일 표본 축적"],
    do_not_auto_apply: true,
    auto_apply_allowed: false,
    is_live_authorization: false,
    is_order_signal: false,
    contains_secret: false,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { realDataStrategyValidationLatest: vi.fn(async () => report) };
}

describe("<RealDataStrategyValidationCard>", () => {
  it("verdict + overall score 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    expect((await screen.findByTestId("real-data-verdict")).textContent).toContain("CAUTIOUS_CANDIDATE");
    expect(screen.getByTestId("real-data-overall-score").textContent).toContain("62.5");
  });

  it("real_data_used + data source 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    const src = await screen.findByTestId("real-data-source");
    expect(src.textContent).toContain("CSV_REAL_FIXTURE");
    expect(screen.getByTestId("real-data-used").textContent).toContain("예");
  });

  it("sample fixture 경고 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_report({ sample_fixture_only: true }))} />);
    expect(await screen.findByTestId("real-data-sample-warning")).toBeTruthy();
  });

  it("sample fixture 아니면 경고 미표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    expect(screen.queryByTestId("real-data-sample-warning")).toBeNull();
  });

  it("4 score + 표본 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    const s = await screen.findByTestId("real-data-scores");
    expect(s.textContent).toContain("70.0");
    expect(s.textContent).toContain("AGENT_ADDS_VALUE");
    expect(screen.getByTestId("real-data-sample").textContent).toContain("130");
  });

  it("favorable/dangerous + agent helped 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-favorable");
    expect(screen.getByTestId("real-data-dangerous").textContent).toContain("downtrend");
    expect(screen.getByTestId("real-data-agent-helped").textContent).toContain("개선");
  });

  it("agent underperforms (hurt) 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_report({
      agent_value_verdict: "AGENT_UNDERPERFORMS", agent_helped_where: [],
      agent_hurt_where: ["Agent Council 이 best single 전략보다 성과 저조"] }))} />);
    expect((await screen.findByTestId("real-data-agent-hurt")).textContent).toContain("저조");
  });

  it("next steps 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    expect((await screen.findByTestId("real-data-next-steps")).textContent).toContain("Paper");
  });

  it("API 실패 fallback", async () => {
    const api = { realDataStrategyValidationLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<RealDataStrategyValidationCard apiClient={api} />);
    expect(await screen.findByTestId("real-data-error")).toBeTruthy();
  });

  it("빈 응답 fallback", async () => {
    const api = { realDataStrategyValidationLatest: vi.fn(async () => null) };
    render(<RealDataStrategyValidationCard apiClient={api} />);
    expect(await screen.findByTestId("real-data-empty")).toBeTruthy();
  });

  it("자동적용/실전/매수/매도/승인 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기|승인 요청/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea 0개", async () => {
    const { container } = render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer (자동 적용 안 됨 / 실전 승인 아님 / 수익 보장 아님)", async () => {
    const { container } = render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    const text = container.textContent;
    expect(text).toContain("자동 적용 안 됨");
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("수익 보장 아님");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(container.textContent).not.toMatch(/\b\d{8}-\d{2}\b/);
  });

  it("BLOCKED verdict 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_report({ overall_verdict: "BLOCKED" }))} />);
    expect((await screen.findByTestId("real-data-verdict")).textContent).toContain("BLOCKED");
  });
});

// REAL-DATA-INPUT-01: 다종목 dataset 리포트(pass/blocked symbols + aggregate) 표시.
function _datasetReport(overrides = {}) {
  return {
    ..._report(),
    pass_symbols: ["005930", "000660", "035420"],
    blocked_symbols: ["005490"],
    warn_symbols: [],
    per_symbol: [{ symbol: "005930", quality_status: "PASS", trades: 4, included: true }],
    total_trades: 12,
    median_profit_factor: 1.4,
    median_walk_forward_score: 55.0,
    agent_value_summary: "AGENT_MIXED",
    ...overrides,
  };
}

describe("<RealDataStrategyValidationCard> dataset (REAL-DATA-INPUT-01)", () => {
  it("pass_symbols 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_datasetReport())} />);
    const el = await screen.findByTestId("real-data-pass-symbols");
    expect(el.textContent).toContain("005930");
    expect(el.textContent).toContain("3");
  });

  it("blocked_symbols (품질 FAIL) 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_datasetReport())} />);
    expect((await screen.findByTestId("real-data-blocked-symbols")).textContent).toContain("005490");
  });

  it("aggregate (total_trades / median PF / agent summary) 표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api(_datasetReport())} />);
    const agg = await screen.findByTestId("real-data-aggregate");
    expect(agg.textContent).toContain("12");
    expect(agg.textContent).toContain("1.4");
    expect(agg.textContent).toContain("AGENT_MIXED");
  });

  it("단일 리포트(non-dataset)면 pass/blocked 블록 미표시", async () => {
    render(<RealDataStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("real-data-verdict");
    expect(screen.queryByTestId("real-data-pass-symbols")).toBeNull();
  });

  it("dataset 에서도 주문/실전/승인 버튼 0개", async () => {
    const { container } = render(<RealDataStrategyValidationCard apiClient={_api(_datasetReport())} />);
    await screen.findByTestId("real-data-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
  });
});
