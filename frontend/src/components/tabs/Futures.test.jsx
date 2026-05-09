import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Futures, FuturesDisabledNotice } from "./Futures";

// `Futures` 본 화면은 `FuturesMarginRiskCard`와 `FuturesOrderAuditCard`를
// 마운트한다. 본 테스트는 *Futures 자체*의 안전 정책만 검증하므로 그 둘은
// 가벼운 stub로 mock 한다.
vi.mock("./FuturesMarginRiskCard", () => ({
  FuturesMarginRiskCard: () => (
    <div data-testid="mock-futures-margin-card">margin card</div>
  ),
}));
vi.mock("./FuturesOrderAuditCard", () => ({
  FuturesOrderAuditCard: () => (
    <div data-testid="mock-futures-audit-card">audit card</div>
  ),
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {});


// ====================================================================
// 1. Confusion-prevention banner + disabled banner badges
// ====================================================================


describe("<Futures>", () => {
  it("renders 주식/선물 혼동 방지 banner at top", () => {
    const { getByTestId } = render(<Futures />);
    const banner = getByTestId("futures-confusion-banner");
    expect(banner.textContent).toMatch(/주식 자동매매 화면이 \*아닙니다\*/);
  });

  it("renders disabled banner with all four safety badges", () => {
    const { getByTestId } = render(<Futures />);
    expect(getByTestId("futures-disabled-banner").textContent)
      .toMatch(/선물 기능은 현재 비활성화/);
    expect(getByTestId("futures-badge-simulation-only").textContent)
      .toContain("Simulation Only");
    expect(getByTestId("futures-badge-readonly").textContent)
      .toContain("Read-only");
    expect(getByTestId("futures-badge-live-off").textContent)
      .toContain("FUTURES_LIVE OFF");
    expect(getByTestId("futures-badge-zero-orders").textContent)
      .toContain("실제 주문 0건");
  });

  it("renders 6 risk warning items (leverage / margin / liquidation / expiry / overnight / AI)", () => {
    const { getByTestId } = render(<Futures />);
    const warning = getByTestId("futures-risk-warning");
    expect(warning.textContent).toMatch(/레버리지/);
    expect(warning.textContent).toMatch(/증거금/);
    expect(warning.textContent).toMatch(/강제청산/);
    expect(warning.textContent).toMatch(/만기/);
    expect(warning.textContent).toMatch(/야간/);
    expect(warning.textContent).toMatch(/AI 자동매매/);
  });

  it("renders safety matrix with all expected rows", () => {
    const { getByTestId } = render(<Futures />);
    const m = getByTestId("futures-safety-matrix");
    expect(m.textContent).toContain("ENABLE_FUTURES_LIVE_TRADING");
    expect(m.textContent).toContain("MockFuturesBroker");
    expect(m.textContent).toContain("FuturesRiskManager");
    expect(m.textContent).toContain("AI 선물 실행");
    expect(m.textContent).toContain("Manual approval required");
  });

  it("renders activation checklist with 8 items", () => {
    const { getByTestId } = render(<Futures />);
    const list = getByTestId("futures-activation-checklist");
    const items = list.querySelectorAll("ol > li");
    expect(items.length).toBe(8);
    expect(list.textContent).toContain("주식 MVP");
    expect(list.textContent).toContain("opt-in");
  });

  it("renders read-only sub-cards (margin + audit)", () => {
    const { getByTestId } = render(<Futures />);
    expect(getByTestId("mock-futures-margin-card")).toBeTruthy();
    expect(getByTestId("mock-futures-audit-card")).toBeTruthy();
  });
});


// ====================================================================
// 2. Disabled order area — buttons inactive, no real order
// ====================================================================


describe("<Futures> disabled order area", () => {
  it("renders three disabled order buttons", () => {
    const { getByTestId } = render(<Futures />);
    expect(getByTestId("futures-disabled-buy").disabled).toBe(true);
    expect(getByTestId("futures-disabled-sell").disabled).toBe(true);
    expect(getByTestId("futures-disabled-close").disabled).toBe(true);
  });

  it("disabled buttons have '비활성' label", () => {
    const { getByTestId } = render(<Futures />);
    expect(getByTestId("futures-disabled-buy").textContent).toContain("비활성");
    expect(getByTestId("futures-disabled-sell").textContent).toContain("비활성");
    expect(getByTestId("futures-disabled-close").textContent).toContain("비활성");
  });

  it("clicking a disabled button does NOT trigger any action", () => {
    const { getByTestId } = render(<Futures />);
    // disabled button — fireEvent.click should be a no-op (no error, no
    // side effect). 본 테스트는 invariant 자체를 명시.
    fireEvent.click(getByTestId("futures-disabled-buy"));
    fireEvent.click(getByTestId("futures-disabled-sell"));
    fireEvent.click(getByTestId("futures-disabled-close"));
    // Still disabled after clicks.
    expect(getByTestId("futures-disabled-buy").disabled).toBe(true);
  });
});


// ====================================================================
// 3. Visual safety — no green primary CTA / enable buttons
// ====================================================================


describe("<Futures> visual safety invariants", () => {
  it("does NOT render any '활성화' / 'enable' button (must stay simulation-only)", () => {
    const { container } = render(<Futures />);
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      // 비활성 버튼만 허용 — disabled 또는 label에 '비활성' 포함.
      const label = btn.textContent || "";
      // futures-margin-evaluate-btn이 mock 카드에 있을 수 있어 mock 화이트리스트.
      // 본 테스트는 *enabling* 버튼이 없는지를 검증하는 것 — disabled 또는
      // sub-card 마운트 버튼은 패스.
      const isFuturesOrderBtn = btn.dataset?.testid?.startsWith("futures-disabled-");
      if (isFuturesOrderBtn) {
        expect(btn.disabled).toBe(true);
      }
      // 위험 키워드가 적힌 enabled 버튼이 있으면 fail.
      if (!btn.disabled && /활성화|enable.*futures|Enable Futures/i.test(label)) {
        throw new Error(
          `enabled button with activation label found: '${label}'`
        );
      }
    }
  });

  it("does NOT render green primary CTA labeled '주문 실행'", () => {
    const { container } = render(<Futures />);
    expect(container.textContent).not.toMatch(/주문\s*실행\s*시작/);
  });
});


// ====================================================================
// 4. <FuturesDisabledNotice> — forced-access fallback
// ====================================================================


describe("<FuturesDisabledNotice>", () => {
  it("renders friendly disabled notice for forced access", () => {
    const { getByTestId } = render(<FuturesDisabledNotice />);
    const notice = getByTestId("futures-disabled-notice");
    expect(notice.textContent).toMatch(/선물 기능 비활성화|UI 노출 차단/);
  });

  it("references the VITE_ENABLE_FUTURES_TAB env var", () => {
    const { container } = render(<FuturesDisabledNotice />);
    expect(container.textContent).toContain("VITE_ENABLE_FUTURES_TAB");
  });

  it("includes the confusion-prevention banner", () => {
    const { getByTestId } = render(<FuturesDisabledNotice />);
    expect(getByTestId("futures-confusion-banner")).toBeTruthy();
  });

  it("does NOT render any order buttons (disabled or otherwise)", () => {
    const { queryByTestId } = render(<FuturesDisabledNotice />);
    expect(queryByTestId("futures-disabled-buy")).toBeNull();
    expect(queryByTestId("futures-disabled-sell")).toBeNull();
    expect(queryByTestId("futures-disabled-close")).toBeNull();
  });
});
