import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ManualHoldingsCard } from "./ManualHoldingsCard";

afterEach(() => cleanup());

const _confirmYes = () => true;
const _confirmNo  = () => false;

const live = (over = {}) => ({
  available: true,
  bot_isolation_active: false,
  manual_isolation_notice: "봇 격리 검증 후 사용하세요.",
  positions: [
    { symbol: "005930", name: "삼성전자", quantity: 10, avg_price: 70000, market_price: 77000, source: "BOT", bot_qty: 10, manual_qty: 0 },
    { symbol: "033780", name: "KT&G", quantity: 5, avg_price: 80000, market_price: 84000, source: "MANUAL", bot_qty: 0, manual_qty: 5, _qty: 5, return_pct: 5.0 },
  ],
  ...over,
});

describe("ManualHoldingsCard (설계 B)", () => {
  // ── 섹션/격리 ─────────────────────────────────────────────

  it("봇 격리 미작동 경고 표시(bot_isolation_active=false)", () => {
    const { getByTestId } = render(<ManualHoldingsCard live={live()} api={{}} />);
    expect(getByTestId("mh-isolation-warning").textContent).toContain("격리하지 못해요");
  });

  it("격리 작동(true)이면 경고 미표시", () => {
    const { queryByTestId } = render(<ManualHoldingsCard live={live({ bot_isolation_active: true })} api={{}} />);
    expect(queryByTestId("mh-isolation-warning")).toBeNull();
  });

  it("2섹션 분리 — 봇/직접", () => {
    const { getByTestId } = render(<ManualHoldingsCard live={live()} api={{}} />);
    expect(getByTestId("mh-section-BOT").textContent).toContain("삼성전자");
    expect(getByTestId("mh-section-MANUAL").textContent).toContain("KT&G");
  });

  // ── 직접 매수 (manualOrder) ───────────────────────────────

  it("직접 매수 → api.manualOrder BUY 호출 + 결과 표시", async () => {
    const manualOrder = vi.fn(async () => ({ message: "KT&G 3주 직접 매수 주문을 보냈어요." }));
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmYes} />
    );
    fireEvent.change(getByTestId("mh-symbol"), { target: { value: "033780" } });
    fireEvent.change(getByTestId("mh-qty"), { target: { value: "3" } });
    fireEvent.click(getByTestId("mh-buy"));
    await waitFor(() =>
      expect(manualOrder).toHaveBeenCalledWith({ symbol: "033780", side: "BUY", quantity: 3 })
    );
    await waitFor(() => expect(getByTestId("mh-note").textContent).toContain("주문을 보냈어요"));
  });

  it("종목 미입력 → 실패 안내(주문 안 함)", async () => {
    const manualOrder = vi.fn();
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmYes} />
    );
    fireEvent.click(getByTestId("mh-buy"));
    await waitFor(() => expect(getByTestId("mh-note").textContent).toContain("종목 코드"));
    expect(manualOrder).not.toHaveBeenCalled();
  });

  it("confirmFn=false이면 주문 안 함", async () => {
    const manualOrder = vi.fn();
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmNo} />
    );
    fireEvent.change(getByTestId("mh-symbol"), { target: { value: "033780" } });
    fireEvent.click(getByTestId("mh-buy"));
    expect(manualOrder).not.toHaveBeenCalled();
  });

  // ── 현재가 조회 ───────────────────────────────────────────

  it("조회 버튼 → manualOrderQuote 호출 + 가격 표시", async () => {
    const manualOrderQuote = vi.fn(async () => ({ symbol: "033780", name: "KT&G", price: 84000, fetched_at_kst: "15:00" }));
    const { getByTestId, getByText } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrderQuote }} confirmFn={_confirmYes} />
    );
    fireEvent.change(getByTestId("mh-symbol"), { target: { value: "033780" } });
    fireEvent.click(getByTestId("mh-quote"));
    await waitFor(() => expect(manualOrderQuote).toHaveBeenCalledWith("033780"));
    await waitFor(() => expect(getByTestId("mh-quote-display")).toBeTruthy());
  });

  // ── 직접 보유 행 매도 ─────────────────────────────────────

  it("직접 보유 행에 매도 버튼 표시", () => {
    const { getByTestId } = render(<ManualHoldingsCard live={live()} api={{}} />);
    expect(getByTestId("mh-sell-033780")).toBeTruthy();
  });

  it("직접 보유 [매도] → api.manualOrder SELL 호출", async () => {
    const manualOrder = vi.fn(async () => ({ message: "KT&G 5주 매도 주문을 보냈어요." }));
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmYes} />
    );
    fireEvent.click(getByTestId("mh-sell-033780"));
    await waitFor(() =>
      expect(manualOrder).toHaveBeenCalledWith(
        expect.objectContaining({ symbol: "033780", side: "SELL" })
      )
    );
  });

  it("매도 confirmFn=false이면 주문 안 함", async () => {
    const manualOrder = vi.fn();
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmNo} />
    );
    fireEvent.click(getByTestId("mh-sell-033780"));
    expect(manualOrder).not.toHaveBeenCalled();
  });

  it("매도 성공 → 행 메모 표시", async () => {
    const manualOrder = vi.fn(async () => ({ message: "KT&G 5주 매도 주문을 보냈어요." }));
    const { getByTestId } = render(
      <ManualHoldingsCard live={live()} api={{ manualOrder }} confirmFn={_confirmYes} />
    );
    fireEvent.click(getByTestId("mh-sell-033780"));
    await waitFor(() =>
      expect(getByTestId("mh-sell-note-033780").textContent).toContain("주문을 보냈어요")
    );
  });
});
