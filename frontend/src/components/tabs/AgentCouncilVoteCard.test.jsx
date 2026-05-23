/**
 * AgentCouncilVoteCard — 4전략 vote + 최종 판단 표시 테스트.
 * 실거래 / 매수 / 매도 / Place Order / ENABLE_* 버튼 0개.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { AgentCouncilVoteCard } from "./AgentCouncilVoteCard";

const _BUY_DECISION = {
  symbol: "005930",
  final_action: "BUY",
  confidence: 0.74,
  quality_score: 80,
  buy_score: 45.5,
  sell_score: 0,
  hold_score: 14,
  selected_strategies: ["MOMENTUM", "VWAP"],
  risk_profile: "BALANCED",
  risk_flags: [],
  reason: "BUY 채택",
  votes: [
    { strategy: "MOMENTUM", signal: "BUY", confidence: 0.8, score: 82, reason: "상승 모멘텀", risk_flags: [] },
    { strategy: "VWAP", signal: "BUY", confidence: 0.7, score: 76, reason: "VWAP 상회", risk_flags: [] },
    { strategy: "ORB", signal: "HOLD", confidence: 0.3, score: 30, reason: "range 내", risk_flags: [] },
    { strategy: "GAP", signal: "HOLD", confidence: 0.3, score: 30, reason: "갭 미미", risk_flags: [] },
  ],
  has_exit_plan: true,
  exit_plan: {
    entry_price: 75000, stop_loss: 73500, take_profit: 77250,
    risk_reward_ratio: 1.5, exit_strategy: "STOP_LOSS_TAKE_PROFIT",
  },
  exit_plan_validation: { valid: true, reason_code: "EXIT_PLAN_OK" },
  is_live_authorization: false,
};

describe("<AgentCouncilVoteCard>", () => {
  afterEach(cleanup);

  it("decision 없으면 안내 문구", () => {
    render(<AgentCouncilVoteCard decision={null} />);
    expect(screen.getByTestId("council-empty")).toBeTruthy();
  });

  it("4 전략 vote 표시 (ORB/Momentum/Gap/VWAP)", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    for (const s of ["MOMENTUM", "VWAP", "ORB", "GAP"]) {
      expect(screen.getByTestId(`council-vote-${s}`)).toBeTruthy();
    }
  });

  it("Momentum BUY / VWAP BUY / ORB HOLD / Gap HOLD 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-vote-signal-MOMENTUM").textContent).toBe("BUY");
    expect(screen.getByTestId("council-vote-signal-VWAP").textContent).toBe("BUY");
    expect(screen.getByTestId("council-vote-signal-ORB").textContent).toBe("HOLD");
    expect(screen.getByTestId("council-vote-signal-GAP").textContent).toBe("HOLD");
  });

  it("최종 BUY 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-final").getAttribute("data-final-action")).toBe("BUY");
    expect(screen.getByTestId("council-final-action").textContent).toBe("BUY");
  });

  it("최종 HOLD 표시", () => {
    render(<AgentCouncilVoteCard decision={{ ..._BUY_DECISION, final_action: "HOLD", selected_strategies: [] }} />);
    expect(screen.getByTestId("council-final").getAttribute("data-final-action")).toBe("HOLD");
  });

  it("최종 SELL 표시", () => {
    render(<AgentCouncilVoteCard decision={{ ..._BUY_DECISION, final_action: "SELL" }} />);
    expect(screen.getByTestId("council-final-action").textContent).toBe("SELL");
  });

  it("confidence / quality_score 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-confidence").textContent).toMatch(/74%/);
    expect(screen.getByTestId("council-quality-score").textContent).toMatch(/80/);
  });

  it("selected_strategies 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-selected-strategies").textContent).toMatch(/MOMENTUM/);
    expect(screen.getByTestId("council-selected-strategies").textContent).toMatch(/VWAP/);
  });

  it("risk_profile 표시", () => {
    render(<AgentCouncilVoteCard decision={{ ..._BUY_DECISION, risk_profile: "AGGRESSIVE" }} />);
    expect(screen.getByTestId("council-risk-profile").getAttribute("data-risk-profile")).toBe("AGGRESSIVE");
    expect(screen.getByTestId("council-risk-profile").textContent).toMatch(/공격적/);
  });

  it("risk_flags 표시", () => {
    render(<AgentCouncilVoteCard decision={{
      ..._BUY_DECISION, risk_flags: ["high_volatility", "low_volume"],
    }} />);
    expect(screen.getByTestId("council-risk-flags").textContent).toMatch(/high_volatility/);
  });

  it("vote score / reason 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-vote-score-MOMENTUM").textContent).toMatch(/82/);
    expect(screen.getByTestId("council-vote-reason-MOMENTUM").textContent).toMatch(/모멘텀/);
  });

  it("실거래 / 매수 / Place Order / ENABLE_* 버튼·문구 0건", () => {
    const { container } = render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(container.querySelectorAll("button").length).toBe(0);
    const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기"];
    for (const b of banned) expect(container.textContent).not.toContain(b);
    expect(container.textContent).toMatch(/is_live_authorization=false/);
  });

  it("is_live_authorization=false disclaimer 영구", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    expect(screen.getByTestId("council-disclaimer").textContent).toMatch(/is_live_authorization=false/);
    expect(screen.getByTestId("council-badge-advisory").textContent).toMatch(/주문 신호 아님/);
  });

  // ── 2-10 보강 ──

  it("2-10: buy/sell/hold score 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    const t = screen.getByTestId("council-scores").textContent;
    expect(t).toMatch(/buy 45\.5/);
    expect(t).toMatch(/sell 0/);
    expect(t).toMatch(/hold 14/);
  });

  it("2-10: 전략별 confidence / risk_flags 표시", () => {
    render(<AgentCouncilVoteCard decision={{
      ..._BUY_DECISION,
      votes: [
        { strategy: "MOMENTUM", signal: "SELL", confidence: 0.6, score: 70,
          reason: "하락", risk_flags: ["high_volatility"] },
        { strategy: "VWAP", signal: "HOLD", confidence: 0.3, score: 30, reason: "중립", risk_flags: [] },
        { strategy: "ORB", signal: "HOLD", confidence: 0.3, score: 30, reason: "range", risk_flags: [] },
        { strategy: "GAP", signal: "HOLD", confidence: 0.3, score: 30, reason: "갭", risk_flags: [] },
      ],
    }} />);
    expect(screen.getByTestId("council-vote-conf-MOMENTUM").textContent).toMatch(/60%/);
    expect(screen.getByTestId("council-vote-flags-MOMENTUM").textContent).toMatch(/high_volatility/);
  });

  it("2-10: reason 없으면 '사유 미기록' 표시", () => {
    render(<AgentCouncilVoteCard decision={{
      ..._BUY_DECISION,
      votes: [{ strategy: "ORB", signal: "HOLD", confidence: 0.3, score: 30, risk_flags: [] }],
    }} />);
    expect(screen.getByTestId("council-vote-reason-ORB").textContent).toMatch(/사유 미기록/);
  });

  it("2-10: RiskOfficer veto 표시", () => {
    render(<AgentCouncilVoteCard decision={{
      ..._BUY_DECISION, final_action: "HOLD",
      risk_veto_result: {
        veto_applied: true, pre_veto_action: "BUY", final_action: "HOLD",
        risk_flag_count: 2, max_risk_flags: 1, risk_profile: "BALANCED",
        reason_code: "RISK_OFFICER_VETO",
      },
    }} />);
    const t = screen.getByTestId("council-risk-veto").textContent;
    expect(t).toMatch(/RiskOfficer veto/);
    expect(t).toMatch(/위험 플래그 2개/);
    expect(t).toMatch(/HOLD 강등/);
  });

  it("2-10: exit_plan(손절/익절) 표시", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    const t = screen.getByTestId("council-exit-plan").textContent;
    expect(t).toMatch(/손절 73,500원/);
    expect(t).toMatch(/익절 77,250원/);
  });

  it("2-10: exit_plan_validation 실패 → BUY 차단 표시", () => {
    render(<AgentCouncilVoteCard decision={{
      ..._BUY_DECISION, final_action: "HOLD", exit_plan: {},
      exit_plan_validation: { valid: false, reason_code: "EXIT_PLAN_MISSING", forced_action: "HOLD" },
    }} />);
    expect(screen.getByTestId("council-exit-plan-veto").textContent)
      .toMatch(/BUY 차단.*EXIT_PLAN_MISSING.*HOLD 강등/);
  });

  it("2-10: 안내 문구(판단 근거 표시용/주문 권한 무관/별도 승인)", () => {
    render(<AgentCouncilVoteCard decision={_BUY_DECISION} />);
    const t = screen.getByTestId("council-disclaimer").textContent;
    expect(t).toMatch(/AI 판단 근거 표시용이며 주문 버튼이 아닙니다/);
    expect(t).toMatch(/실제 주문 권한과/);
    expect(t).toMatch(/실전 전환은 별도 승인 절차/);
  });

  // ── 2-10: 자동 조회(self-fetch) + loading/error/empty ──

  it("2-10: decision 생략 시 최근 episode council 자동 조회", async () => {
    const api = {
      agentDecisionEpisodes: vi.fn(async () => ({
        episodes: [{ episode_id: "ep-x", final_action: "BUY", council: _BUY_DECISION }],
      })),
    };
    render(<AgentCouncilVoteCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("council-final-action")).toBeTruthy());
    expect(api.agentDecisionEpisodes).toHaveBeenCalledWith({ limit: 1 });
    expect(screen.getByTestId("council-final-action").textContent).toBe("BUY");
  });

  it("2-10: 자동 조회 결과 없음 → empty 상태", async () => {
    const api = { agentDecisionEpisodes: vi.fn(async () => ({ episodes: [] })) };
    render(<AgentCouncilVoteCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("council-empty")).toBeTruthy());
  });

  it("2-10: 자동 조회 실패 → error 상태", async () => {
    const api = { agentDecisionEpisodes: vi.fn(async () => { throw new Error("down"); }) };
    render(<AgentCouncilVoteCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("council-error").textContent).toMatch(/down/));
  });

  it("2-10: autoFetch=false + decision 생략 → empty (fetch 안 함)", () => {
    const api = { agentDecisionEpisodes: vi.fn() };
    render(<AgentCouncilVoteCard apiClient={api} autoFetch={false} />);
    expect(screen.getByTestId("council-empty")).toBeTruthy();
    expect(api.agentDecisionEpisodes).not.toHaveBeenCalled();
  });

  it("2-10: 자동 조회 카드에도 매수/매도/실전 버튼 0개", async () => {
    const api = {
      agentDecisionEpisodes: vi.fn(async () => ({
        episodes: [{ episode_id: "ep-x", final_action: "BUY", council: _BUY_DECISION }],
      })),
    };
    const { container } = render(<AgentCouncilVoteCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("council-final-action")).toBeTruthy());
    // 버튼 0개 = 매수/매도/실전 버튼 전부 없음(가장 강한 보장).
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const b of ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "app_secret", "account_no"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
