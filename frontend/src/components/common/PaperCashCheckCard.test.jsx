/**
 * P-07: PaperCashCheckCard — 단위 테스트.
 *
 * 검증 (사용자 요청서 §frontend 테스트 요구):
 *  - 현금 부족 차단 사유가 화면에 표시
 *  - "남은 Paper 현금이 부족하여 매수 차단" 문구 표시
 *  - INSUFFICIENT_PAPER_CASH 사유 표시
 *  - 실거래 활성화 / LIVE 관련 UI 0개
 *  - input / textarea / select 0개
 *  - secret 패턴 0개
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PaperCashCheckCard } from "./PaperCashCheckCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    previewPaperCashCheck: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function makeResult(overrides = {}) {
  return {
    verdict:               "ALLOWED",
    symbol:                "005930",
    action:                "BUY",
    price:                 100_000,
    quantity:              3,
    required_krw:          300_000,
    available_cash_krw:    1_000_000,
    shortfall_krw:         0,
    reason_ko:             "Paper 현금 잔고 충분 — 필요 금액 300,000원 ≤ 남은 Paper 현금 1,000,000원.",
    risk_flag:             null,
    metadata:              {},
    is_allowed:            true,
    is_insufficient:       false,
    is_paper_only:         true,
    is_order_signal:       false,
    is_live_authorization: false,
    notice:                "본 결과는 advisory — Paper 전용이며 실거래 주문 결정과 결합되지 않습니다.",
    ...overrides,
  };
}


beforeEach(() => {
  backendApi.previewPaperCashCheck.mockReset();
});

afterEach(cleanup);


describe("<PaperCashCheckCard>", () => {
  it("renders ALLOWED verdict when cash is sufficient", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={3} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-verdict"),
    );
    expect(
      screen.getByTestId("paper-cash-check-card-verdict")
        .getAttribute("data-verdict"),
    ).toBe("ALLOWED");
    expect(
      screen.getByTestId("paper-cash-check-card-verdict").textContent,
    ).toContain("현금 충분");
  });

  it("renders INSUFFICIENT_PAPER_CASH verdict + 차단 banner when blocked", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult({
      verdict:            "INSUFFICIENT_PAPER_CASH",
      quantity:           5,
      required_krw:       500_000,
      available_cash_krw: 300_000,
      shortfall_krw:      200_000,
      is_allowed:         false,
      is_insufficient:    true,
      reason_ko:          "남은 Paper 현금이 부족하여 매수 차단 — 필요 금액 500,000원 > 남은 Paper 현금 300,000원 (부족 200,000원).",
      risk_flag:          "insufficient_paper_cash",
    }));
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={5} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-blocked-banner"),
    );
    // 1. 사용자 요청서: "남은 Paper 현금이 부족하여 매수 차단" 표시.
    expect(
      screen.getByTestId("paper-cash-check-card-blocked-banner").textContent,
    ).toBe("남은 Paper 현금이 부족하여 매수 차단");
    // 2. INSUFFICIENT_PAPER_CASH 사유 노출.
    expect(
      screen.getByTestId("paper-cash-check-card-verdict").textContent,
    ).toContain("INSUFFICIENT_PAPER_CASH");
    expect(
      screen.getByTestId("paper-cash-check-card-verdict")
        .getAttribute("data-verdict"),
    ).toBe("INSUFFICIENT_PAPER_CASH");
  });

  it("displays required / available / shortfall amounts", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult({
      verdict:            "INSUFFICIENT_PAPER_CASH",
      quantity:           5,
      required_krw:       500_000,
      available_cash_krw: 300_000,
      shortfall_krw:      200_000,
      is_allowed:         false,
      is_insufficient:    true,
      reason_ko:          "남은 Paper 현금이 부족하여 매수 차단",
    }));
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={5} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-amounts"),
    );
    expect(
      screen.getByTestId("paper-cash-check-card-amount-required").textContent,
    ).toContain("500,000");
    expect(
      screen.getByTestId("paper-cash-check-card-amount-available").textContent,
    ).toContain("300,000");
    expect(
      screen.getByTestId("paper-cash-check-card-amount-shortfall").textContent,
    ).toContain("200,000");
  });

  it("displays reason_ko with '남은 Paper 현금' fragment", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult({
      verdict:        "INSUFFICIENT_PAPER_CASH",
      reason_ko:      "남은 Paper 현금이 부족하여 매수 차단 — 필요 금액 500,000원 > 남은 Paper 현금 300,000원 (부족 200,000원).",
    }));
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={5} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-reason"),
    );
    const text = screen.getByTestId("paper-cash-check-card-reason").textContent || "";
    expect(text).toContain("남은 Paper 현금");
    expect(text).toContain("매수 차단");
    expect(text).toContain("필요 금액");
  });

  it("shows disclaimer about 종목당 한도 vs 현금 분리", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-disclaimer"),
    );
    const text = screen.getByTestId("paper-cash-check-card-disclaimer").textContent || "";
    expect(text).toContain("종목당 투자 한도");
    expect(text).toContain("별개");
    expect(text).toContain("차감하지 않습니다");
  });

  it("renders 'no input' state when price or quantity is missing", () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    render(<PaperCashCheckCard symbol="005930" price={null} quantity={null} />);
    expect(
      screen.getByTestId("paper-cash-check-card-no-input").textContent,
    ).toContain("후보 정보");
  });

  it("renders SKIP_NON_BUY verdict without blocking", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult({
      verdict:         "SKIP_NON_BUY",
      action:          "SELL",
      is_allowed:      false,
      is_insufficient: false,
      reason_ko:       "BUY 가 아닌 action — 현금 잔고 검사 무관 (청산 / 관망은 현금 부족으로 차단되지 않습니다).",
    }));
    render(<PaperCashCheckCard symbol="005930" action="SELL"
                                price={100_000} quantity={5} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-verdict"),
    );
    // SKIP 은 차단 banner 노출 X.
    expect(
      screen.queryByTestId("paper-cash-check-card-blocked-banner"),
    ).toBeNull();
  });

  it("displays invariant badges (Paper 전용 / 현금 차감 0건 / broker 호출 0건)", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />);
    await waitFor(() =>
      screen.getByTestId("paper-cash-check-card-invariant-badges"),
    );
    const text = screen.getByTestId("paper-cash-check-card-invariant-badges").textContent || "";
    expect(text).toContain("Paper 전용");
    expect(text).toContain("현금 차감 0건");
    expect(text).toContain("broker 호출 0건");
  });

  it("error response shows fallback message", async () => {
    backendApi.previewPaperCashCheck.mockRejectedValue(new Error("boom"));
    render(<PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />);
    await waitFor(() => screen.getByTestId("paper-cash-check-card-error"));
    expect(
      screen.getByTestId("paper-cash-check-card-error").textContent,
    ).toContain("boom");
  });

  it("has NO trade-execution / live-order labels or buttons", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    const { container } = render(
      <PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />,
    );
    await waitFor(() => screen.getByTestId("paper-cash-check-card"));
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // button element 0개 — 본 카드는 표시 전용.
    expect(container.querySelectorAll("button").length).toBe(0);
  });

  it("has NO input / textarea / select form elements", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    const { container } = render(
      <PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />,
    );
    await waitFor(() => screen.getByTestId("paper-cash-check-card"));
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("does not expose secret patterns", async () => {
    backendApi.previewPaperCashCheck.mockResolvedValue(makeResult());
    const { container } = render(
      <PaperCashCheckCard symbol="005930" price={100_000} quantity={1} />,
    );
    await waitFor(() => screen.getByTestId("paper-cash-check-card"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-ant-",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });
});
