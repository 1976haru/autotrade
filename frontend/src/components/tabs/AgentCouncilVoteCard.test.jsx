/**
 * AgentCouncilVoteCard — 4전략 vote + 최종 판단 표시 테스트.
 * 실거래 / 매수 / 매도 / Place Order / ENABLE_* 버튼 0개.
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AgentCouncilVoteCard } from "./AgentCouncilVoteCard";

const _BUY_DECISION = {
  symbol: "005930",
  final_action: "BUY",
  confidence: 0.74,
  quality_score: 80,
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
  exit_plan: { stop_loss_pct: 3.0, take_profit_pct: 6.0 },
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
});
