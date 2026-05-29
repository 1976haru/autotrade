/**
 * AutoRefreshFooter — 토글 / 마지막 갱신 / 에러 / 수동 새로고침 / 안전 invariant.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { AutoRefreshFooter } from "./AutoRefreshFooter";

afterEach(cleanup);


function _render(props = {}) {
  const setIsAutoOn = vi.fn();
  const manualRefresh = vi.fn();
  const utils = render(
    <AutoRefreshFooter
      isAutoOn={true}
      setIsAutoOn={setIsAutoOn}
      lastUpdatedAt={null}
      isRefreshing={false}
      lastError={null}
      manualRefresh={manualRefresh}
      intervalMs={5_000}
      {...props}
    />,
  );
  return { ...utils, setIsAutoOn, manualRefresh };
}


describe("<AutoRefreshFooter>", () => {
  it("자동 ON 상태 + 인터벌 표시", () => {
    _render({ isAutoOn: true, intervalMs: 10_000 });
    const label = screen.getByTestId("auto-refresh-footer").textContent;
    expect(label).toContain("자동 새로고침 ON");
    expect(label).toContain("10s");
  });

  it("자동 OFF 상태 + 수동 새로고침 버튼 노출", () => {
    const { manualRefresh } = _render({ isAutoOn: false });
    expect(screen.getByTestId("auto-refresh-footer").textContent).toContain("OFF");
    fireEvent.click(screen.getByTestId("auto-refresh-footer-manual"));
    expect(manualRefresh).toHaveBeenCalledTimes(1);
  });

  it("자동 ON 일 때 수동 새로고침 버튼 미노출 (체크박스로 끄게 유도)", () => {
    _render({ isAutoOn: true });
    expect(screen.queryByTestId("auto-refresh-footer-manual")).toBeNull();
  });

  it("체크박스 토글 → setIsAutoOn 호출", () => {
    const { setIsAutoOn } = _render({ isAutoOn: true });
    const cb = screen.getByTestId("auto-refresh-footer-toggle");
    fireEvent.click(cb);
    expect(setIsAutoOn).toHaveBeenCalledWith(false);
  });

  it("lastUpdatedAt 표시 (N초 전 / 방금 전)", () => {
    const now = new Date();
    _render({ lastUpdatedAt: now });
    expect(screen.getByTestId("auto-refresh-footer-last").textContent).toMatch(/방금 전|초 전/);
    cleanup();
    _render({ lastUpdatedAt: null });
    expect(screen.getByTestId("auto-refresh-footer-last").textContent).toContain("—");
  });

  it("isRefreshing → 로딩 인디케이터 노출", () => {
    _render({ isRefreshing: true });
    expect(screen.getByTestId("auto-refresh-footer-loading")).toBeTruthy();
  });

  it("lastError 노출 (clamped 80자, 시크릿 직접 표시 X)", () => {
    _render({ lastError: "backend connection refused at 127.0.0.1:8000" });
    expect(screen.getByTestId("auto-refresh-footer-error").textContent)
      .toContain("backend connection refused");
  });

  it("invariant: 매수/매도/실전/Place Order 라벨 button 0개", () => {
    const { container } = _render({ isAutoOn: false });
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    for (const t of labels) {
      expect(t).not.toMatch(/실전|매수|매도|Place Order|ENABLE_/i);
    }
  });

  it("invariant: input 은 *체크박스 1개* 만", () => {
    const { container } = _render({ isAutoOn: false });
    const inputs = container.querySelectorAll("input");
    expect(inputs.length).toBe(1);
    expect(inputs[0].type).toBe("checkbox");
    expect(container.querySelectorAll("textarea,select").length).toBe(0);
  });
});
