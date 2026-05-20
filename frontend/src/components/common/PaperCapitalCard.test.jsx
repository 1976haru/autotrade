/**
 * P-01: PaperCapitalCard 단위 테스트.
 *
 * invariant 강제:
 *  - 3개 옵션 button (10,000,000 / 30,000,000 / 50,000,000) 노출
 *  - "실전 계좌와 무관" disclaimer 노출
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "BUY" / "SELL" / "HOLD"
 *    라벨 button 0개
 *  - 임의 KRW 입력 form 0개 (input type="number" 0개)
 *  - secret 패턴 노출 0건
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PaperCapitalCard } from "./PaperCapitalCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    paperCapitalConfig:    vi.fn(),
    setPaperCapitalConfig: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


const _DEFAULT_CONFIG = {
  initial_cash: 10_000_000,
  allowed_initial_cash_options: [10_000_000, 30_000_000, 50_000_000],
  currency: "KRW",
  is_paper_only: true,
  is_live_authorization: false,
  updated_at: "2026-05-21T01:00:00+00:00",
  notice: "Paper 시드머니는 모의매매 전용이며 실전 계좌와 무관합니다.",
};


beforeEach(() => {
  backendApi.paperCapitalConfig.mockReset();
  backendApi.setPaperCapitalConfig.mockReset();
});
afterEach(cleanup);


describe("<PaperCapitalCard>", () => {
  it("renders 3 options (1,000만원 / 3,000만원 / 5,000만원)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-summary"));
    expect(screen.getByTestId("paper-capital-card-option-10000000")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-option-30000000")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-option-50000000")).toBeTruthy();
  });

  it("displays 1,000만원 / 3,000만원 / 5,000만원 Korean labels", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-summary"));
    const text = container.textContent || "";
    expect(text).toContain("1,000만원");
    expect(text).toContain("3,000만원");
    expect(text).toContain("5,000만원");
  });

  it("highlights selected option with data-selected=true", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      initial_cash: 30_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-summary"));
    expect(
      screen.getByTestId("paper-capital-card-option-30000000")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-option-10000000")
        .getAttribute("data-selected"),
    ).toBe("false");
  });

  it("clicking an option calls setPaperCapitalConfig + updates UI", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      initial_cash: 50_000_000,
      fallback_used: false,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-option-50000000"));
    fireEvent.click(screen.getByTestId("paper-capital-card-option-50000000"));
    await waitFor(() => {
      expect(backendApi.setPaperCapitalConfig).toHaveBeenCalledWith({
        initialCash: 50_000_000,
      });
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-current-value").textContent,
      ).toContain("5,000만원");
    });
  });

  it("shows disclaimer that money is paper-only and unrelated to real account", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-disclaimer"));
    const text = screen.getByTestId("paper-capital-card-disclaimer").textContent || "";
    expect(text).toContain("모의매매 전용");
    expect(text).toContain("실전 계좌와 무관");
  });

  it("displays invariant badges (Paper 전용 / 실거래 OFF / 주문 권한 없음)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-invariant-badges"));
    const text = screen.getByTestId("paper-capital-card-invariant-badges")
      .textContent || "";
    expect(text).toContain("Paper 전용");
    expect(text).toContain("실거래 OFF 유지");
    expect(text).toContain("주문 권한 없음");
  });

  it("error response shows '설정 조회 실패' fallback", async () => {
    backendApi.paperCapitalConfig.mockRejectedValue(new Error("boom"));
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-error"));
    expect(screen.getByTestId("paper-capital-card-error").textContent)
      .toContain("boom");
  });

  it("has NO trade-execution / live-order buttons", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card"));
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "BUY", "SELL", "HOLD",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("has NO raw KRW input form (only the 3 preset options)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card"));
    // 사용자가 *임의* KRW 값을 입력할 수 있는 form 이 있으면 안 됨 — 허용
    // 옵션만 button 으로 노출. input / textarea / select 0개.
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("does not expose any secret-looking strings", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("shows currency=KRW in the summary", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-summary"));
    expect(
      screen.getByTestId("paper-capital-card-summary").textContent,
    ).toContain("KRW");
  });
});
