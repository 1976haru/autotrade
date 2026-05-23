/**
 * P-17: BuyBlockReasonsCard 단위 테스트.
 *
 * invariant:
 *  - 차단 건수 / reason_code 요약 / 최근 내역 표시.
 *  - empty state.
 *  - 실거래 / Place Order / 매수 버튼 0개.
 *  - LIVE 토글 / API key 입력 0개.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { BuyBlockReasonsCard } from "./BuyBlockReasonsCard";


function _mkApi(summary) {
  return {
    autoPaperBlockedReasonsToday: vi.fn().mockResolvedValue(summary),
  };
}

const _SAMPLE = {
  total_blocked: 3,
  by_reason: {
    INSUFFICIENT_PAPER_CASH: 2,
    MIN_LOT_NOT_AFFORDABLE: 1,
  },
  recent: [
    {
      reason_code: "MIN_LOT_NOT_AFFORDABLE", symbol: "373220",
      timestamp: "2026-05-23T00:42:00+00:00",
      detail: "",
    },
    {
      reason_code: "INSUFFICIENT_PAPER_CASH", symbol: "005930",
      timestamp: "2026-05-23T00:31:00+00:00",
      details: { required_amount: 500000, remaining_cash: 300000 },
    },
  ],
  last_block: { reason_code: "MIN_LOT_NOT_AFFORDABLE", symbol: "373220" },
  is_order_signal: false,
  is_live_authorization: false,
};


describe("<BuyBlockReasonsCard>", () => {
  afterEach(cleanup);

  it("카드 + 배지 렌더링", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-reasons-card")).toBeTruthy());
    expect(screen.getByTestId("badge-display-only").textContent)
      .toMatch(/표시 전용/);
  });

  it("총 차단 건수 표시", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-total").textContent)
        .toMatch(/오늘 매수 차단 3건/));
  });

  it("reason_code 별 요약 표시", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-reason-INSUFFICIENT_PAPER_CASH"))
        .toBeTruthy());
    expect(screen.getByTestId("buy-block-reason-INSUFFICIENT_PAPER_CASH").textContent)
      .toMatch(/남은 Paper 현금이 부족/);
    expect(screen.getByTestId("buy-block-reason-INSUFFICIENT_PAPER_CASH").textContent)
      .toMatch(/2건/);
  });

  it("최근 차단 내역 표시 (종목/한국어 사유/상세)", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-recent")).toBeTruthy());
    const rows = screen.getAllByTestId("buy-block-recent-row");
    expect(rows.length).toBe(2);
    const txt = screen.getByTestId("buy-block-recent").textContent;
    expect(txt).toMatch(/373220/);
    expect(txt).toMatch(/1주 가격이 투자한도 초과로 제외/);
    expect(txt).toMatch(/005930/);
    // 현금 부족 상세 문구.
    expect(txt).toMatch(/필요 금액 500,000원 > 남은 Paper 현금 300,000원/);
  });

  it("후보 없을 때 empty state", async () => {
    const empty = { total_blocked: 0, by_reason: {}, recent: [], last_block: null,
      is_order_signal: false, is_live_authorization: false };
    render(<BuyBlockReasonsCard apiClient={_mkApi(empty)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-empty").textContent)
        .toMatch(/아직 기록된 매수 불가 사유가 없습니다/));
  });

  it("API 실패 시 에러 표시 (크래시 없음)", async () => {
    const api = {
      autoPaperBlockedReasonsToday: vi.fn().mockRejectedValue(new Error("boom")),
    };
    render(<BuyBlockReasonsCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("buy-block-error")).toBeTruthy());
  });

  it("실거래 / Place Order / 매수 라벨 button 0개 (invariant)", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() => expect(screen.getByTestId("buy-block-reasons-card")).toBeTruthy());
    const buttons = Array.from(document.querySelectorAll("button"));
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      expect(txt).not.toMatch(/Place Order/i);
      expect(txt).not.toMatch(/실거래/);
      expect(txt).not.toMatch(/지금 매수/);
      expect(txt).not.toMatch(/ENABLE_/);
    }
  });

  it("API key / secret 입력 form 0개 + LIVE 토글 0개 (invariant)", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() => expect(screen.getByTestId("buy-block-reasons-card")).toBeTruthy());
    expect(document.querySelectorAll("input").length).toBe(0);
    expect(document.querySelectorAll("select").length).toBe(0);
    const txt = screen.getByTestId("buy-block-reasons-card").textContent;
    expect(txt).not.toMatch(/ENABLE_LIVE_TRADING/);
    expect(txt).not.toMatch(/실거래 활성화/);
  });

  it("footer 표시 전용 안내", async () => {
    render(<BuyBlockReasonsCard apiClient={_mkApi(_SAMPLE)} />);
    await waitFor(() =>
      expect(screen.getByTestId("buy-block-footer").textContent)
        .toMatch(/표시\/진단용/));
  });
});
