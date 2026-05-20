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
    paperCapitalConfig:           vi.fn(),
    setPaperCapitalConfig:        vi.fn(),
    setPaperPerSymbolAllocation:  vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


const _DEFAULT_CONFIG = {
  initial_cash: 10_000_000,
  allowed_initial_cash_options: [10_000_000, 30_000_000, 50_000_000],
  // P-02 fields.
  per_symbol_mode: "FIXED_KRW",
  per_symbol_max_krw: 1_000_000,
  per_symbol_max_pct: 0.10,
  effective_per_symbol_cap_krw: 1_000_000,
  allowed_per_symbol_max_krw_options: [1_000_000, 2_000_000],
  allowed_per_symbol_max_pct_options: [0.10],
  currency: "KRW",
  is_paper_only: true,
  is_live_authorization: false,
  updated_at: "2026-05-21T01:00:00+00:00",
  notice: "Paper 시드머니는 모의매매 전용이며 실전 계좌와 무관합니다.",
};


beforeEach(() => {
  backendApi.paperCapitalConfig.mockReset();
  backendApi.setPaperCapitalConfig.mockReset();
  backendApi.setPaperPerSymbolAllocation.mockReset();
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


// ────────────────────────────────────────────────────────────────────────────
// P-02: 종목당 한도 UI
// ────────────────────────────────────────────────────────────────────────────
describe("<PaperCapitalCard> P-02 — per-symbol allocation", () => {
  it("renders 3 per-symbol options (100만원 / 200만원 / 시드머니의 10%)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-per-symbol-section"));
    expect(screen.getByTestId("paper-capital-card-per-symbol-option-fixed-1m")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-per-symbol-option-fixed-2m")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-per-symbol-option-pct-10")).toBeTruthy();
    const text = container.textContent || "";
    expect(text).toContain("100만원");
    expect(text).toContain("200만원");
    expect(text).toContain("시드머니의 10%");
  });

  it("shows current effective cap (1,000,000 KRW) for FIXED_KRW default", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-effective"),
    );
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-effective-value").textContent,
    ).toContain("100만원");
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-effective").textContent,
    ).toContain("FIXED_KRW");
  });

  it("highlights selected option (fixed-1m default)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-option-fixed-1m"),
    );
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-option-fixed-1m")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-option-fixed-2m")
        .getAttribute("data-selected"),
    ).toBe("false");
  });

  it("clicking 200만원 option calls setPaperPerSymbolAllocation with FIXED_KRW 2M", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperPerSymbolAllocation.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      per_symbol_mode: "FIXED_KRW",
      per_symbol_max_krw: 2_000_000,
      effective_per_symbol_cap_krw: 2_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-option-fixed-2m"),
    );
    fireEvent.click(screen.getByTestId("paper-capital-card-per-symbol-option-fixed-2m"));
    await waitFor(() => {
      expect(backendApi.setPaperPerSymbolAllocation).toHaveBeenCalledWith({
        mode: "FIXED_KRW",
        perSymbolMaxKrw: 2_000_000,
        perSymbolMaxPct: null,
      });
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-per-symbol-effective-value").textContent,
      ).toContain("200만원");
    });
  });

  it("clicking 10% option calls setPaperPerSymbolAllocation with PCT_OF_EQUITY", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperPerSymbolAllocation.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      per_symbol_mode: "PCT_OF_EQUITY",
      per_symbol_max_pct: 0.10,
      effective_per_symbol_cap_krw: 1_000_000,  // 10m * 10%
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-option-pct-10"),
    );
    fireEvent.click(screen.getByTestId("paper-capital-card-per-symbol-option-pct-10"));
    await waitFor(() => {
      expect(backendApi.setPaperPerSymbolAllocation).toHaveBeenCalledWith({
        mode: "PCT_OF_EQUITY",
        perSymbolMaxKrw: null,
        perSymbolMaxPct: 0.10,
      });
    });
  });

  it("shows disclaimer about per-symbol cap being paper-only and not live order amount", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-disclaimer"),
    );
    const text = screen.getByTestId("paper-capital-card-per-symbol-disclaimer")
      .textContent || "";
    expect(text).toContain("Paper 모의매매 전용");
    expect(text).toContain("실전 주문금액이 아닙니다");
  });

  it("displays effective cap=3,000,000 (300만원) when 10% mode and 30M equity", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      initial_cash: 30_000_000,
      per_symbol_mode: "PCT_OF_EQUITY",
      per_symbol_max_pct: 0.10,
      effective_per_symbol_cap_krw: 3_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-effective-value"),
    );
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-effective-value").textContent,
    ).toContain("300만원");
  });

  it("displays effective cap=5,000,000 (500만원) when 10% mode and 50M equity", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      initial_cash: 50_000_000,
      per_symbol_mode: "PCT_OF_EQUITY",
      per_symbol_max_pct: 0.10,
      effective_per_symbol_cap_krw: 5_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-effective-value"),
    );
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-effective-value").textContent,
    ).toContain("500만원");
  });

  it("per-symbol options have NO live-trade labels", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-per-symbol-section"));
    const section = screen.getByTestId("paper-capital-card-per-symbol-section");
    const text = section.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "BUY", "SELL", "HOLD",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // input/textarea/select 0개 — 임의 입력 form 차단.
    expect(section.querySelectorAll("input").length).toBe(0);
    expect(section.querySelectorAll("textarea").length).toBe(0);
    expect(section.querySelectorAll("select").length).toBe(0);
    // top-level container 도 동일하게 secret-noisy 라벨 0건.
    expect((container.textContent || "").includes("kis_app_key")).toBe(false);
  });

  it("per-symbol section does NOT expose secret patterns", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-per-symbol-section"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("highlights pct-10 when mode=PCT_OF_EQUITY", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      per_symbol_mode: "PCT_OF_EQUITY",
      per_symbol_max_pct: 0.10,
      effective_per_symbol_cap_krw: 1_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-per-symbol-option-pct-10"),
    );
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-option-pct-10")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-per-symbol-option-fixed-1m")
        .getAttribute("data-selected"),
    ).toBe("false");
  });
});
