/**
 * #52 / 6-07 — AgentDecisionExplanationCard 단위 테스트.
 *
 * lock: entry/counter/exit/risk_flags/risk_veto/sell_reason 표시 + fallback +
 * 표시 전용 문구 + 주문/실전/승인 버튼 0개 + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { AgentDecisionExplanationCard } from "./AgentDecisionExplanationCard";

afterEach(cleanup);

const _BUY_COUNCIL = {
  final_action: "BUY", confidence: 0.72, quality_score: 78,
  selected_strategies: ["MOMENTUM", "VWAP"], market_regime: "TREND_UP",
  reason: "BUY 채택", risk_flags: ["high_volatility"], has_exit_plan: true,
  exit_plan: { stop_loss_pct: 2.0, take_profit_pct: 3.0, risk_reward_ratio: 1.5 },
  risk_veto_result: {}, exit_plan_validation: { valid: true }, sell_reason: {},
  votes: [
    { strategy: "MOMENTUM", signal: "BUY", score: 80, reason: "상승 모멘텀" },
    { strategy: "VWAP", signal: "BUY", score: 70, reason: "VWAP 상회" },
    { strategy: "GAP", signal: "SELL", score: 40, reason: "과열 위험" },
    { strategy: "ORB", signal: "HOLD", score: 30, reason: "range 내" },
  ],
};

describe("<AgentDecisionExplanationCard>", () => {
  it("entry_reason 표시", async () => {
    render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    const t = (await screen.findByTestId("explanation-entry-reason")).textContent;
    expect(t).toContain("MOMENTUM");
    expect(t).toContain("매수");
  });

  it("counter_reason 표시", async () => {
    render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    expect((await screen.findByTestId("explanation-counter-reason")).textContent).toContain("GAP");
  });

  it("exit_plan 표시", async () => {
    render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    const t = (await screen.findByTestId("explanation-exit-plan")).textContent;
    expect(t).toContain("손절 2");
    expect(t).toContain("익절 3");
    expect(t).toContain("RR 1.5");
  });

  it("risk_flags 표시", async () => {
    render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    expect((await screen.findByTestId("explanation-risk-flags")).textContent).toContain("high_volatility");
  });

  it("risk_veto_result 표시 (veto 적용)", async () => {
    const c = { final_action: "HOLD", votes: [],
                risk_veto_result: { veto_applied: true, reason_code: "RISK_FLAGS_EXCEEDED" } };
    render(<AgentDecisionExplanationCard decision={c} />);
    const t = (await screen.findByTestId("explanation-risk-veto")).textContent;
    expect(t).toContain("RiskOfficer veto");
    expect(t).toContain("RISK_FLAGS_EXCEEDED");
  });

  it("sell_reason 표시 (SELL)", async () => {
    const c = { final_action: "SELL", held_position: true,
                votes: [{ strategy: "VWAP", signal: "SELL", score: 60 }],
                sell_reason: { reason_code: "STOP_LOSS", message: "손절가 도달" } };
    render(<AgentDecisionExplanationCard decision={c} />);
    const t = (await screen.findByTestId("explanation-sell-reason")).textContent;
    expect(t).toContain("STOP_LOSS");
  });

  it("fallback 문구 표시 (빈 council)", async () => {
    render(<AgentDecisionExplanationCard decision={{ final_action: "BUY", votes: [
      { strategy: "GAP", signal: "SELL", score: 50 }] }} />);
    expect((await screen.findByTestId("explanation-entry-reason")).textContent).toContain("진입 근거 미기록");
    expect(screen.getByTestId("explanation-exit-plan").textContent).toContain("청산 계획 미기록");
    expect(screen.getByTestId("explanation-risk-flags").textContent).toContain("위험 플래그 없음");
  });

  it("표시 전용/주문 아님 문구 표시", async () => {
    render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    const intro = (await screen.findByTestId("explanation-intro")).textContent;
    expect(intro).toContain("분석/설명 전용");
    expect(intro).toContain("주문 버튼이 아닙니다");
    expect(intro).toContain("실전 전환 승인과 무관");
    expect(intro).toContain("수익을 보장하지 않습니다");
  });

  it("주문/실전/승인 버튼 0개, 입력 form 0개", async () => {
    const { container } = render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    await screen.findByTestId("explanation-entry-reason");
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
    for (const f of ["지금 매수", "지금 매도", "Place Order", "실거래 시작", "승인하기", "주문 실행"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<AgentDecisionExplanationCard decision={_BUY_COUNCIL} />);
    await screen.findByTestId("explanation-entry-reason");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("apiClient 로 최신 episode fetch (decision prop 없음)", async () => {
    const api = {
      agentDecisionEpisodes: vi.fn(async () => ({
        episodes: [{ council: _BUY_COUNCIL, market_summary: { time_phase: "MORNING" } }],
      })),
    };
    render(<AgentDecisionExplanationCard apiClient={api} />);
    await waitFor(() => expect(api.agentDecisionEpisodes).toHaveBeenCalled());
    expect((await screen.findByTestId("explanation-entry-reason")).textContent).toContain("MOMENTUM");
  });
});
