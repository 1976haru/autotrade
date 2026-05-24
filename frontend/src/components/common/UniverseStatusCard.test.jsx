/**
 * #54 / 7-02 — UniverseStatusCard 단위 테스트.
 *
 * lock 하는 invariant:
 *  - 기본 Universe 50개 사용 문구 + universe_source/count/preview/fallback 표시
 *  - 사용자 관심종목 → USER_WATCHLIST 표시
 *  - 후보군 0개 reason 표시
 *  - loading / error 상태
 *  - secret/account 표시 없음
 *  - 매수/매도/실전 버튼 0개, 입력 form 0개
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { UniverseStatusCard } from "./UniverseStatusCard";


afterEach(cleanup);


function _api(payload) {
  return { universeStatus: vi.fn(async () => payload) };
}

const _DEFAULT_50 = {
  universe_source: "DEFAULT_UNIVERSE_50",
  universe_count: 50,
  symbols_preview: ["005930", "000660", "035420", "051910", "005380"],
  fallback_used: true,
  reason_code: "NO_USER_WATCHLIST",
  message_ko: "사용자 관심종목 없음 → 기본 Universe 50개 사용 중입니다.",
  invalid_symbols_removed: [],
  is_live_authorization: false,
  contains_secret: false,
};


describe("<UniverseStatusCard>", () => {
  it("기본 Universe 50개 사용 문구 + source 표시", async () => {
    render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    expect((await screen.findByTestId("universe-source")).textContent)
      .toContain("DEFAULT_UNIVERSE_50");
    expect(screen.getByTestId("universe-fallback").textContent)
      .toContain("기본 Universe 50개 사용");
  });

  it("universe_count=50 표시", async () => {
    render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    expect((await screen.findByTestId("universe-count")).textContent).toContain("50개");
  });

  it("symbols_preview 표시", async () => {
    render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    const t = (await screen.findByTestId("universe-preview")).textContent;
    expect(t).toContain("005930");
    expect(t).toContain("외 45개");  // 50 - 5 preview
  });

  it("사용자 관심종목 → USER_WATCHLIST, fallback 없음", async () => {
    render(<UniverseStatusCard apiClient={_api({
      ..._DEFAULT_50, universe_source: "USER_WATCHLIST", universe_count: 2,
      symbols_preview: ["005930", "000660"], fallback_used: false,
      reason_code: "USER_WATCHLIST_OK", message_ko: "사용자 관심종목을 사용 중입니다.",
    })} />);
    expect((await screen.findByTestId("universe-source")).getAttribute("data-source"))
      .toBe("USER_WATCHLIST");
    expect(screen.queryByTestId("universe-fallback")).toBeNull();
  });

  it("후보군 0개 reason 표시", async () => {
    render(<UniverseStatusCard apiClient={_api({
      ..._DEFAULT_50, universe_source: "EMPTY", universe_count: 0,
      symbols_preview: [], fallback_used: false, reason_code: "NO_UNIVERSE_SYMBOLS",
      message_ko: "후보군이 없어 자동 판단을 건너뜁니다.",
    })} />);
    expect((await screen.findByTestId("universe-empty-reason")).textContent)
      .toContain("NO_UNIVERSE_SYMBOLS");
  });

  it("invalid 제외 종목 표시", async () => {
    render(<UniverseStatusCard apiClient={_api({
      ..._DEFAULT_50, universe_source: "USER_WATCHLIST", universe_count: 1,
      symbols_preview: ["005930"], fallback_used: false,
      invalid_symbols_removed: ["BADCODE"],
    })} />);
    expect((await screen.findByTestId("universe-invalid-removed")).textContent)
      .toContain("BADCODE");
  });

  it("loading 상태", async () => {
    const api = { universeStatus: vi.fn(() => new Promise(() => {})) };
    render(<UniverseStatusCard apiClient={api} />);
    expect(await screen.findByTestId("universe-loading")).toBeTruthy();
  });

  it("error 상태", async () => {
    const api = { universeStatus: vi.fn(async () => { throw new Error("offline"); }) };
    render(<UniverseStatusCard apiClient={api} />);
    expect(await screen.findByTestId("universe-error")).toBeTruthy();
  });

  it("투자 추천 아님 안내 문구", async () => {
    render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    expect((await screen.findByTestId("universe-note")).textContent)
      .toContain("투자 추천이 아닙니다");
  });
});


describe("<UniverseStatusCard> — safety invariants", () => {
  it("매수/매도/실전/Place Order 버튼 0개", async () => {
    const { container } = render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    await screen.findByTestId("universe-source");
    expect(container.querySelectorAll("button").length).toBe(0);
    const text = container.textContent || "";
    for (const banned of ["지금 매수", "지금 매도", "Place Order", "매수 실행",
                          "매도 실행", "실거래 시작", "강제 주문"]) {
      expect(text).not.toContain(banned);
    }
  });

  it("입력 form(input/textarea/select) 0개", async () => {
    const { container } = render(<UniverseStatusCard apiClient={_api(_DEFAULT_50)} />);
    await screen.findByTestId("universe-source");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("secret/account 원문이 응답에 있어도 표시되지 않음", async () => {
    const { container } = render(<UniverseStatusCard apiClient={_api({
      ..._DEFAULT_50, kis_app_secret: "LEAKSECRET999", kis_account_no: "98765432-11",
    })} />);
    await screen.findByTestId("universe-source");
    const text = container.textContent || "";
    expect(text).not.toContain("LEAKSECRET999");
    expect(text).not.toContain("98765432");
  });
});
