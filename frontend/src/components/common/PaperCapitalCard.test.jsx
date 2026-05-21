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
    paperCapitalConfig:                 vi.fn(),
    setPaperCapitalConfig:              vi.fn(),
    setPaperPerSymbolAllocation:        vi.fn(),
    setPaperMaxConcurrentPositions:     vi.fn(),
    previewPaperConcurrentBuy:          vi.fn(),
    paperMinLotPreview:                 vi.fn(),
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
  // P-03 fields.
  max_concurrent_positions: 3,
  allowed_max_concurrent_positions_options: [3, 5, 10],
  currency: "KRW",
  is_paper_only: true,
  is_live_authorization: false,
  updated_at: "2026-05-21T01:00:00+00:00",
  notice: "Paper 시드머니는 모의매매 전용이며 실전 계좌와 무관합니다.",
};


const _DEFAULT_MIN_LOT_PREVIEW = {
  effective_per_symbol_cap_krw: 1_000_000,
  initial_cash: 10_000_000,
  min_lot_quantity: 1,
  fractional_share_supported: false,
  rounding_policy: "floor",
  examples: [
    { price: 50_000,    affordable_quantity: 20, is_affordable: true  },
    { price: 100_000,   affordable_quantity: 10, is_affordable: true  },
    { price: 500_000,   affordable_quantity: 2,  is_affordable: true  },
    { price: 1_000_000, affordable_quantity: 1,  is_affordable: true  },
    { price: 2_000_000, affordable_quantity: 0,  is_affordable: false },
    { price: 5_000_000, affordable_quantity: 0,  is_affordable: false },
  ],
  is_paper_only: true,
  is_live_authorization: false,
  notice: "예시 표는 현재 시드머니 전체가 가용하다는 가정. Paper 전용 advisory.",
};


beforeEach(() => {
  backendApi.paperCapitalConfig.mockReset();
  backendApi.setPaperCapitalConfig.mockReset();
  backendApi.setPaperPerSymbolAllocation.mockReset();
  backendApi.setPaperMaxConcurrentPositions.mockReset();
  backendApi.previewPaperConcurrentBuy.mockReset();
  backendApi.paperMinLotPreview.mockReset();
  // default mock — min-lot preview 가 항상 응답하도록 (P-01~04 tests 들이
  // 본 preview 를 명시 mock 하지 않아도 동작).
  backendApi.paperMinLotPreview.mockResolvedValue(_DEFAULT_MIN_LOT_PREVIEW);
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


// ────────────────────────────────────────────────────────────────────────────
// P-03: 최대 동시 보유 종목 수 UI
// ────────────────────────────────────────────────────────────────────────────
describe("<PaperCapitalCard> P-03 — max concurrent positions", () => {
  it("renders 3 options (3종목 / 5종목 / 10종목)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-section"));
    expect(screen.getByTestId("paper-capital-card-max-positions-option-3")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-max-positions-option-5")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-max-positions-option-10")).toBeTruthy();
  });

  it("displays 3종목 / 5종목 / 10종목 Korean labels", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-section"));
    const text = container.textContent || "";
    expect(text).toContain("3종목");
    expect(text).toContain("5종목");
    expect(text).toContain("10종목");
  });

  it("default 3종목 is selected", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-option-3"));
    expect(
      screen.getByTestId("paper-capital-card-max-positions-option-3")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-max-positions-option-5")
        .getAttribute("data-selected"),
    ).toBe("false");
  });

  it("clicking 5종목 calls setPaperMaxConcurrentPositions", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperMaxConcurrentPositions.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      max_concurrent_positions: 5,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-option-5"));
    fireEvent.click(screen.getByTestId("paper-capital-card-max-positions-option-5"));
    await waitFor(() => {
      expect(backendApi.setPaperMaxConcurrentPositions).toHaveBeenCalledWith({
        maxConcurrentPositions: 5,
      });
    });
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-max-positions-current-value").textContent,
      ).toContain("5종목");
    });
  });

  it("clicking 10종목 calls setPaperMaxConcurrentPositions", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperMaxConcurrentPositions.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      max_concurrent_positions: 10,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-option-10"));
    fireEvent.click(screen.getByTestId("paper-capital-card-max-positions-option-10"));
    await waitFor(() => {
      expect(backendApi.setPaperMaxConcurrentPositions).toHaveBeenCalledWith({
        maxConcurrentPositions: 10,
      });
    });
  });

  it("disclaimer says paper-only and not live trade limit", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-max-positions-disclaimer"),
    );
    const text = screen.getByTestId("paper-capital-card-max-positions-disclaimer")
      .textContent || "";
    expect(text).toContain("Paper 모의매매 전용");
    expect(text).toContain("실전 주문 한도가 아닙니다");
    // 신규 진입만 차단되고 청산/관망은 영향 없음 명시 — 토큰 BUY/SELL/EXIT/HOLD
    // literal 은 *사용하지 않는다* (UI invariant 의 "no live trade labels" 와
    // 충돌 방지).
    expect(text).toContain("신규 진입");
    expect(text).toContain("청산");
  });

  it("shows current setting value", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      max_concurrent_positions: 10,
    });
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-max-positions-current-value"),
    );
    expect(
      screen.getByTestId("paper-capital-card-max-positions-current-value").textContent,
    ).toContain("10종목");
  });

  it("section has NO live-trade labels", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-section"));
    const section = screen.getByTestId("paper-capital-card-max-positions-section");
    const text = section.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    expect(section.querySelectorAll("input").length).toBe(0);
    expect(section.querySelectorAll("textarea").length).toBe(0);
    expect(section.querySelectorAll("select").length).toBe(0);
  });

  it("section does NOT expose secret patterns", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    const { container } = render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-section"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("highlights 5종목 when current setting is 5", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      max_concurrent_positions: 5,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-max-positions-option-5"));
    expect(
      screen.getByTestId("paper-capital-card-max-positions-option-5")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-max-positions-option-3")
        .getAttribute("data-selected"),
    ).toBe("false");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// P-05: 최소 1주 매수 + 예시 가능 수량 표
// ────────────────────────────────────────────────────────────────────────────
describe("<PaperCapitalCard> P-05 — min-lot preview", () => {
  it("renders min-lot section with disclaimer + examples + policy", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-section"));
    expect(screen.getByTestId("paper-capital-card-min-lot-disclaimer")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-min-lot-examples")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-min-lot-policy")).toBeTruthy();
  });

  it("renders 6 example price rows (50k / 100k / 500k / 1M / 2M / 5M)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-examples"));
    for (const price of [50_000, 100_000, 500_000, 1_000_000, 2_000_000, 5_000_000]) {
      expect(
        screen.getByTestId(`paper-capital-card-min-lot-example-price-${price}`),
      ).toBeTruthy();
      expect(
        screen.getByTestId(`paper-capital-card-min-lot-example-qty-${price}`),
      ).toBeTruthy();
    }
  });

  it("shows 1주 수량 for 1,000,000 KRW price (cap=1M)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-example-qty-1000000"));
    expect(
      screen.getByTestId("paper-capital-card-min-lot-example-qty-1000000").textContent,
    ).toContain("1주");
  });

  it("shows 0주 (1주 미만) for 2,000,000 KRW price (cap=1M)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-example-qty-2000000"));
    expect(
      screen.getByTestId("paper-capital-card-min-lot-example-qty-2000000").textContent,
    ).toContain("0주");
  });

  it("disclaimer says 정수 1주 이상 + 소수점 불가 + floor 정책", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-disclaimer"));
    const text = screen.getByTestId("paper-capital-card-min-lot-disclaimer").textContent || "";
    expect(text).toContain("정수 1주 이상");
    expect(text).toContain("소수점 주식 불가");
    expect(text).toContain("내림(floor)");
    // floor 정책 명시 — "반올림하지 않습니다" 같은 *부정형* 으로 사용. 단독
    // 긍정형 ("반올림 적용") 같은 표현은 없어야 함.
    expect(text).toContain("반올림하지 않습니다");
  });

  it("policy row says min_lot=1, fractional unsupported, rounding=floor", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-policy"));
    const text = screen.getByTestId("paper-capital-card-min-lot-policy").textContent || "";
    expect(text).toContain("1주");
    expect(text).toContain("지원 안 함");
    expect(text).toContain("floor");
  });

  it("min-lot section has NO trade-execution / input buttons", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-section"));
    const section = screen.getByTestId("paper-capital-card-min-lot-section");
    const text = section.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "BUY", "SELL", "HOLD",
      "실거래 시작", "실거래 활성화",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    expect(section.querySelectorAll("input").length).toBe(0);
    expect(section.querySelectorAll("textarea").length).toBe(0);
    expect(section.querySelectorAll("select").length).toBe(0);
    expect(section.querySelectorAll("button").length).toBe(0);
  });

  it("min-lot section secret 노출 0건", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-min-lot-section"));
    const text = (screen.getByTestId("paper-capital-card-min-lot-section").textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("min-lot preview is re-fetched after initial_cash change", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    backendApi.setPaperCapitalConfig.mockResolvedValue({
      ..._DEFAULT_CONFIG,
      initial_cash: 30_000_000,
    });
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-option-30000000"));
    backendApi.paperMinLotPreview.mockClear();
    fireEvent.click(screen.getByTestId("paper-capital-card-option-30000000"));
    await waitFor(() => {
      expect(backendApi.paperMinLotPreview).toHaveBeenCalled();
    });
  });

  it("min-lot section absent when preview not yet loaded", () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    // preview 가 *pending* — 응답 안 옴.
    backendApi.paperMinLotPreview.mockReturnValue(new Promise(() => {}));
    render(<PaperCapitalCard />);
    expect(
      screen.queryByTestId("paper-capital-card-min-lot-section"),
    ).toBeNull();
  });
});


// ────────────────────────────────────────────────────────────────────────────
// P-06: 고가주 처리 정책 (EXCLUDE / HOLD / INCREASE_BUDGET_HINT)
// ────────────────────────────────────────────────────────────────────────────
describe("<PaperCapitalCard> P-06 — high-price policy", () => {
  it("renders high-price section with 3 policy options", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-section"));
    expect(screen.getByTestId("paper-capital-card-high-price-option-EXCLUDE")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-card-high-price-option-HOLD")).toBeTruthy();
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-INCREASE_BUDGET_HINT"),
    ).toBeTruthy();
  });

  it("default selected policy is EXCLUDE (with '기본값' badge)", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-option-EXCLUDE"));
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-EXCLUDE")
        .getAttribute("data-selected"),
    ).toBe("true");
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-HOLD")
        .getAttribute("data-selected"),
    ).toBe("false");
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-INCREASE_BUDGET_HINT")
        .getAttribute("data-selected"),
    ).toBe("false");
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-default-badge").textContent,
    ).toContain("기본값");
  });

  it("renders EXCLUDE reason by default — '1주 가격이 투자한도 초과로 제외'", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-current-reason"));
    const reason = screen.getByTestId("paper-capital-card-high-price-current-reason-value")
      .textContent || "";
    expect(reason).toBe("1주 가격이 투자한도 초과로 제외");
  });

  it("clicking HOLD shows '1주 가격이 투자한도 초과로 보류'", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-option-HOLD"));
    fireEvent.click(screen.getByTestId("paper-capital-card-high-price-option-HOLD"));
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-high-price-current-reason-value").textContent,
      ).toBe("1주 가격이 투자한도 초과로 보류");
    });
    expect(
      screen.getByTestId("paper-capital-card-high-price-option-HOLD")
        .getAttribute("data-selected"),
    ).toBe("true");
  });

  it("clicking INCREASE_BUDGET_HINT shows '종목당 투자금 증액 필요' reason", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() =>
      screen.getByTestId("paper-capital-card-high-price-option-INCREASE_BUDGET_HINT"),
    );
    fireEvent.click(
      screen.getByTestId("paper-capital-card-high-price-option-INCREASE_BUDGET_HINT"),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-high-price-current-reason-value").textContent,
      ).toBe("1주 가격이 투자한도 초과: 종목당 투자금 증액 필요");
    });
  });

  it("disclaimer mentions 고가주 + 예시 100,000원 + 120,000원 + Paper 전용", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-disclaimer"));
    const text = screen.getByTestId("paper-capital-card-high-price-disclaimer")
      .textContent || "";
    expect(text).toContain("고가주");
    expect(text).toContain("100,000원");
    expect(text).toContain("120,000원");
    expect(text).toContain("Paper 전용");
  });

  it("all 3 required reason messages are present somewhere in the section", async () => {
    // 사용자 요청서 §5 — 화면이나 상태 메시지에 다음 중 하나가 표시되어야 함.
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-section"));
    const text = screen.getByTestId("paper-capital-card-high-price-section")
      .textContent || "";
    // EXCLUDE 메시지 (default) 는 current-reason 영역에 표시.
    expect(text).toContain("1주 가격이 투자한도 초과로 제외");
    // HOLD / HINT 메시지는 정책 chip 의 hint 또는 click 시 표시될 reason 이지만,
    // 본 카드는 default render 후 다른 정책 선택 시 표시 — 본 테스트는
    // EXCLUDE default 메시지 + 공통 fragment "1주 가격이 투자한도 초과" 존재
    // 만 lock (HOLD/HINT 메시지 click 후 표시는 위 별도 it 으로 검증).
    expect(text).toContain("1주 가격이 투자한도 초과");
  });

  it("high-price section has NO trade-execution / live-order buttons", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-section"));
    const section = screen.getByTestId("paper-capital-card-high-price-section");
    const text = section.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
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
  });

  it("high-price section does NOT expose secret patterns", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-section"));
    const text = (screen.getByTestId("paper-capital-card-high-price-section").textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-", "bearer ",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("clicking EXCLUDE after HOLD restores '제외' reason", async () => {
    backendApi.paperCapitalConfig.mockResolvedValue(_DEFAULT_CONFIG);
    render(<PaperCapitalCard />);
    await waitFor(() => screen.getByTestId("paper-capital-card-high-price-option-HOLD"));
    fireEvent.click(screen.getByTestId("paper-capital-card-high-price-option-HOLD"));
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-high-price-current-reason-value").textContent,
      ).toContain("보류");
    });
    fireEvent.click(screen.getByTestId("paper-capital-card-high-price-option-EXCLUDE"));
    await waitFor(() => {
      expect(
        screen.getByTestId("paper-capital-card-high-price-current-reason-value").textContent,
      ).toBe("1주 가격이 투자한도 초과로 제외");
    });
  });
});
