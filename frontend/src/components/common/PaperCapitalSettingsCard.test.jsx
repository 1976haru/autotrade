/**
 * P-15: PaperCapitalSettingsCard 단위 테스트.
 *
 * invariant 강제:
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "실거래 활성화" /
 *    "ENABLE_*" / "BUY/SELL/HOLD" 라벨 button 0개
 *  - API key / secret / 계좌번호 입력 form 0개
 *  - allow_additional_buy default OFF
 *  - 안전 안내 영구 노출
 */

import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { PaperCapitalSettingsCard } from "./PaperCapitalSettingsCard";
import { PAPER_CAPITAL_SETTINGS_LS_KEY } from "../../store/usePaperCapitalSettings";


function _mkStorage() {
  const data = new Map();
  return {
    getItem:    (k) => (data.has(k) ? data.get(k) : null),
    setItem:    (k, v) => data.set(k, v),
    removeItem: (k) => data.delete(k),
    _dump:      () => Object.fromEntries(data),
  };
}


describe("<PaperCapitalSettingsCard>", () => {
  afterEach(cleanup);

  it("카드 + intro + safety badges 렌더링", () => {
    render(<PaperCapitalSettingsCard storage={_mkStorage()} />);
    expect(screen.getByTestId("paper-capital-settings-card")).toBeTruthy();
    expect(screen.getByTestId("paper-capital-settings-intro").textContent)
      .toMatch(/AI Paper/);
    expect(screen.getByTestId("badge-paper-only").textContent)
      .toMatch(/Paper/);
    expect(screen.getByTestId("badge-not-live-authorization").textContent)
      .toMatch(/실거래 활성화 아님/);
    expect(screen.getByTestId("badge-risk-still-applied").textContent)
      .toMatch(/RiskManager/);
  });

  it("6개 필드 모두 렌더링", () => {
    render(<PaperCapitalSettingsCard storage={_mkStorage()} />);
    [
      "field-total-paper-capital",
      "field-per-symbol-allocation",
      "field-max-positions",
      "field-max-daily-buy-amount",
      "field-max-symbol-weight-pct",
      "field-allow-additional-buy",
    ].forEach((id) => {
      expect(screen.getByTestId(id)).toBeTruthy();
    });
  });

  it("Paper 시드머니 preset 클릭", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-total-paper-capital-3,000만원"));
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.totalPaperCapital).toBe(30_000_000);
  });

  it("종목당 투자금 preset 100만원 클릭", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-per-symbol-allocation-100만원"));
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.perSymbolAllocation).toBe(1_000_000);
  });

  it("최대 보유 종목 수 preset 8개 클릭", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-max-positions-8개"));
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.maxPositions).toBe(8);
  });

  it("일일 최대 매수금액 preset 500만원 클릭", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-max-daily-buy-amount-500만원"));
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.maxDailyBuyAmount).toBe(5_000_000);
  });

  it("종목별 최대 비중 preset 30% 클릭", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-max-symbol-weight-pct-30%"));
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.maxSymbolWeightPct).toBeCloseTo(0.30);
  });

  it("기본 추가매수 OFF + ON 시 경고", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    const btn = screen.getByTestId("toggle-allow-additional-buy");
    expect(btn.textContent).toMatch(/비허용/);
    expect(screen.queryByTestId("additional-buy-warning")).toBeNull();
    fireEvent.click(btn);
    expect(screen.getByTestId("toggle-allow-additional-buy").textContent)
      .toMatch(/허용/);
    expect(screen.getByTestId("additional-buy-warning").textContent)
      .toMatch(/손실 확대/);
  });

  it("잘못된 값 입력 시 오류 메시지", () => {
    const storage = _mkStorage();
    // 시드머니가 100,000 보다 작게 입력되면 적용 안 되고 오류 표시.
    render(<PaperCapitalSettingsCard storage={storage} />);
    const input = screen.getByTestId("input-total-paper-capital");
    fireEvent.change(input, { target: { value: "50" } });
    expect(screen.getByTestId("paper-capital-settings-errors").textContent)
      .toMatch(/적용되지 않음/);
  });

  it("종목당 투자금 > 시드머니 오류", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    // 시드머니를 1,000,000 으로 낮춘 뒤 종목당 2,000,000 → error.
    fireEvent.change(screen.getByTestId("input-total-paper-capital"), {
      target: { value: "1000000" },
    });
    fireEvent.change(screen.getByTestId("input-per-symbol-allocation"), {
      target: { value: "2000000" },
    });
    expect(screen.getByTestId("paper-capital-settings-errors").textContent)
      .toMatch(/시드머니/);
  });

  it("reset 버튼 → default 복원", () => {
    const storage = _mkStorage();
    render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-max-positions-8개"));
    fireEvent.click(screen.getByTestId("paper-capital-settings-reset"));
    expect(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]).toBeUndefined();
  });

  it("재마운트 후 localStorage 값 복원", () => {
    const storage = _mkStorage();
    const { unmount } = render(<PaperCapitalSettingsCard storage={storage} />);
    fireEvent.click(screen.getByTestId("preset-total-paper-capital-5,000만원"));
    unmount();
    render(<PaperCapitalSettingsCard storage={storage} />);
    // 5,000만원 preset 이 active 상태인지 LS 로 확인.
    const raw = JSON.parse(storage._dump()[PAPER_CAPITAL_SETTINGS_LS_KEY]);
    expect(raw.totalPaperCapital).toBe(50_000_000);
  });

  it("API key / secret / 계좌번호 input 0개 (invariant)", () => {
    render(<PaperCapitalSettingsCard storage={_mkStorage()} />);
    const allInputs = document.querySelectorAll("input");
    allInputs.forEach((inp) => {
      const t = (inp.getAttribute("type") || "").toLowerCase();
      const ph = (inp.getAttribute("placeholder") || "").toLowerCase();
      // password / 계좌 / secret 류 placeholder / type 금지.
      expect(t).not.toBe("password");
      expect(ph).not.toMatch(/api\s*key/);
      expect(ph).not.toMatch(/secret/);
      expect(ph).not.toMatch(/계좌/);
      expect(ph).not.toMatch(/account/);
    });
  });

  it("실거래 / Place Order / 매수 라벨 button 0개 (invariant)", () => {
    render(<PaperCapitalSettingsCard storage={_mkStorage()} />);
    const buttons = Array.from(document.querySelectorAll("button"));
    const labels = buttons.map((b) => (b.textContent || "").trim());
    for (const txt of labels) {
      expect(txt).not.toMatch(/Place Order/i);
      expect(txt).not.toMatch(/실거래\s*시작/);
      expect(txt).not.toMatch(/실거래\s*활성화/);
      expect(txt).not.toMatch(/지금\s*매수/);
      expect(txt).not.toMatch(/지금\s*매도/);
      expect(txt).not.toMatch(/ENABLE_LIVE_TRADING/);
      expect(txt).not.toMatch(/ENABLE_AI_EXECUTION/);
      expect(txt).not.toMatch(/^BUY$/);
      expect(txt).not.toMatch(/^SELL$/);
      expect(txt).not.toMatch(/^HOLD$/);
    }
  });

  it("footer 안전 안내 영구 노출", () => {
    render(<PaperCapitalSettingsCard storage={_mkStorage()} />);
    expect(screen.getByTestId("paper-capital-settings-footer").textContent)
      .toMatch(/Paper.*기준/);
    expect(screen.getByTestId("paper-capital-settings-footer").textContent)
      .toMatch(/실거래 활성화 설정이 아닙니다/);
    expect(screen.getByTestId("paper-capital-settings-footer").textContent)
      .toMatch(/RiskManager/);
  });
});
