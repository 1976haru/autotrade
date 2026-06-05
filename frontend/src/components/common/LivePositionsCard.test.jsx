import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { LivePositionsCard } from "./LivePositionsCard";

afterEach(() => cleanup());

const pos = (over = {}) => ({
  symbol: "005930", name: "삼성전자", quantity: 3, avg_price: 70000, market_price: 75000,
  eval_pnl_krw: 15000, return_pct: 7.14, sell_in_progress: false, status: "sellable", ...over,
});
const data = (positions, over = {}) => ({ available: true, positions, fetched_at_kst: "09:05", ...over });

describe("LivePositionsCard (M3/M4)", () => {
  it("보유 행: 종목명/수량/손익 + [매도] 버튼", () => {
    const { getByTestId } = render(<LivePositionsCard data={data([pos()])} api={{}} />);
    expect(getByTestId("livepos-row-005930").textContent).toContain("삼성전자");
    expect(getByTestId("livepos-pnl-005930").textContent).toContain("+15,000원");
    expect(getByTestId("livepos-sell-005930")).toBeTruthy();
  });

  it("보유 0 / 조회 실패 는 다르게 표시", () => {
    const empty = render(<LivePositionsCard data={data([])} api={{}} />);
    expect(empty.getByTestId("livepos-empty")).toBeTruthy();
    cleanup();
    const fail = render(<LivePositionsCard data={{ available: false, fetched_at_kst: "09:07" }} api={{}} />);
    expect(fail.getByTestId("livepos-fail").textContent).toContain("불러오기 실패(09:07)");
  });

  it("다이얼로그 취소 시 매도 미발사", () => {
    const sell = vi.fn(async () => ({}));
    const { getByTestId } = render(<LivePositionsCard data={data([pos()])} api={{ positionSellAll: sell }} confirmFn={() => false} />);
    fireEvent.click(getByTestId("livepos-sell-005930"));
    expect(sell).not.toHaveBeenCalled();
  });

  it("확정 → POST 호출, 연타 중복 방지(버튼 비활성), 성공 시 '주문 보냄'", async () => {
    let resolve;
    const sell = vi.fn(() => new Promise((r) => { resolve = r; }));
    const { getByTestId } = render(<LivePositionsCard data={data([pos()])} api={{ positionSellAll: sell }} confirmFn={() => true} />);
    fireEvent.click(getByTestId("livepos-sell-005930"));
    // 진행 중 → 버튼 비활성(연타 방지)
    await waitFor(() => expect(getByTestId("livepos-sell-005930").disabled).toBe(true));
    fireEvent.click(getByTestId("livepos-sell-005930")); // 연타
    expect(sell).toHaveBeenCalledTimes(1);
    resolve({ status: "SUBMITTED" });
    await waitFor(() => expect(getByTestId("livepos-inprogress-005930").textContent).toContain("주문 보냄"));
  });

  it("실패/거절 → 사유 행 안에 정직 표시 + 버튼 복구", async () => {
    const sell = vi.fn(async () => { throw { detail: "긴급정지 중이라 주문이 차단됐어요" }; });
    const { getByTestId } = render(<LivePositionsCard data={data([pos()])} api={{ positionSellAll: sell }} confirmFn={() => true} />);
    fireEvent.click(getByTestId("livepos-sell-005930"));
    await waitFor(() => expect(getByTestId("livepos-error-005930").textContent).toContain("긴급정지 중이라"));
    expect(getByTestId("livepos-sell-005930").disabled).toBe(false); // 재시도 가능
  });

  it("sell_in_progress 종목: 버튼 대신 '매도 주문 진행 중'", () => {
    const { getByTestId, queryByTestId } = render(
      <LivePositionsCard data={data([pos({ sell_in_progress: true, status: "sell_in_progress" })])} api={{}} />);
    expect(getByTestId("livepos-inprogress-005930").textContent).toContain("매도 주문 진행 중");
    expect(queryByTestId("livepos-sell-005930")).toBeNull();
  });
});
