import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AIExecutionGateCard } from "./AIExecutionGateCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    aiExecutionGateEvaluate: vi.fn(),
    aiExecutionGatePolicy: vi.fn(),
  },
}));


const _READY_RESULT = {
  verdict: "READY_FOR_REVIEW",
  blocked_criteria: [],
  cautions: [],
  required_actions: [],
  metrics: {
    promotion_gate_passed: true,
    paper_gate_passed: true,
    ai_assist_gate_passed: true,
    live_manual_gate_passed: true,
    user_explicit_opt_in: true,
    risk_manager_active: true,
    order_guard_active: true,
    ai_permission_gate_active: true,
    audit_log_complete: true,
    kill_switch_ready: true,
    circuit_breaker_configured: true,
    current_max_order_notional_krw: 20000,
    current_max_daily_loss_krw: 4000,
    current_max_daily_order_count: 8,
    current_max_open_positions: 2,
    allowed_symbols_count: 2,
    window_start_kst: "09:30:00",
    window_end_kst:   "14:30:00",
  },
};


const _BLOCKED_RESULT = {
  verdict: "BLOCKED",
  blocked_criteria: [
    "운영자 명시 opt-in 필요 — 자동 활성화 절대 금지.",
    "Paper Gate(#72) 미통과.",
  ],
  cautions: [],
  required_actions: [
    "`scripts/evaluate_paper_gate.py` 통과.",
  ],
  metrics: {
    promotion_gate_passed: false,
    paper_gate_passed: false,
    ai_assist_gate_passed: false,
    live_manual_gate_passed: false,
    user_explicit_opt_in: false,
    risk_manager_active: true,
    order_guard_active: true,
    ai_permission_gate_active: true,
    audit_log_complete: true,
    kill_switch_ready: false,
    circuit_breaker_configured: false,
    current_max_order_notional_krw: 0,
    current_max_daily_loss_krw: 0,
    current_max_daily_order_count: 0,
    current_max_open_positions: 0,
    allowed_symbols_count: 0,
    window_start_kst: null,
    window_end_kst: null,
  },
};


afterEach(cleanup);


describe("AIExecutionGateCard", () => {
  it("READY_FOR_REVIEW 스냅샷에서 '활성화 검토 가능' 배지 노출", () => {
    const { getByTestId } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const badge = getByTestId("ai-execution-verdict-READY_FOR_REVIEW");
    expect(badge.textContent).toBe("활성화 검토 가능");
  });

  it("BLOCKED 스냅샷에서 차단 사유 + 필요 조치 노출", () => {
    const { getByTestId } = render(
      <AIExecutionGateCard resultOverride={_BLOCKED_RESULT} />,
    );
    expect(getByTestId("ai-execution-verdict-BLOCKED")).toBeTruthy();
    expect(getByTestId("ai-execution-blocked-list")).toBeTruthy();
    expect(getByTestId("ai-execution-actions")).toBeTruthy();
  });

  it("고지 문구 (이 화면은 활성화 평가만 하며 실제 모드를 켜지 않습니다) 영구 노출", () => {
    const { getByTestId, rerender } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    let disc = getByTestId("ai-execution-disclaimer").textContent;
    expect(disc).toContain("활성화");
    expect(disc).toContain("실제 모드를 켜지 않습니다");

    rerender(<AIExecutionGateCard resultOverride={_BLOCKED_RESULT} />);
    disc = getByTestId("ai-execution-disclaimer").textContent;
    expect(disc).toContain("활성화");
    expect(disc).toContain("실제 모드를 켜지 않습니다");
  });

  it("선물 영구 차단 banner 노출 (futures_allowed=false)", () => {
    const { getByTestId } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const fb = getByTestId("ai-execution-futures-banner");
    expect(fb.textContent).toContain("선물 AI Execution");
    expect(fb.textContent).toContain("영구");
    expect(fb.textContent).toContain("futures_allowed=false");
  });

  it("AI 자동매매 활성화 / ENABLE_AI_EXECUTION 토글 라벨 버튼 0개", () => {
    const { container } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);  // 평가 버튼 1개만
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "AI 자동매매 켜기",
        "AI 자동매매 시작",
        "AI 자동매매 활성화",
        "LIVE_AI_EXECUTION 활성화",
        "ENABLE_AI_EXECUTION",
        "주문 시작",
        "Place Order",
        "실거래 활성화",
        "활성화 토글",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("BUY/SELL/HOLD 주문 신호 문구 0건", () => {
    const { container } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("평가 버튼 라벨이 '활성화 검토 평가'", () => {
    const { getByTestId } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const btn = getByTestId("ai-execution-evaluate-btn");
    expect(btn.textContent.trim()).toBe("활성화 검토 평가");
  });

  it("극소액 한도 / 종목 수 / 거래 시간이 카드에 노출", () => {
    const { getByTestId } = render(
      <AIExecutionGateCard resultOverride={_READY_RESULT} />,
    );
    const limits = getByTestId("ai-execution-limits");
    expect(limits.textContent).toContain("20,000");        // 1회 주문
    expect(limits.textContent).toContain("4,000");         // 일일 손실
    expect(limits.textContent).toContain("8 건");           // 일일 주문 수
    expect(limits.textContent).toContain("2 개");           // 보유
    expect(limits.textContent).toContain("09:30:00");      // 시작
    expect(limits.textContent).toContain("14:30:00");      // 종료
  });
});
