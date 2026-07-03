import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { ManualPortfolioCard } from "./ManualPortfolioCard";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ── API 픽스처 ───────────────────────────────────────────────────────────────

const HOLDING = {
  symbol: "005930", name: "삼성전자", quantity: 10,
  avg_price: 70000, current_price: 75000,
  market_value: 750000, cost_basis: 700000,
  unrealized_pnl: 50000, unrealized_pnl_pct: 7.14,
  first_bought_at: "2026-06-30", holding_days: 4,
  weight_pct: 100.0,
};

const SUMMARY_RESP = {
  period: "today",
  period_from: "2026-07-03",
  period_to: "2026-07-03",
  holdings: [HOLDING],
  summary: {
    position_count: 1,
    total_market_value: 750000,
    total_cost: 700000,
    total_unrealized_pnl: 50000,
    total_unrealized_pnl_pct: 7.14,
    period_realized_pnl: 0,
  },
  no_price_count: 0,
  data_note: "체결 기준 손익.",
};

const EMPTY_RESP = {
  period: "today",
  period_from: "2026-07-03",
  period_to: "2026-07-03",
  holdings: [],
  summary: { position_count: 0, total_market_value: 0, total_cost: 0,
    total_unrealized_pnl: 0, total_unrealized_pnl_pct: 0, period_realized_pnl: 0 },
  no_price_count: 0,
  data_note: "체결 기준 손익.",
};

const makeApi = (resp = SUMMARY_RESP, sellResp = { message: "주문 접수" }) => ({
  manualPortfolioSummary: vi.fn().mockResolvedValue(resp),
  manualOrder: vi.fn().mockResolvedValue(sellResp),
});

// ── 렌더 테스트 ───────────────────────────────────────────────────────────────

describe("ManualPortfolioCard", () => {
  it("renders root testid", async () => {
    const { getByTestId } = render(<ManualPortfolioCard api={makeApi()} />);
    await waitFor(() => getByTestId("mhp-root"));
    expect(getByTestId("mhp-root")).toBeTruthy();
  });

  it("shows empty state when no holdings", async () => {
    const { getByTestId } = render(<ManualPortfolioCard api={makeApi(EMPTY_RESP)} />);
    await waitFor(() => getByTestId("mhp-empty"));
    expect(getByTestId("mhp-empty").textContent).toContain("직접 보유");
  });

  it("renders period tabs", async () => {
    const { getByTestId } = render(<ManualPortfolioCard api={makeApi()} />);
    await waitFor(() => getByTestId("mhp-holdings-table"));
    for (const k of ["today", "1w", "1m", "custom"]) {
      expect(getByTestId(`mhp-period-${k}`)).toBeTruthy();
    }
  });

  it("calls API on mount", async () => {
    const api = makeApi();
    render(<ManualPortfolioCard api={api} />);
    await waitFor(() => expect(api.manualPortfolioSummary).toHaveBeenCalledWith({ period: "today" }));
  });

  it("re-calls API when period tab changes", async () => {
    const api = makeApi();
    const { getByTestId } = render(<ManualPortfolioCard api={api} />);
    await waitFor(() => getByTestId("mhp-holdings-table"));
    fireEvent.click(getByTestId("mhp-period-1w"));
    await waitFor(() => expect(api.manualPortfolioSummary).toHaveBeenCalledWith({ period: "1w" }));
  });

  // ── 보유 테이블 ────────────────────────────────────────────────────────────

  it("renders holding row with symbol", async () => {
    const { getByTestId } = render(<ManualPortfolioCard api={makeApi()} />);
    await waitFor(() => getByTestId("mhp-row-005930"));
    expect(getByTestId("mhp-row-005930").textContent).toContain("삼성전자");
  });

  it("renders summary card values", async () => {
    const { getByTestId } = render(<ManualPortfolioCard api={makeApi()} />);
    await waitFor(() => getByTestId("mhp-summary-value"));
    expect(getByTestId("mhp-summary-value").textContent).toContain("750,000");
  });

  // ── 매도 버튼 + 모달 ───────────────────────────────────────────────────────

  it("sell button opens modal", async () => {
    const { getByTestId, queryByTestId } = render(<ManualPortfolioCard api={makeApi()} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    expect(queryByTestId("mhp-sell-modal")).toBeNull();
    fireEvent.click(getByTestId("mhp-sell-005930"));
    expect(getByTestId("mhp-sell-modal")).toBeTruthy();
  });

  it("cancel closes modal without API call", async () => {
    const api = makeApi();
    const { getByTestId, queryByTestId } = render(<ManualPortfolioCard api={api} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    fireEvent.click(getByTestId("mhp-sell-cancel"));
    expect(queryByTestId("mhp-sell-modal")).toBeNull();
    expect(api.manualOrder).not.toHaveBeenCalled();
  });

  it("full sell confirms and calls manualOrder with full qty", async () => {
    const api = makeApi();
    const confirmFn = vi.fn().mockReturnValue(true);
    const { getByTestId } = render(<ManualPortfolioCard api={api} confirmFn={confirmFn} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    // default is 'full'
    fireEvent.click(getByTestId("mhp-sell-confirm"));
    await waitFor(() => expect(api.manualOrder).toHaveBeenCalledWith({
      symbol: "005930", side: "SELL", quantity: 10,
    }));
  });

  it("half sell calls manualOrder with floor(qty/2)", async () => {
    const api = makeApi();
    const confirmFn = vi.fn().mockReturnValue(true);
    const { getByTestId } = render(<ManualPortfolioCard api={api} confirmFn={confirmFn} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    fireEvent.click(getByTestId("mhp-sell-half"));
    fireEvent.click(getByTestId("mhp-sell-confirm"));
    await waitFor(() => expect(api.manualOrder).toHaveBeenCalledWith({
      symbol: "005930", side: "SELL", quantity: 5,
    }));
  });

  it("custom sell uses input qty", async () => {
    const api = makeApi();
    const confirmFn = vi.fn().mockReturnValue(true);
    const { getByTestId } = render(<ManualPortfolioCard api={api} confirmFn={confirmFn} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    fireEvent.click(getByTestId("mhp-sell-custom"));
    fireEvent.change(getByTestId("mhp-sell-qty-input"), { target: { value: "3" } });
    fireEvent.click(getByTestId("mhp-sell-confirm"));
    await waitFor(() => expect(api.manualOrder).toHaveBeenCalledWith({
      symbol: "005930", side: "SELL", quantity: 3,
    }));
  });

  it("confirm=false skips order and closes modal", async () => {
    const api = makeApi();
    const confirmFn = vi.fn().mockReturnValue(false);
    const { getByTestId, queryByTestId } = render(<ManualPortfolioCard api={api} confirmFn={confirmFn} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    fireEvent.click(getByTestId("mhp-sell-confirm"));
    expect(api.manualOrder).not.toHaveBeenCalled();
    expect(queryByTestId("mhp-sell-modal")).toBeNull();
  });

  it("sell success calls manualOrder and shows note", async () => {
    const api = makeApi();
    const confirmFn = vi.fn().mockReturnValue(true);
    const { getByTestId } = render(<ManualPortfolioCard api={api} confirmFn={confirmFn} />);
    await waitFor(() => getByTestId(`mhp-sell-005930`));
    fireEvent.click(getByTestId("mhp-sell-005930"));
    fireEvent.click(getByTestId("mhp-sell-confirm"));
    await waitFor(() => expect(api.manualOrder).toHaveBeenCalledWith({
      symbol: "005930", side: "SELL", quantity: 10,
    }));
    // 매도 후 노트가 뜨는 것 확인 (setTimeout reload은 integration 범위)
    await waitFor(() => getByTestId("mhp-sell-note-005930"));
    expect(getByTestId("mhp-sell-note-005930").textContent).toContain("주문");
  });

  it("API error shows error message", async () => {
    const api = { manualPortfolioSummary: vi.fn().mockRejectedValue(new Error("net")) };
    const { getByTestId } = render(<ManualPortfolioCard api={api} />);
    await waitFor(() => getByTestId("mhp-error"));
    expect(getByTestId("mhp-error").textContent).toContain("실패");
  });

  it("no manualPortfolioSummary in api → renders root without crash", () => {
    const { getByTestId } = render(<ManualPortfolioCard api={{}} />);
    expect(getByTestId("mhp-root")).toBeTruthy();
  });
});
