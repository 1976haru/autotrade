import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../../services/backend/client";
import { WatchlistsCard } from "./WatchlistsCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    listWatchlists:      vi.fn(),
    createWatchlist:     vi.fn(),
    patchWatchlist:      vi.fn(),
    deleteWatchlist:     vi.fn(),
    addWatchlistItem:    vi.fn(),
    removeWatchlistItem: vi.fn(),
    importWatchlistCsv:  vi.fn(),
  },
}));


function _resetMocks() {
  Object.values(backendApi).forEach((fn) => fn?.mockReset?.());
  backendApi.listWatchlists.mockResolvedValue({
    watchlists: [], max_items: 200, recommended_items: 50,
  });
}


describe("WatchlistsCard", () => {
  beforeEach(_resetMocks);
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("shows the universe-not-signal banner with limits", async () => {
    render(<WatchlistsCard />);
    await waitFor(() => screen.getByTestId("watchlists-empty"));
    // banner 텍스트 — 마운트 시점부터 즉시 표시. "최대 200개"는 <b> 태그로
    // 분할되어 있어 정규식 한 줄로는 안 잡혀, 카드 전체 textContent를 본다.
    const card = screen.getByTestId("watchlists-card");
    expect(card.textContent).toMatch(/관심종목은 주문 신호가 아닙니다/);
    expect(card.textContent).toMatch(/최대\s*200개/);
    expect(card.textContent).toMatch(/권장 50개/);
  });

  it("shows the empty state when no watchlists exist", async () => {
    render(<WatchlistsCard />);
    await waitFor(() => screen.getByTestId("watchlists-empty"));
  });

  it("renders existing watchlists with item counts and active badge", async () => {
    backendApi.listWatchlists.mockResolvedValue({
      watchlists: [
        { id: 1, name: "core", is_active: true, item_count: 3,
          items: [
            { id: 11, symbol: "005930", name: "삼성전자" },
            { id: 12, symbol: "000660", name: "SK하이닉스" },
          ] },
      ],
      max_items: 200, recommended_items: 50,
    });

    render(<WatchlistsCard />);
    await waitFor(() => screen.getByText("core"));

    expect(screen.getByText("3 / 200")).toBeTruthy();
    expect(screen.getByText("활성")).toBeTruthy();
    expect(screen.getByTestId("watchlist-item-11")).toBeTruthy();
    expect(screen.getByTestId("watchlist-item-12")).toBeTruthy();
  });

  it("creating with backend rejection surfaces a friendly error", async () => {
    backendApi.createWatchlist.mockRejectedValue(
      new Error("관심종목 목록 이름을 입력해 주세요."),
    );

    render(<WatchlistsCard />);
    // mount fetch 완료 대기 — loading=true 동안엔 생성 버튼이 disabled.
    await waitFor(() => screen.getByTestId("watchlists-empty"));

    fireEvent.change(screen.getByPlaceholderText(/새 목록 이름/),
                     { target: { value: "x" } });
    fireEvent.click(screen.getByText("생성"));

    await waitFor(() => {
      const msg = screen.getByTestId("watchlist-create-msg");
      expect(msg.textContent).toContain("이름을 입력");
    });
  });

  it("CSV import shows a summary message after success", async () => {
    backendApi.listWatchlists.mockResolvedValue({
      watchlists: [{ id: 1, name: "x", is_active: false, item_count: 0, items: [] }],
      max_items: 200, recommended_items: 50,
    });
    backendApi.importWatchlistCsv.mockResolvedValue({
      added: 2, skipped: 1, invalid: 0, total_after_import: 3, errors: [],
    });

    render(<WatchlistsCard />);
    await waitFor(() => screen.getByTestId("watchlist-row-1"));

    // <details>를 펼쳐 textarea 표시.
    fireEvent.click(screen.getByText(/CSV로 일괄 추가/));

    const textarea = await screen.findByPlaceholderText(/symbol,name,market/);
    fireEvent.change(textarea, { target: { value: "symbol\n005930\n000660\n" } });
    fireEvent.click(screen.getByText("가져오기"));

    await waitFor(() => {
      const msg = screen.getByTestId("watchlist-csv-msg-1");
      expect(msg.textContent).toContain("완료");
      expect(msg.textContent).toContain("추가 2");
    });
  });

  it("disables 종목 추가 button when the watchlist is at the 200-cap", async () => {
    backendApi.listWatchlists.mockResolvedValue({
      watchlists: [{ id: 1, name: "x", is_active: false, item_count: 200, items: [] }],
      max_items: 200, recommended_items: 50,
    });

    render(<WatchlistsCard />);
    await waitFor(() => screen.getByTestId("watchlist-row-1"));

    fireEvent.change(screen.getByPlaceholderText(/종목코드/),
                     { target: { value: "005930" } });
    expect(screen.getByText("종목 추가").disabled).toBe(true);
  });
});
