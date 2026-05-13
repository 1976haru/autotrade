import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveManualGateCard } from "./LiveManualGateCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    liveManualGateEvaluate: vi.fn(),
    liveManualPeriodSummary: vi.fn(),
  },
}));


const _PASS_RESULT = {
  verdict: "PASS",
  blocked_criteria: [],
  cautions: [],
  required_actions: [],
  metrics: {
    paper_gate_passed: true,
    promotion_gate_passed: true,
    user_explicit_opt_in: true,
    approval_required: true,
    ai_execution_enabled: false,
    futures_live_enabled: false,
    approval_bypass_attempts: 0,
    audit_missing_count: 0,
    current_max_order_notional_krw: 30000,
    current_max_daily_loss_krw: 8000,
    current_max_open_positions: 2,
  },
};


const _BLOCKED_RESULT = {
  verdict: "BLOCKED",
  blocked_criteria: [
    "Paper Gate PASS 필요.",
    "운영자 명시 opt-in 필요.",
  ],
  cautions: [],
  required_actions: [
    "Paper 모드 4주 운용 후 평가.",
  ],
  metrics: {
    paper_gate_passed: false,
    promotion_gate_passed: false,
    user_explicit_opt_in: false,
    approval_required: true,
    ai_execution_enabled: false,
    futures_live_enabled: false,
    approval_bypass_attempts: 0,
    audit_missing_count: 0,
    current_max_order_notional_krw: 0,
    current_max_daily_loss_krw: 0,
    current_max_open_positions: 0,
  },
};


afterEach(cleanup);


describe("LiveManualGateCard", () => {
  it("PASS 스냅샷에서 검토 가능 배지를 노출한다", () => {
    const { getByTestId } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const badge = getByTestId("live-manual-verdict-PASS");
    expect(badge.textContent).toBe("검토 가능");
  });

  it("BLOCKED 스냅샷에서 차단 사유 + 필요 조치를 노출한다", () => {
    const { getByTestId } = render(
      <LiveManualGateCard resultOverride={_BLOCKED_RESULT} />,
    );
    const badge = getByTestId("live-manual-verdict-BLOCKED");
    expect(badge.textContent).toBe("차단됨");
    expect(getByTestId("live-manual-blocked-list")).toBeTruthy();
    expect(getByTestId("live-manual-actions")).toBeTruthy();
  });

  it("위험 문구 (PASS는 실거래 자동 허가가 아니라) 가 *항상* 표시된다", () => {
    const { getByTestId, rerender } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const disclaimer = getByTestId("live-manual-disclaimer");
    expect(disclaimer.textContent).toContain("실거래 자동 허가가 아니라");
    expect(disclaimer.textContent).toContain("초소액 수동승인");

    // BLOCKED 상태에서도 동일.
    rerender(<LiveManualGateCard resultOverride={_BLOCKED_RESULT} />);
    expect(getByTestId("live-manual-disclaimer").textContent).toContain(
      "실거래 자동 허가가 아니라",
    );
  });

  it("'실거래 활성화' 같은 enabling 버튼이 *0개*", () => {
    const { container } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const buttons = container.querySelectorAll("button");
    // 평가 버튼은 하나 있어야 함 — "활성화 가능성 평가".
    expect(buttons.length).toBeGreaterThanOrEqual(1);
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      // 금지 라벨 — 활성화 / 실거래 시작 / LIVE 켜기 / Place Order / 주문 실행.
      for (const banned of [
        "실거래 활성화",
        "실거래 시작",
        "LIVE 켜기",
        "Place Order",
        "주문 실행",
        "ENABLE_LIVE_TRADING",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("주문 신호 (BUY/SELL/HOLD) / 긴급정지 토글 버튼 0개", () => {
    const { container } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY", "SELL", "HOLD",
                          "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴이 화면에 노출되지 않는다", () => {
    const { container } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("극소액 정책 한도가 카드에 명확히 노출된다", () => {
    const { getByTestId } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const limits = getByTestId("live-manual-limits");
    expect(limits.textContent).toContain("30,000");  // 주문 한도
    expect(limits.textContent).toContain("8,000");   // 일일 손실
    expect(limits.textContent).toContain("2 개");    // 보유 종목
  });

  it("평가 버튼 라벨이 '활성화 가능성 평가'", () => {
    const { getByTestId } = render(
      <LiveManualGateCard resultOverride={_PASS_RESULT} />,
    );
    const btn = getByTestId("live-manual-evaluate-btn");
    expect(btn.textContent.trim()).toBe("활성화 가능성 평가");
  });
});
