/**
 * #50 / 6-05 — PostTradeFeedbackCard 단위 테스트.
 *
 * lock: feedback summary/tags + threshold recommendation + 자동 적용 안 됨 +
 * 운영자 승인 필요 문구 + 주문/실전/승인 버튼 0개 + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PostTradeFeedbackCard } from "./PostTradeFeedbackCard";

afterEach(cleanup);

const _REPORT = {
  status: "OK", sample_count: 12, win_count: 6, loss_count: 4, neutral_count: 2,
  feedback_tags: [
    { tag: "OVER_ENTRY", count: 4, severity: "HIGH", message: "신호 약할 때 진입 보류 강화 검토" },
    { tag: "LATE_EXIT", count: 3, severity: "HIGH", message: "VWAP 이탈 시 청산 판단 강화 검토" },
    { tag: "WINNING_SETUP", count: 6, severity: "HIGH", message: "승리 셋업" },
  ],
  strategy_errors: { ORB: 3 },
  threshold_recommendations: [
    { reason_code: "OVER_ENTRY", message: "신호 약할 때 진입 보류 강화 검토",
      evidence_count: 4, auto_apply_allowed: false, requires_operator_approval: true },
  ],
  feedback_summary: "표본 12건 (승 6 / 패 4 / 중립 2).",
  auto_apply_allowed: false, requires_operator_approval: true,
  is_order_signal: false, is_live_authorization: false, contains_secret: false,
};

function _api(report = _REPORT) {
  return { agentFeedbackLoop: vi.fn(async () => report) };
}

describe("<PostTradeFeedbackCard>", () => {
  it("승/패/중립 카운트 표시", async () => {
    render(<PostTradeFeedbackCard apiClient={_api()} />);
    expect((await screen.findByTestId("feedback-counts")).textContent).toMatch(/12/);
  });

  it("feedback 태그 표시", async () => {
    render(<PostTradeFeedbackCard apiClient={_api()} />);
    expect((await screen.findByTestId("feedback-tag-OVER_ENTRY")).textContent).toContain("OVER_ENTRY");
    expect(screen.getByTestId("feedback-tag-LATE_EXIT").textContent).toContain("LATE_EXIT");
  });

  it("threshold 추천 표시", async () => {
    render(<PostTradeFeedbackCard apiClient={_api()} />);
    const t = (await screen.findByTestId("feedback-rec-OVER_ENTRY")).textContent;
    expect(t).toContain("운영자 승인 필요");
  });

  it("자동 적용 안 됨 / 운영자 승인 필요 문구", async () => {
    render(<PostTradeFeedbackCard apiClient={_api()} />);
    const badges = (await screen.findByTestId("feedback-badges")).textContent;
    expect(badges).toContain("자동 적용 안 됨");
    expect(badges).toContain("운영자 승인 필요");
  });

  it("표시 전용/주문 아님 문구", async () => {
    render(<PostTradeFeedbackCard apiClient={_api()} />);
    expect((await screen.findByTestId("feedback-intro")).textContent).toContain("주문 버튼이 아니며");
  });

  it("insufficient data 안내", async () => {
    render(<PostTradeFeedbackCard apiClient={_api({ ..._REPORT, status: "INSUFFICIENT_DATA" })} />);
    expect((await screen.findByTestId("feedback-insufficient")).textContent).toContain("부족");
  });

  it("주문/실전/승인 버튼 0개, 입력 form 0개", async () => {
    const { container } = render(<PostTradeFeedbackCard apiClient={_api()} />);
    await screen.findByTestId("feedback-tag-OVER_ENTRY");
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
    for (const f of ["지금 매수", "지금 매도", "Place Order", "실거래 시작", "승인하기", "자동 적용하기"]) {
      expect(container.textContent).not.toContain(f);
    }
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<PostTradeFeedbackCard apiClient={_api()} />);
    await screen.findByTestId("feedback-tag-OVER_ENTRY");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });
});
