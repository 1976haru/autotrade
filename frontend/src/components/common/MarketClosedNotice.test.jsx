/**
 * Tests for `MarketClosedNotice` (fix/market-closed-state-distinction).
 *
 * 본 컴포넌트는 phase 별 친절한 안내를 노출하며 어떤 액션 버튼도 포함하지
 * 않는다. 절대 원칙:
 *  - "지금 매수" / "Place Order" / "BUY/SELL/HOLD" / "활성화" 버튼 0개
 *  - "조회 실패" / "Failed to fetch" 같은 오류 문구 노출 0건
 */

import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MarketClosedNotice } from "./MarketClosedNotice";
import { MarketPhase } from "../../utils/marketHours";


afterEach(cleanup);


describe("<MarketClosedNotice>", () => {
  it("CLOSED phase 면 '장 종료로 신규 판단 없음' 헤드라인을 노출", () => {
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.CLOSED} />,
    );
    const headline = getByTestId("market-closed-notice-headline");
    expect(headline.textContent).toContain("장 종료");
    expect(headline.textContent).toContain("신규 판단 없음");
  });

  it("PRE_OPEN phase 면 '장 시작 전' 안내", () => {
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.PRE_OPEN} />,
    );
    expect(getByTestId("market-closed-notice-headline").textContent)
      .toContain("장 시작 전");
  });

  it("WEEKEND phase 면 '주말 휴장' 안내", () => {
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.WEEKEND} />,
    );
    expect(getByTestId("market-closed-notice-headline").textContent)
      .toContain("주말 휴장");
  });

  it("OPEN phase 면 아무것도 렌더하지 않는다 (null)", () => {
    const { container } = render(
      <MarketClosedNotice phase={MarketPhase.OPEN} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("phase 미지정이면 null", () => {
    const { container } = render(<MarketClosedNotice />);
    expect(container.firstChild).toBeNull();
  });

  it("detail prop 을 detail 영역에 노출", () => {
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.CLOSED} detail="추가 안내문" />,
    );
    expect(getByTestId("market-closed-notice-detail").textContent)
      .toContain("추가 안내문");
  });

  it("onRefresh 가 주어지면 '다시 확인' 버튼 노출 + 클릭 시 호출", () => {
    const onRefresh = vi.fn();
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.CLOSED} onRefresh={onRefresh} />,
    );
    const btn = getByTestId("market-closed-notice-refresh");
    expect(btn.textContent).toContain("다시 확인");
    fireEvent.click(btn);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("onRefresh 가 없으면 버튼 0개 (read-only banner)", () => {
    const { container } = render(
      <MarketClosedNotice phase={MarketPhase.CLOSED} />,
    );
    expect(container.querySelectorAll("button").length).toBe(0);
  });

  it("어떤 phase 에서도 '조회 실패' / 'Failed to fetch' 문구 노출 0건", () => {
    for (const phase of [
      MarketPhase.PRE_OPEN, MarketPhase.CLOSED, MarketPhase.WEEKEND,
    ]) {
      const { container } = render(<MarketClosedNotice phase={phase} />);
      const text = container.textContent || "";
      expect(text).not.toContain("조회 실패");
      expect(text).not.toContain("Failed to fetch");
      cleanup();
    }
  });

  it("invariant — 매수 / 매도 / Place Order / 활성화 라벨 버튼 0개", () => {
    const { container } = render(
      <MarketClosedNotice phase={MarketPhase.CLOSED} onRefresh={() => {}} />,
    );
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "BUY", "SELL", "HOLD",
      "Place Order", "실거래 활성화", "활성화 토글",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // refresh 버튼 하나만 존재해야 함.
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(1);
    expect(buttons[0].textContent).toContain("다시 확인");
  });

  it("data-market-phase 속성으로 phase carry — 디버그 / 테스트 용이성", () => {
    const { getByTestId } = render(
      <MarketClosedNotice phase={MarketPhase.WEEKEND} />,
    );
    expect(getByTestId("market-closed-notice").getAttribute("data-market-phase"))
      .toBe("WEEKEND");
  });
});
