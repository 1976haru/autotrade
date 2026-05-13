import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CorrelationGuardCard } from "./CorrelationGuardCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    correlationGuardPreview: vi.fn(),
  },
}));


const _PASS = {
  verdict: "PASS",
  blocked_reasons: [],
  warnings: [],
  sector_exposure: { SEMI: 200000 },
  theme_exposure: { AI: 200000 },
  projected_sector: "SEMI",
  projected_themes: ["AI"],
  projected_sector_exposure: 300000,
  projected_sector_symbol_count: 2,
};


const _REJECT = {
  verdict: "REJECT",
  blocked_reasons: [
    "sector 'SEMI' 종목 수 3 > 2 — 동일 섹터 과집중.",
    "theme 'AI' 종목 수 3 > 2 — 테마 과집중.",
  ],
  warnings: [],
  sector_exposure: { SEMI: 400000 },
  theme_exposure: { AI: 400000 },
  projected_sector: "SEMI",
  projected_themes: ["AI"],
  projected_sector_exposure: 500000,
  projected_sector_symbol_count: 3,
};


const _SKIP_SELL = {
  verdict: "SKIP_NON_BUY",
  blocked_reasons: [],
  warnings: [],
  sector_exposure: { SEMI: 500000 },
  theme_exposure: {},
  projected_sector: null,
  projected_themes: [],
  projected_sector_exposure: 0,
  projected_sector_symbol_count: 0,
};


afterEach(cleanup);


describe("CorrelationGuardCard", () => {
  it("PASS 스냅샷에서 통과 배지 + sector / theme 노출 테이블", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_PASS} />,
    );
    expect(getByTestId("correlation-guard-verdict-PASS").textContent).toBe("통과");
    expect(getByTestId("correlation-guard-sector").textContent).toContain("SEMI");
    expect(getByTestId("correlation-guard-theme").textContent).toContain("AI");
  });

  it("REJECT 스냅샷에서 차단 사유 노출", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_REJECT} />,
    );
    expect(getByTestId("correlation-guard-verdict-REJECT").textContent).toBe("차단");
    expect(getByTestId("correlation-guard-blocked-list").textContent).toContain("sector");
  });

  it("SELL은 SKIP_NON_BUY 배지로 가드 우회 안내", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_SKIP_SELL} />,
    );
    const badge = getByTestId("correlation-guard-verdict-SKIP_NON_BUY");
    expect(badge.textContent).toBe("SELL — 가드 우회");
  });

  it("disclaimer가 SELL 우회와 RiskManager 흐름 명시", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_PASS} />,
    );
    const disc = getByTestId("correlation-guard-disclaimer").textContent;
    expect(disc).toContain("사전 검사");
    expect(disc).toContain("RiskManager");
    expect(disc).toContain("SELL");
  });

  it("실제 주문 / 정책 변경 / ENABLE_* 라벨 버튼 0개", () => {
    const { container } = render(
      <CorrelationGuardCard resultOverride={_PASS} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);  // preview 버튼 1개
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "주문 실행", "Place Order",
        "정책 적용", "Apply Policy",
        "RiskPolicy 적용",
        "AI 자동매매 활성화",
        "ENABLE_AI_EXECUTION",
        "ENABLE_LIVE_TRADING",
        "실거래 활성화",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("BUY/SELL/HOLD 주문 신호 문구 0건", () => {
    const { container } = render(
      <CorrelationGuardCard resultOverride={_REJECT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <CorrelationGuardCard resultOverride={_REJECT} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("preview 버튼 라벨이 'Correlation 사전 검사'", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_PASS} />,
    );
    const btn = getByTestId("correlation-guard-preview-btn");
    expect(btn.textContent.trim()).toBe("Correlation 사전 검사");
  });

  it("예상 sector 노출 / 종목 수가 표시", () => {
    const { getByTestId } = render(
      <CorrelationGuardCard resultOverride={_PASS} />,
    );
    const proj = getByTestId("correlation-guard-projection");
    expect(proj.textContent).toContain("SEMI");
    expect(proj.textContent).toContain("2");           // projected_sector_symbol_count
    expect(proj.textContent).toContain("300,000");     // projected_sector_exposure
  });
});
