import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../../services/backend/client";
import { ThemeSignalsCard } from "./ThemeSignalsCard";
import { ThemeSummaryTile } from "./ThemeSummaryTile";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    themeSignals:  vi.fn(),
    themesScan:    vi.fn(),
    themesSummary: vi.fn(),
  },
}));


function _resetMocks() {
  Object.values(backendApi).forEach((fn) => fn?.mockReset?.());
  backendApi.themeSignals.mockResolvedValue({ signals: [], used_for_order: false });
  backendApi.themesSummary.mockResolvedValue({
    total: 0, by_grade: {}, top_themes: [], used_for_order: false,
  });
}


describe("ThemeSignalsCard", () => {
  beforeEach(_resetMocks);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("always shows the '주문 신호 아님' badge", async () => {
    render(<ThemeSignalsCard />);
    await waitFor(() => screen.getByTestId("theme-signals-card"));
    const badge = screen.getByTestId("theme-card-not-order-badge");
    expect(badge.textContent).toContain("주문 신호 아님");
  });

  it("does NOT render BUY/SELL/HOLD buttons (CLAUDE.md invariant)", async () => {
    backendApi.themeSignals.mockResolvedValue({
      signals: [{
        id: 1, theme: "AI", grade: "STRONG", score: 90,
        provider: "mock", source: "trends", confidence: 80,
        related_symbols: ["005930"], keywords: ["HBM"],
        summary: "test", used_for_order: false,
      }],
      used_for_order: false,
    });

    render(<ThemeSignalsCard />);
    await waitFor(() => screen.getByText("AI"));

    const card = screen.getByTestId("theme-signals-card");
    const text = card.textContent;
    expect(text).not.toMatch(/매수\s*$|매수\s*\(|^\s*매수\s*$/);  // 단독 매수 버튼이 없는지
    // BUY/SELL/HOLD 영문 버튼도 없음
    expect(card.querySelector("button[data-action='buy']")).toBeNull();
    expect(card.querySelector("button[data-action='sell']")).toBeNull();
  });

  it("shows the empty state when no signals", async () => {
    render(<ThemeSignalsCard />);
    await waitFor(() => screen.getByTestId("theme-signals-empty"));
  });

  it("Mock 스캔 button populates candidates and scanMsg", async () => {
    backendApi.themesScan.mockResolvedValue({
      persisted: 3, records: [],
      candidate_symbols: [
        { symbol: "005930", themes: ["AI"], best_score: 90, best_grade: "STRONG" },
      ],
      provider: "mock", is_provider_enabled: true, used_for_order: false,
    });
    backendApi.themeSignals
      .mockResolvedValueOnce({ signals: [], used_for_order: false })
      .mockResolvedValueOnce({
        signals: [{ id: 1, theme: "AI", grade: "STRONG", score: 90,
                    provider: "mock", source: "trends", confidence: 80,
                    related_symbols: ["005930"], keywords: ["HBM"],
                    used_for_order: false }],
        used_for_order: false,
      });

    render(<ThemeSignalsCard />);
    await waitFor(() => screen.getByTestId("theme-signals-empty"));

    fireEvent.click(screen.getByText("Mock 스캔"));
    await waitFor(() => screen.getByTestId("theme-scan-msg"));

    expect(screen.getByTestId("theme-scan-msg").textContent).toMatch(/완료/);
    expect(screen.getByTestId("theme-candidate-005930")).toBeTruthy();
  });

  it("renders provider name + universe-not-signal explanation", async () => {
    render(<ThemeSignalsCard />);
    await waitFor(() => screen.getByTestId("theme-signals-card"));
    const card = screen.getByTestId("theme-signals-card");
    expect(card.textContent).toMatch(/universe 후보 필터/);
    expect(card.textContent).toMatch(/RiskManager.*PermissionGate.*OrderExecutor/);
  });
});


describe("ThemeSummaryTile", () => {
  beforeEach(_resetMocks);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("shows '주문 신호 아님' badge in dashboard tile", async () => {
    render(<ThemeSummaryTile />);
    await waitFor(() => screen.getByTestId("theme-summary-tile"));
    expect(screen.getByTestId("theme-summary-not-order-badge")).toBeTruthy();
  });

  it("renders top themes when summary has STRONG entries", async () => {
    backendApi.themesSummary.mockResolvedValue({
      total: 5,
      by_grade: { STRONG: 2, WATCH: 3 },
      top_themes: [
        { theme: "AI", score: 90, grade: "STRONG",
          provider: "mock", related_symbols: ["005930"] },
        { theme: "2차 전지", score: 75, grade: "STRONG",
          provider: "mock", related_symbols: ["247540"] },
      ],
      used_for_order: false,
    });

    render(<ThemeSummaryTile />);
    await waitFor(() => screen.getByText(/총 5건/));

    expect(screen.getByTestId("theme-summary-top-AI")).toBeTruthy();
    expect(screen.getByTestId("theme-summary-top-2차 전지")).toBeTruthy();
  });

  it("empty summary shows hint message", async () => {
    render(<ThemeSummaryTile />);
    await waitFor(() => screen.getByTestId("theme-summary-tile"));
    const tile = screen.getByTestId("theme-summary-tile");
    expect(tile.textContent).toMatch(/아직 테마 신호가 없습니다/);
  });
});
