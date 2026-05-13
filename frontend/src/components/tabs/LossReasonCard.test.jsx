import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LossReasonCard } from "./LossReasonCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    lossTagsSummary: vi.fn(),
    lossTagsRecent:  vi.fn(),
    lossTagsReview:  vi.fn(),
  },
}));


const _SUMMARY = {
  days: 7,
  loss_count: 5,
  pnl_sum: -120000,
  top_tags: [
    { tag: "stop_loss_hit", category: "strategy", count: 3, pnl_sum: -90000 },
    { tag: "market_selloff", category: "market", count: 2, pnl_sum: -60000 },
    { tag: "high_slippage", category: "execution", count: 1, pnl_sum: -20000 },
  ],
  top_primary: [
    { tag: "stop_loss_hit", count: 2 },
    { tag: "market_selloff", count: 2 },
  ],
  by_category: { strategy: 2, market: 2, execution: 1 },
  by_strategy: [
    { strategy: "sma_cross", count: 3, pnl_sum: -90000,
      top_tags: [{ tag: "stop_loss_hit", count: 2 },
                 { tag: "market_selloff", count: 1 }] },
  ],
  is_estimated: true,
  note: "본 요약은 *추정* 손실 원인입니다.",
};


const _RECENT = {
  items: [
    {
      id: 1, created_at: new Date().toISOString(),
      source_table: "manual", source_id: 100,
      symbol: "005930", strategy: "sma_cross", mode: "PAPER",
      trade_pnl: -30000, is_loss: true,
      primary_tag: "stop_loss_hit", primary_category: "strategy",
      tags: ["stop_loss_hit", "high_slippage"],
      rationale: ["손절가 근처 청산", "슬리피지 80bps"],
      confidence: 60, is_estimated: true,
      review_status: null, reviewed_by: null, review_note: null, reviewed_at: null,
    },
    {
      id: 2, created_at: new Date().toISOString(),
      source_table: "manual", source_id: 101,
      symbol: "000660", strategy: "sma_cross", mode: "PAPER",
      trade_pnl: -20000, is_loss: true,
      primary_tag: "market_selloff", primary_category: "market",
      tags: ["market_selloff"],
      rationale: ["KOSPI -2.5%"],
      confidence: 50, is_estimated: true,
      review_status: "agreed", reviewed_by: "ops", review_note: "확인됨",
      reviewed_at: new Date().toISOString(),
    },
  ],
  is_estimated: true,
  note: "본 목록은 *추정* 손실 원인입니다.",
};


afterEach(cleanup);


describe("LossReasonCard", () => {
  it("'추정 원인 · 확정 원인 아님' 영구 배지 노출", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const badge = getByTestId("loss-reason-estimated-badge");
    expect(badge.textContent).toContain("추정 원인");
    expect(badge.textContent).toContain("확정 원인 아님");
  });

  it("disclaimer가 '확정 원인이 아닙니다' 명시", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const disc = getByTestId("loss-reason-disclaimer").textContent;
    expect(disc).toContain("추정 원인");
    expect(disc).toContain("확정 원인이 아닙니다");
    expect(disc).toContain("투자");
    expect(disc).toContain("조언");
    expect(disc).toContain("주문 차단");
  });

  it("top tags / categories / by_strategy / recent 모두 노출", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    expect(getByTestId("loss-reason-top-tags").textContent).toContain("stop_loss_hit");
    expect(getByTestId("loss-reason-categories").textContent).toContain("strategy: 2");
    expect(getByTestId("loss-reason-by-strategy").textContent).toContain("sma_cross");
    expect(getByTestId("loss-reason-recent").textContent).toContain("005930");
  });

  it("recent item에 추정 primary_tag 배지 + review note 표시", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const recent = getByTestId("loss-reason-recent");
    expect(recent.textContent).toContain("stop_loss_hit");
    expect(recent.textContent).toContain("market_selloff");
    expect(recent.textContent).toContain("agreed");        // review_status
    expect(recent.textContent).toContain("확인됨");          // review_note
  });

  it("loss_count / pnl_sum 통계 노출", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const stats = getByTestId("loss-reason-stats");
    expect(stats.textContent).toContain("5");
    expect(stats.textContent).toContain("-120,000");
    expect(stats.textContent).toContain("7일");
  });

  it("강제 적용 / 자동 비활성 / 삭제 / 확정 원인 라벨 버튼 0개", () => {
    const { container } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);  // 새로 고침 버튼
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "강제 적용",
        "자동 비활성",
        "전략 비활성화",
        "전략 삭제",
        "삭제",
        "Delete",
        "확정 원인",
        "주문 차단 적용",
        "ENABLE_AI_EXECUTION",
        "ENABLE_LIVE_TRADING",
        "Place Order",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("'원인' 단독 표현 없이 항상 '추정' 또는 '추정 원인'으로 표시", () => {
    const { container } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const text = container.textContent || "";
    // 원인이 나오는 모든 위치에 "추정" 키워드가 함께 있는지 확인.
    expect(text).toContain("추정");
    // 확정 표현 0개.
    expect(text.includes("확정 원인")).toBe(true);    // *확정 원인이 아닙니다* 안내에만
    expect(text.includes("확정 원인이 아닙니다")).toBe(true);
    expect(text.includes("원인 확정")).toBe(false);
  });

  it("BUY/SELL/HOLD 주문 신호 문구 0건", () => {
    const { container } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("'새로 고침' 버튼 라벨이 '추정 원인 요약 새로 고침'", () => {
    const { getByTestId } = render(
      <LossReasonCard summaryOverride={_SUMMARY} recentOverride={_RECENT} />,
    );
    const btn = getByTestId("loss-reason-refresh-btn");
    expect(btn.textContent.trim()).toBe("추정 원인 요약 새로 고침");
  });
});
