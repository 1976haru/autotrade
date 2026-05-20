/**
 * P-04: PaperAffordabilityCard 테스트.
 *
 * invariant 강제:
 *  - verdict 헤드라인 + 한국어 reason + 5개 수치 (종목 / 가격 / 한도 /
 *    현금 / 가능 수량 / 보유) 표시
 *  - "Paper 전용 · 실제 주문 아님" 배지 영구
 *  - input/textarea/select 0개 (임의 입력 form 차단)
 *  - "지금 매수" / "Place Order" / "BUY/SELL/HOLD" / "실거래" 라벨 button 0개
 *  - secret 패턴 노출 0건
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PaperAffordabilityCard } from "./PaperAffordabilityCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: { previewPaperAffordability: vi.fn() },
}));

import { backendApi } from "../../services/backend/client";


const _MK_RESULT = (overrides = {}) => ({
  verdict: "AFFORDABLE",
  symbol: "005930",
  action: "BUY",
  price: 70_000,
  effective_per_symbol_cap_krw: 1_000_000,
  available_cash_krw: 10_000_000,
  affordable_quantity: 14,
  current_held_unique_symbols: 0,
  max_concurrent_positions: 3,
  is_existing_position: false,
  reason_ko: "Paper 매수 가능: 1주 70,000원 × 14주 가능 (한도 1,000,000원 / 현금 10,000,000원).",
  risk_flag: null,
  metadata: {},
  is_affordable: true,
  is_order_signal: false,
  is_live_authorization: false,
  is_paper_only: true,
  ...overrides,
});


beforeEach(() => {
  backendApi.previewPaperAffordability.mockReset();
});
afterEach(cleanup);


describe("<PaperAffordabilityCard>", () => {
  it("renders Paper-only badge always", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card"));
    const badge = screen.getByTestId("paper-affordability-card-paper-only-badge");
    expect(badge.textContent).toContain("Paper 전용");
    expect(badge.textContent).toContain("실제 주문 아님");
  });

  it("shows AFFORDABLE verdict for 1주=900k under cap=1M", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(
      _MK_RESULT({ price: 900_000, affordable_quantity: 1 }),
    );
    render(<PaperAffordabilityCard symbol="005930" price={900_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-verdict"));
    expect(
      screen.getByTestId("paper-affordability-card-verdict").getAttribute("data-verdict"),
    ).toBe("AFFORDABLE");
    expect(
      screen.getByTestId("paper-affordability-card-detail-affordable-qty").textContent,
    ).toContain("1주");
  });

  it("shows PRICE_OVER_CAP for 1주=1.8M with cap=1M (SK하이닉스 예시)", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT({
      verdict: "PRICE_OVER_CAP",
      symbol: "000660",
      price: 1_800_000,
      affordable_quantity: 0,
      reason_ko: "1주 가격이 종목당 투자금 한도 1,000,000원을 초과해 Paper 매수 후보에서 제외했습니다.",
      risk_flag: "price_over_per_symbol_cap",
      is_affordable: false,
    }));
    render(<PaperAffordabilityCard symbol="000660" price={1_800_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-verdict"));
    expect(
      screen.getByTestId("paper-affordability-card-verdict").getAttribute("data-verdict"),
    ).toBe("PRICE_OVER_CAP");
    expect(
      screen.getByTestId("paper-affordability-card-reason").textContent,
    ).toContain("종목당 투자금 한도");
    // 매수 가능 수량 = 0주.
    expect(
      screen.getByTestId("paper-affordability-card-detail-affordable-qty").textContent,
    ).toContain("0주");
  });

  it("shows INSUFFICIENT_CASH when cash < price", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT({
      verdict: "INSUFFICIENT_CASH",
      price: 500_000,
      available_cash_krw: 100_000,
      affordable_quantity: 0,
      reason_ko: "남은 Paper 현금 100,000원으로 1주(500,000원)를 살 수 없어 Paper 매수 후보에서 제외했습니다.",
      risk_flag: "insufficient_paper_cash",
      is_affordable: false,
    }));
    render(<PaperAffordabilityCard symbol="005930" price={500_000}
                                    availableCashKrw={100_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-verdict"));
    expect(
      screen.getByTestId("paper-affordability-card-verdict").getAttribute("data-verdict"),
    ).toBe("INSUFFICIENT_CASH");
    expect(
      screen.getByTestId("paper-affordability-card-reason").textContent,
    ).toContain("Paper 현금");
  });

  it("shows MAX_POSITIONS_REACHED when held >= limit", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT({
      verdict: "MAX_POSITIONS_REACHED",
      current_held_unique_symbols: 3,
      max_concurrent_positions: 3,
      reason_ko: "동시 보유 종목 수 한도 3종목 도달 — 신규 종목 매수 후보에서 제외했습니다 (현재 3종목 보유).",
      risk_flag: "max_positions_reached",
      is_affordable: false,
    }));
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000}
                                    currentHeldSymbols={["a", "b", "c"]} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-verdict"));
    expect(
      screen.getByTestId("paper-affordability-card-verdict").getAttribute("data-verdict"),
    ).toBe("MAX_POSITIONS_REACHED");
    expect(
      screen.getByTestId("paper-affordability-card-detail-positions").textContent,
    ).toContain("3 / 3종목");
  });

  it("shows MISSING_PRICE verdict when backend returns missing_price", async () => {
    // backend 가 가격 누락 verdict 를 반환하는 케이스 — caller 가 price=null 을
    // 명시 전달한 경우는 본 카드 자체가 backend 호출 없이 'no-price' state 로 들어감.
    // 본 테스트는 backend 가 verdict=MISSING_PRICE 를 명시 반환한 케이스.
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT({
      verdict: "MISSING_PRICE",
      price: null,
      affordable_quantity: 0,
      reason_ko: "현재가가 없어 매수 가능성을 평가할 수 없어 Paper 매수 후보에서 제외했습니다.",
      risk_flag: "missing_price",
      is_affordable: false,
    }));
    render(<PaperAffordabilityCard symbol="005930" price={0.01}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-verdict"));
    expect(
      screen.getByTestId("paper-affordability-card-verdict").getAttribute("data-verdict"),
    ).toBe("MISSING_PRICE");
  });

  it("shows no-price placeholder when price prop is null", () => {
    render(<PaperAffordabilityCard symbol="005930" price={null}
                                    availableCashKrw={10_000_000} />);
    expect(screen.getByTestId("paper-affordability-card-no-price")).toBeTruthy();
    expect(backendApi.previewPaperAffordability).not.toHaveBeenCalled();
  });

  it("shows error on backend failure", async () => {
    backendApi.previewPaperAffordability.mockRejectedValue(new Error("boom"));
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-error"));
    expect(
      screen.getByTestId("paper-affordability-card-error").textContent,
    ).toContain("boom");
  });

  it("calls backendApi.previewPaperAffordability with correct args", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000}
                                    currentHeldSymbols={["000660"]} />);
    await waitFor(() => {
      expect(backendApi.previewPaperAffordability).toHaveBeenCalledWith({
        action: "BUY",
        symbol: "005930",
        price: 70_000,
        availableCashKrw: 10_000_000,
        currentHeldSymbols: ["000660"],
      });
    });
  });

  it("renders all 5 detail rows (price / cap / cash / qty / positions)", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-details"));
    expect(screen.getByTestId("paper-affordability-card-detail-symbol")).toBeTruthy();
    expect(screen.getByTestId("paper-affordability-card-detail-price")).toBeTruthy();
    expect(screen.getByTestId("paper-affordability-card-detail-cap")).toBeTruthy();
    expect(screen.getByTestId("paper-affordability-card-detail-cash")).toBeTruthy();
    expect(screen.getByTestId("paper-affordability-card-detail-affordable-qty")).toBeTruthy();
    expect(screen.getByTestId("paper-affordability-card-detail-positions")).toBeTruthy();
  });

  it("has NO trade-execution / live-order buttons", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    const { container } = render(
      <PaperAffordabilityCard symbol="005930" price={70_000}
                              availableCashKrw={10_000_000} />,
    );
    await waitFor(() => screen.getByTestId("paper-affordability-card"));
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // input/textarea/select 0개 — caller 가 prop 으로 후보를 전달.
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
    // button 도 0개 — 본 카드는 *advisory 표시 전용*.
    expect(container.querySelectorAll("button").length).toBe(0);
  });

  it("does not expose any secret-like strings", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    const { container } = render(
      <PaperAffordabilityCard symbol="005930" price={70_000}
                              availableCashKrw={10_000_000} />,
    );
    await waitFor(() => screen.getByTestId("paper-affordability-card"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("disclaimer says Paper-only advisory + broker call 0", async () => {
    backendApi.previewPaperAffordability.mockResolvedValue(_MK_RESULT());
    render(<PaperAffordabilityCard symbol="005930" price={70_000}
                                    availableCashKrw={10_000_000} />);
    await waitFor(() => screen.getByTestId("paper-affordability-card-disclaimer"));
    const text = screen.getByTestId("paper-affordability-card-disclaimer").textContent || "";
    expect(text).toContain("Paper 모의매매 전용");
    expect(text).toContain("advisory");
    expect(text).toContain("broker");
  });
});
