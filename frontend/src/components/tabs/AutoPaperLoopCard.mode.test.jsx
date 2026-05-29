/**
 * AutoPaperLoopCard — V2 운용 모드 표시 테스트.
 *
 * 4 모드(VIRTUAL_ONLY / KIS_REALTIME_DRYRUN / KIS_REALTIME_PAPER_AUTO /
 * KIS_REALTIME_SMOKE_TEST) 구분 + smoke vs 정상 Paper Auto 문구 + mock 차단 안내.
 * 주문/실거래 버튼 0건 invariant.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { AutoPaperLoopCard } from "./AutoPaperLoopCard";

beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* jsdom */ }
});
afterEach(cleanup);

function _api(paperAutoMode, paperAutoLimits) {
  const status = { state: "WAITING_MARKET", cycle_count: 0 };
  return {
    autoPaperStatus: vi.fn(async () => status),
    autoPaperLedger: vi.fn(async () => ({ events: [], event_count: 0 })),
    desktopHealth: vi.fn(async () => ({
      ok: true,
      safety_flags: { enable_live_trading: false, enable_ai_execution: false,
        enable_futures_live_trading: false, kis_is_paper: true },
      auto_paper: status,
    })),
    autoPaperRunReadiness: vi.fn(async () => ({
      loop: { state: "WAITING_MARKET", cycle_count: 0 },
      market_session: { phase: "PRE_OPEN", is_open: false },
      universe: { source: "DEFAULT_UNIVERSE_50", count: 50 },
      market_data: { provider: paperAutoMode.market_data_provider, is_mock: paperAutoMode.market_data_provider === "mock" },
      paper_auto_mode: paperAutoMode,
      paper_auto_limits: paperAutoLimits,
      permission: { live_execution_blocked: true },
      paper_capital: { available_cash_krw: 10000000 },
    })),
  };
}

const LIMITS = {
  smoke_mode: false, smoke_symbol: "005930", smoke_qty: 1,
  max_concurrent_positions: 5, per_symbol_notional_krw: 1000000,
  daily_buy_limit_krw: 3000000, max_new_positions_per_tick: 1,
  max_orders_per_day: 10, scan_max_symbols: 10,
};

describe("AutoPaperLoopCard V2 mode display", () => {
  it("renders KIS_REALTIME_PAPER_AUTO with multi-symbol wording", async () => {
    const api = _api(
      { mode: "KIS_REALTIME_PAPER_AUTO", price_source: "kis", market_data_provider: "kis",
        kis_realtime: true, broker_order_enabled: true, dry_run: false, smoke_mode: false },
      LIMITS,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    const v = await screen.findByTestId("paper-auto-mode-value");
    expect(v.textContent).toBe("KIS_REALTIME_PAPER_AUTO");
    const block = await screen.findByTestId("paper-auto-mode");
    expect(block.getAttribute("data-price-source")).toBe("kis");
    expect(block.textContent).toMatch(/여러 종목/);
    expect(block.textContent).not.toMatch(/1주/);
  });

  it("renders KIS_REALTIME_SMOKE_TEST with 1종목/1주/1건 restriction", async () => {
    const api = _api(
      { mode: "KIS_REALTIME_SMOKE_TEST", price_source: "kis", market_data_provider: "kis",
        kis_realtime: true, broker_order_enabled: true, dry_run: false, smoke_mode: true },
      { ...LIMITS, smoke_mode: true },
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    const block = await screen.findByTestId("paper-auto-mode");
    expect(block.textContent).toMatch(/1종목 \/ 1주 \/ 1건/);
  });

  it("renders VIRTUAL_ONLY (mock) with no-mock-order note", async () => {
    const api = _api(
      { mode: "VIRTUAL_ONLY", price_source: "mock", market_data_provider: "mock",
        kis_realtime: false, broker_order_enabled: false, dry_run: false, smoke_mode: false },
      LIMITS,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    const note = await screen.findByTestId("paper-auto-mode-mock-note");
    expect(note.textContent).toMatch(/mock 시세 주문 금지/);
  });

  it("has no live/order buttons in the mode block", async () => {
    const api = _api(
      { mode: "KIS_REALTIME_PAPER_AUTO", price_source: "kis", market_data_provider: "kis",
        kis_realtime: true, broker_order_enabled: true, dry_run: false, smoke_mode: false },
      LIMITS,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    const block = await screen.findByTestId("paper-auto-mode");
    expect(block.querySelectorAll("button").length).toBe(0);
    expect(block.textContent).not.toMatch(/Place Order|지금 매수|실거래 시작/);
  });
});
