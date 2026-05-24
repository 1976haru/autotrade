/**
 * STRATEGY-VALIDATION-01 — StrategyPotentialReportCard 단위 테스트.
 *
 * lock: verdict + overall score + 7 sub-score + sample fixture 배지 + 강점/약점/
 * 리스크/다음 단계 + error fallback + empty fallback + 자동적용/실전/매수/매도/승인
 * 버튼 0개 (새로고침/복사만) + input/textarea 0개 + secret 미표시 + 필수 disclaimer.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { StrategyPotentialReportCard } from "./StrategyPotentialReportCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    generated_at: "2026-05-24T01:00:00+00:00",
    overall_verdict: "RESEARCH_ONLY",
    overall_strategy_potential_score: 48.0,
    backtest_score: 75.0,
    walk_forward_score: 0.0,
    stress_resilience_score: 79.2,
    paper_execution_score: null,
    agent_value_score: 10.0,
    risk_control_score: 100.0,
    data_sufficiency_score: 36.7,
    paper_sample_class: "PAPER_NO_TRADES_YET",
    agent_value_verdict: "AGENT_UNDERPERFORMS",
    strengths: ["백테스트 핵심 지표(승률/PF/expectancy) 양호"],
    weaknesses: ["Paper 표본 부족(PAPER_NO_TRADES_YET) — 수익성 판단 불가"],
    risks: ["sample fixture 결과 — 수익성 판정에 낮은 가중치 (실데이터 검증 필요)"],
    recommended_next_steps: ["실/준실제 OHLCV 데이터로 백테스트 + walk-forward 재실행"],
    method_fit: {},
    sample_fixture_only: true,
    do_not_auto_apply: true,
    auto_apply_allowed: false,
    is_live_authorization: false,
    is_order_signal: false,
    contains_secret: false,
    disclaimer: "본 리포트는 전략 가능성 평가 자료이며 자동 적용/실전 전환 승인/주문 신호가 아니다.",
    ...overrides,
  };
}

function _api(report = _report()) {
  return { strategyPotential: vi.fn(async () => report) };
}

describe("<StrategyPotentialReportCard>", () => {
  it("종합 판정 + overall score 표시", async () => {
    render(<StrategyPotentialReportCard apiClient={_api()} />);
    const v = await screen.findByTestId("strategy-potential-verdict");
    expect(v.textContent).toContain("RESEARCH_ONLY");
    expect(screen.getByTestId("strategy-potential-overall-score").textContent).toContain("48.0");
  });

  it("7개 sub-score 표시 (None 은 평가불가)", async () => {
    render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-scores");
    expect(screen.getByTestId("strategy-potential-score-backtest_score").textContent).toContain("75.0");
    expect(screen.getByTestId("strategy-potential-score-paper_execution_score").textContent).toContain("평가불가");
    expect(screen.getByTestId("strategy-potential-score-risk_control_score").textContent).toContain("100.0");
  });

  it("sample fixture 배지 표시", async () => {
    render(<StrategyPotentialReportCard apiClient={_api()} />);
    expect(await screen.findByTestId("strategy-potential-fixture-badge")).toBeTruthy();
  });

  it("강점/약점/리스크/다음 단계 표시", async () => {
    render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-strengths");
    expect(screen.getByTestId("strategy-potential-weaknesses").textContent).toContain("Paper 표본 부족");
    expect(screen.getByTestId("strategy-potential-risks").textContent).toContain("sample fixture");
    expect(screen.getByTestId("strategy-potential-next-steps").textContent).toContain("재실행");
  });

  it("paper sample + agent value 판정 표시", async () => {
    render(<StrategyPotentialReportCard apiClient={_api()} />);
    const el = await screen.findByTestId("strategy-potential-paper-sample");
    expect(el.textContent).toContain("PAPER_NO_TRADES_YET");
    expect(el.textContent).toContain("AGENT_UNDERPERFORMS");
  });

  it("API 실패 시 fallback (화면 안 깨짐)", async () => {
    const api = { strategyPotential: vi.fn(async () => { throw new Error("boom"); }) };
    render(<StrategyPotentialReportCard apiClient={api} />);
    expect(await screen.findByTestId("strategy-potential-error")).toBeTruthy();
  });

  it("빈 응답 fallback", async () => {
    const api = { strategyPotential: vi.fn(async () => null) };
    render(<StrategyPotentialReportCard apiClient={api} />);
    expect(await screen.findByTestId("strategy-potential-empty")).toBeTruthy();
  });

  it("자동적용/실전/매수/매도/승인 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["리포트 새로고침", "결과 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기|승인 요청/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea 0개", async () => {
    const { container } = render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer 문구 노출 (자동 적용 안 됨 / 실전 승인 아님 / 수익 보장 아님)", async () => {
    const { container } = render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-verdict");
    const text = container.textContent;
    expect(text).toContain("자동 적용 안 됨");
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("수익 보장 아님");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<StrategyPotentialReportCard apiClient={_api()} />);
    await screen.findByTestId("strategy-potential-verdict");
    const text = container.textContent;
    expect(text).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(text).not.toMatch(/\b\d{8}-\d{2}\b/);
  });

  it("BLOCKED verdict 표시", async () => {
    render(<StrategyPotentialReportCard apiClient={_api(_report({ overall_verdict: "BLOCKED" }))} />);
    expect((await screen.findByTestId("strategy-potential-verdict")).textContent).toContain("BLOCKED");
  });
});
