/**
 * PortfolioCard 단위 테스트.
 *
 * - Paper 현금 / 보유 종목 / 평가금액 / 총 자산 / 평가손익 표시.
 * - 실거래 / 매수 / 매도 / Place Order / ENABLE_* 버튼 0개.
 * - broker_order_sent=false 문구 + "실제 주문 아님" 배지.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PortfolioCard } from "./PortfolioCard";

function _mockApi(cash, positions = []) {
  return {
    paperCashState: vi.fn(async () => cash),
    virtualPositions: vi.fn(async () => positions),
  };
}

const _CASH_AFTER_BUY = {
  initial_cash_krw: 10_000_000,
  available_cash_krw: 9_025_000,
  invested_krw: 975_000,
  realized_pnl_krw: 0,
  buy_count: 1,
  sell_count: 0,
  is_order_signal: false,
  is_live_authorization: false,
  is_paper_only: true,
};

const _POSITION_005930 = {
  symbol: "005930",
  strategy: "ai_paper",
  quantity: 13,
  avg_price: 75_000,
  last_price: 75_000,
  unrealized_pnl: 0,
  unrealized_pct: 0,
  realized_pnl: 0,
};

describe("<PortfolioCard>", () => {
  afterEach(cleanup);

  it("renders paper portfolio badges + disclaimer", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, []);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(screen.getByTestId("portfolio-badge-paper").textContent).toMatch(/실제 주문 아님/);
    expect(screen.getByTestId("portfolio-badge-no-broker").textContent).toMatch(/broker_order_sent=false/);
    expect(screen.getByTestId("portfolio-disclaimer").textContent).toMatch(/broker 호출 0건/);
  });

  it("shows paper cash 9,025,000 after a buy", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, [_POSITION_005930]);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-cash").textContent).toMatch(/9,025,000/),
    );
  });

  it("shows 005930 holding 13주", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, [_POSITION_005930]);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-holding-005930")).toBeTruthy(),
    );
    expect(screen.getByTestId("portfolio-qty-005930").textContent).toMatch(/13주/);
  });

  it("shows position value 975,000 and total equity 10,000,000", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, [_POSITION_005930]);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-position-value").textContent).toMatch(/975,000/),
    );
    // 총 자산 = 현금 9,025,000 + 평가금액 975,000 = 10,000,000.
    expect(screen.getByTestId("portfolio-total-equity").textContent).toMatch(/10,000,000/);
  });

  it("empty positions → 보유 종목 없음 표시", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, []);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(screen.getByTestId("portfolio-empty")).toBeTruthy();
  });

  it("shows positive unrealized pnl when last_price > avg_price", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, [{
      ..._POSITION_005930, last_price: 80_000, unrealized_pnl: 65_000,
    }]);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-unrealized-pnl").textContent).toMatch(/\+/),
    );
    expect(screen.getByTestId("portfolio-unrealized-pnl").textContent).toMatch(/65,000/);
  });

  it("no buy/sell/place-order/live buttons anywhere", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, [_POSITION_005930]);
    const { container } = render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(container.querySelectorAll("button").length).toBe(0);
    const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기"];
    for (const b of banned) expect(container.textContent).not.toContain(b);
  });

  it("safe when virtualPositions missing (no throw)", async () => {
    const api = { paperCashState: vi.fn(async () => _CASH_AFTER_BUY) };
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(screen.getByTestId("portfolio-empty")).toBeTruthy();
  });

  it("shows error when cash fetch fails", async () => {
    const api = {
      paperCashState: vi.fn(async () => { throw new Error("backend down"); }),
      virtualPositions: vi.fn(async () => []),
    };
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-error").textContent).toMatch(/backend down/),
    );
  });
});
