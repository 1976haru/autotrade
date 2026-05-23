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

  it("intro + safety text mentions 실제 계좌 잔고가 아닙니다", async () => {
    const api = _mockApi(_CASH_AFTER_BUY, []);
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(screen.getByTestId("portfolio-intro").textContent)
      .toMatch(/실제 계좌 잔고가 아닙니다/);
    expect(screen.getByTestId("portfolio-disclaimer").textContent)
      .toMatch(/실제 계좌 잔고가 아닙니다/);
  });
});


// ── P-18: 종합 요약(autoPaperPortfolio) 모드 ──

const _SUMMARY = {
  starting_cash: 10_000_000,
  current_cash: 7_600_000,
  positions: [
    {
      symbol: "005930", strategy: "ai_paper", quantity: 13,
      average_price: 75_000, current_price: 80_000,
      market_value: 1_040_000, cost_basis: 975_000,
      unrealized_pnl: 65_000, unrealized_pnl_pct: 0.0667,
      portfolio_weight_pct: 0.12, max_symbol_weight_pct: 0.2,
      symbol_weight_status: "OK",
    },
    {
      symbol: "000660", strategy: "ai_paper", quantity: 5,
      average_price: 200_000, current_price: 190_000,
      market_value: 950_000, cost_basis: 1_000_000,
      unrealized_pnl: -50_000, unrealized_pnl_pct: -0.05,
      portfolio_weight_pct: 0.55, max_symbol_weight_pct: 0.2,
      symbol_weight_status: "EXCEEDED",
    },
  ],
  total_position_value: 1_990_000,
  total_equity: 9_590_000,
  total_unrealized_pnl: 15_000,
  total_unrealized_pnl_pct: 0.0076,
  today_buy_used_amount: 2_400_000,
  remaining_daily_buy_amount: 600_000,
  max_daily_buy_amount: 3_000_000,
  max_positions: 5,
  max_symbol_weight_pct: 0.2,
  position_count: 2,
  available_position_slots: 3,
  is_order_signal: false, is_live_authorization: false, contains_secret: false,
  is_paper_only: true,
};

function _summaryApi(summary = _SUMMARY) {
  return { autoPaperPortfolio: vi.fn(async () => summary) };
}

describe("<PortfolioCard> P-18 요약 모드", () => {
  afterEach(cleanup);

  it("autoPaperPortfolio 우선 사용 (legacy 조회 생략)", async () => {
    const api = {
      autoPaperPortfolio: vi.fn(async () => _SUMMARY),
      paperCashState: vi.fn(async () => _CASH_AFTER_BUY),
      virtualPositions: vi.fn(async () => []),
    };
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperPortfolio).toHaveBeenCalled());
    expect(api.paperCashState).not.toHaveBeenCalled();
  });

  it("현재 현금 / 총 자산 / 평가손익(%) 표시", async () => {
    render(<PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-cash").textContent).toMatch(/7,600,000/));
    expect(screen.getByTestId("portfolio-total-equity").textContent).toMatch(/9,590,000/);
    expect(screen.getByTestId("portfolio-unrealized-pnl").textContent).toMatch(/15,000/);
    expect(screen.getByTestId("portfolio-unrealized-pnl").textContent).toMatch(/%/);
  });

  it("오늘 매수 사용 / 남은 일일 매수 / 보유·최대 종목 표시", async () => {
    render(<PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-today-buy-used").textContent).toMatch(/2,400,000/));
    expect(screen.getByTestId("portfolio-remaining-daily-buy").textContent).toMatch(/600,000/);
    expect(screen.getByTestId("portfolio-position-slots").textContent).toMatch(/2 \/ 5/);
  });

  it("보유 종목 테이블 — 수량/평균단가/현재가/평가금액/비중/상태", async () => {
    render(<PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-positions-table")).toBeTruthy());
    const row = screen.getByTestId("portfolio-holding-005930");
    expect(screen.getByTestId("portfolio-qty-005930").textContent).toMatch(/13주/);
    expect(row.textContent).toMatch(/75,000/);   // 평균단가
    expect(row.textContent).toMatch(/80,000/);   // 현재가
    expect(row.textContent).toMatch(/12\.0%/);   // 비중
  });

  it("종목 비중 초과 시 EXCEEDED 상태 표시", async () => {
    render(<PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-holding-000660")).toBeTruthy());
    expect(screen.getByTestId("portfolio-weight-status-EXCEEDED").textContent)
      .toMatch(/초과/);
    expect(screen.getByTestId("portfolio-weight-status-OK").textContent).toMatch(/정상/);
  });

  it("손실 포지션은 빨강/음수로 표시", async () => {
    render(<PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-holding-000660")).toBeTruthy());
    expect(screen.getByTestId("portfolio-holding-000660").textContent).toMatch(/-50,000/);
  });

  it("빈 포지션 요약 → empty state", async () => {
    const empty = { ..._SUMMARY, positions: [], position_count: 0,
      total_position_value: 0, total_equity: 7_600_000 };
    render(<PortfolioCard apiClient={_summaryApi(empty)} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-empty").textContent)
        .toMatch(/현재 보유 중인 Paper 포지션이 없습니다/));
  });

  it("요약 모드에서도 버튼 0개 + 금지 라벨 없음", async () => {
    const { container } = render(
      <PortfolioCard apiClient={_summaryApi()} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("portfolio-positions-table")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    for (const b of ["Place Order", "지금 매수", "지금 매도", "실거래 시작",
                     "ENABLE_LIVE_TRADING"]) {
      expect(container.textContent).not.toContain(b);
    }
  });

  it("요약 실패 시 legacy fallback (현금 표시 유지)", async () => {
    const api = {
      autoPaperPortfolio: vi.fn(async () => { throw new Error("no summary"); }),
      paperCashState: vi.fn(async () => _CASH_AFTER_BUY),
      virtualPositions: vi.fn(async () => []),
    };
    render(<PortfolioCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.paperCashState).toHaveBeenCalled());
    expect(screen.getByTestId("portfolio-cash").textContent).toMatch(/9,025,000/);
  });
});
