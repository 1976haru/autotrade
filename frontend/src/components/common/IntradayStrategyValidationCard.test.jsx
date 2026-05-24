/**
 * INTRADAY-DATA-01 — IntradayStrategyValidationCard 단위 테스트.
 *
 * lock: intraday_data_used + bar_size + total_trades + verdict + agent_no_trade +
 * win_rate/PF + error/empty fallback + 매수/매도/실전/자동적용/승인 버튼 0개
 * (새로고침/복사만) + input/textarea 0개 + secret 미표시 + 필수 disclaimer.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { IntradayStrategyValidationCard } from "./IntradayStrategyValidationCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    generated_at: "2026-05-24T01:00:00+00:00",
    intraday_data_used: true,
    bar_size_minutes: 5.0,
    symbols_count: 5,
    total_bars: 4680,
    total_days: 12,
    total_trades: 1714,
    pass_symbols: ["005930", "000660", "035420", "035720", "005380"],
    warn_symbols: [],
    blocked_symbols: [],
    per_symbol: [{ symbol: "005930", quality_status: "PASS", trades: 186, included: true }],
    overall_verdict: "RESEARCH_ONLY",
    overall_score: 25.7,
    win_rate: 0.98,
    profit_factor: 1342.99,
    expectancy: 1383.08,
    max_drawdown: -120,
    walk_forward_score: 0.0,
    stress_score: null,
    agent_value_score: 10.0,
    agent_value_summary: "AGENT_UNDERPERFORMS",
    agent_no_trade_symbols: [],
    agent_helped_symbols: [],
    agent_hurt_symbols: ["005930"],
    paper_sample_class: "PAPER_NO_TRADES_YET",
    next_steps: ["Paper 모의 100건 + 28거래일 표본 확보 전까지 실전 검토 불가"],
    do_not_auto_apply: true,
    auto_apply_allowed: false,
    is_live_authorization: false,
    is_order_signal: false,
    contains_secret: false,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { intradayStrategyValidationLatest: vi.fn(async () => report) };
}

describe("<IntradayStrategyValidationCard>", () => {
  it("verdict + total_trades 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    const v = await screen.findByTestId("intraday-verdict");
    expect(v.textContent).toContain("RESEARCH_ONLY");
    expect(v.textContent).toContain("1714");
  });

  it("intraday_data_used + bar_size 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    const d = await screen.findByTestId("intraday-data");
    expect(screen.getByTestId("intraday-used").textContent).toContain("예");
    expect(d.textContent).toContain("5");
    expect(d.textContent).toContain("PASS 5");
  });

  it("win_rate / PF / WF 지표 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    const m = await screen.findByTestId("intraday-metrics");
    expect(m.textContent).toContain("0.98");
    expect(m.textContent).toContain("WF");
  });

  it("agent 효과 + 무진입 종목 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api(_report({
      agent_no_trade_symbols: ["035720"] }))} />);
    const a = await screen.findByTestId("intraday-agent");
    expect(a.textContent).toContain("AGENT_UNDERPERFORMS");
    expect(screen.getByTestId("intraday-no-trade").textContent).toContain("035720");
  });

  it("next steps 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    expect((await screen.findByTestId("intraday-next-steps")).textContent).toContain("Paper");
  });

  it("API 실패 fallback", async () => {
    const api = { intradayStrategyValidationLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<IntradayStrategyValidationCard apiClient={api} />);
    expect(await screen.findByTestId("intraday-error")).toBeTruthy();
  });

  it("빈 응답 fallback", async () => {
    const api = { intradayStrategyValidationLatest: vi.fn(async () => null) };
    render(<IntradayStrategyValidationCard apiClient={api} />);
    expect(await screen.findByTestId("intraday-empty")).toBeTruthy();
  });

  it("매수/매도/실전/자동적용/승인 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea 0개", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer (분봉 데이터 기준 / 자동 적용 아님 / 실전 승인 아님 / 수익 보장 아님)", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    const text = container.textContent;
    expect(text).toContain("분봉 데이터 기준");
    expect(text).toContain("자동 적용 아님");
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("수익 보장 아님");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(container.textContent).not.toMatch(/\b\d{8}-\d{2}\b/);
  });

  it("BLOCKED verdict 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api(_report({ overall_verdict: "BLOCKED" }))} />);
    expect((await screen.findByTestId("intraday-verdict")).textContent).toContain("BLOCKED");
  });
});

// INTRADAY-DATA-02: 데이터 소스 상태(경로/입력모드/KIS placeholder) 표시.
function _apiWithSource(report = _report(), source = {
  standard_input_dir: "data/market/intraday_ohlcv",
  csv_file_count: 0,
  input_mode: "KIS_PLACEHOLDER",
  kis_collector: { status: "NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION", can_collect: false },
}) {
  return {
    intradayStrategyValidationLatest: vi.fn(async () => report),
    intradayDataSourceStatus: vi.fn(async () => source),
  };
}

describe("<IntradayStrategyValidationCard> data source (INTRADAY-DATA-02)", () => {
  it("데이터 경로 + 입력 모드 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_apiWithSource()} />);
    const ds = await screen.findByTestId("intraday-data-source");
    expect(ds.textContent).toContain("data/market/intraday_ohlcv");
    expect(screen.getByTestId("intraday-input-mode").textContent).toContain("KIS_PLACEHOLDER");
  });

  it("KIS NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION 안내 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_apiWithSource()} />);
    await screen.findByTestId("intraday-data-source");
    expect(screen.getByTestId("intraday-kis-status").textContent)
      .toContain("NEEDS_OFFICIAL_ENDPOINT_CONFIRMATION");
    expect(screen.getByTestId("intraday-kis-note")).toBeTruthy();
  });

  it("data-source 상태 있어도 주문/실전/승인 버튼 0개", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_apiWithSource()} />);
    await screen.findByTestId("intraday-data-source");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
  });

  it("data-source 상태 없을 때(legacy api) 블록 미표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    expect(screen.queryByTestId("intraday-data-source")).toBeNull();
  });
});

// REAL-INTRADAY-TEST-01: 사용자 최종 판단(user_final_judgement) headline 표시.
function _apiWithFinal(report = _report(), final = {
  user_final_judgement: "WORTH_MORE_RESEARCH",
  one_line_conclusion: "연구를 계속할 가치는 있지만 튜닝이 필요합니다.",
  actual_data_used: true,
  total_trades: 4719,
  paper_rehearsal_recommended: false,
  developer_verdict: "RESEARCH_ONLY",
}) {
  return {
    intradayStrategyValidationLatest: vi.fn(async () => report),
    realIntradayFinalResult: vi.fn(async () => final),
  };
}

describe("<IntradayStrategyValidationCard> final judgement (REAL-INTRADAY-TEST-01)", () => {
  it("user_final_judgement + 한 줄 결론 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_apiWithFinal()} />);
    const j = await screen.findByTestId("intraday-final-judgement");
    expect(screen.getByTestId("intraday-user-judgement").textContent).toContain("WORTH_MORE_RESEARCH");
    expect(screen.getByTestId("intraday-one-liner").textContent).toContain("튜닝이 필요");
    expect(j.textContent).toContain("실전 승인 아님");
  });

  it("actual_data_used + paper 권고 표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_apiWithFinal()} />);
    await screen.findByTestId("intraday-final-judgement");
    expect(screen.getByTestId("intraday-actual-data").textContent).toContain("예");
    expect(screen.getByTestId("intraday-paper-rec").textContent).toContain("아니오");
  });

  it("PROMISING 이면 paper 권고 예", async () => {
    render(<IntradayStrategyValidationCard apiClient={_apiWithFinal(_report(), {
      user_final_judgement: "PROMISING_FOR_PAPER_TEST",
      one_line_conclusion: "Paper 모의 리허설을 진행해볼 만한 가능성이 있습니다.",
      actual_data_used: true, total_trades: 500, paper_rehearsal_recommended: true,
      developer_verdict: "CAUTIOUS_CANDIDATE" })} />);
    await screen.findByTestId("intraday-final-judgement");
    expect(screen.getByTestId("intraday-paper-rec").textContent).toContain("예");
  });

  it("최종 판정 있어도 주문/실전/승인 버튼 0개", async () => {
    const { container } = render(<IntradayStrategyValidationCard apiClient={_apiWithFinal()} />);
    await screen.findByTestId("intraday-final-judgement");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
  });

  it("final 없으면(legacy) headline 미표시", async () => {
    render(<IntradayStrategyValidationCard apiClient={_api()} />);
    await screen.findByTestId("intraday-verdict");
    expect(screen.queryByTestId("intraday-final-judgement")).toBeNull();
  });
});
