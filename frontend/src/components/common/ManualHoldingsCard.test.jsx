import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { ManualHoldingsCard } from "./ManualHoldingsCard";

afterEach(() => cleanup());

const live = (over = {}) => ({
  available: true,
  bot_isolation_active: false,
  manual_isolation_notice: "봇 격리 검증 후 사용하세요.",
  positions: [
    { symbol: "005930", name: "삼성전자", quantity: 10, avg_price: 70000, market_price: 77000, source: "BOT", bot_qty: 10, manual_qty: 0 },
    { symbol: "033780", name: "KT&G", quantity: 5, avg_price: 80000, market_price: 84000, source: "MANUAL", bot_qty: 0, manual_qty: 5 },
  ],
  ...over,
});

describe("ManualHoldingsCard (설계 B 조각 1)", () => {
  it("★봇 격리 미작동 경고를 표시(bot_isolation_active=false)", () => {
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

  it("직접 매수 → api.manualBuy 호출 + 결과 표시", async () => {
    const manualBuy = vi.fn(async () => ({ message: "KT&G 3주 직접 매수 주문을 보냈어요." }));
    const { getByTestId } = render(<ManualHoldingsCard live={live()} api={{ manualBuy }} />);
    fireEvent.change(getByTestId("mh-symbol"), { target: { value: "033780" } });
    fireEvent.change(getByTestId("mh-qty"), { target: { value: "3" } });
    fireEvent.click(getByTestId("mh-buy"));
    await waitFor(() => expect(manualBuy).toHaveBeenCalledWith("033780", 3));
    await waitFor(() => expect(getByTestId("mh-note").textContent).toContain("주문을 보냈어요"));
  });

  it("종목 미입력 → 실패 안내(주문 안 함)", async () => {
    const manualBuy = vi.fn();
    const { getByTestId } = render(<ManualHoldingsCard live={live()} api={{ manualBuy }} />);
    fireEvent.click(getByTestId("mh-buy"));
    await waitFor(() => expect(getByTestId("mh-note").textContent).toContain("종목 코드"));
    expect(manualBuy).not.toHaveBeenCalled();
  });
});
