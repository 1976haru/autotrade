/**
 * useAutoRefresh — 폴링 / 토글 / 가시성 / 언마운트 정리 / 에러 흡수 테스트.
 *
 * fake timers 와 async refresh 동시 사용: `vi.advanceTimersByTimeAsync` (microtask + timer
 * 동기 flush) 를 쓴다. `waitFor` 는 실시간 timer 의존이라 fake timer 와 충돌하므로 사용 X.
 */

import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";

import { useAutoRefresh, formatTimeAgo } from "./useAutoRefresh";

afterEach(cleanup);


describe("useAutoRefresh — 인터벌 폴링", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("intervalMs 마다 refresh 호출 + 최소 1000ms clamp", async () => {
    const refresh = vi.fn(async () => {});
    renderHook(() => useAutoRefresh(refresh, { intervalMs: 100 /* clamp to 1000 */ }));

    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(refresh).toHaveBeenCalledTimes(0);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });   // 1000ms 도달
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("자동 OFF 토글 시 폴링 정지", async () => {
    const refresh = vi.fn(async () => {});
    const { result } = renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 2_000 }));

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(refresh).toHaveBeenCalledTimes(1);

    act(() => result.current.setIsAutoOn(false));
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(1);   // 추가 호출 없음
  });

  it("manualRefresh 는 토글 OFF 여도 호출", async () => {
    const refresh = vi.fn(async () => {});
    const { result } = renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 5_000, enabledByDefault: false }));

    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(0);

    await act(async () => { await result.current.manualRefresh(); });
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.lastUpdatedAt).not.toBeNull();
  });

  it("refresh 실패는 흡수 — 마지막 데이터 유지, lastError 노출", async () => {
    const refresh = vi.fn(async () => { throw new Error("backend down"); });
    const { result } = renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 1_000 }));

    await act(async () => { await result.current.manualRefresh(); });
    expect(result.current.lastError).toBe("backend down");
    expect(result.current.lastUpdatedAt).toBeNull();
  });

  it("성공 후 lastUpdatedAt 갱신 + lastError null 로 복귀", async () => {
    let fail = true;
    const refresh = vi.fn(async () => { if (fail) throw new Error("nope"); });
    const { result } = renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 1_000 }));

    await act(async () => { await result.current.manualRefresh(); });
    expect(result.current.lastError).toBe("nope");

    fail = false;
    await act(async () => { await result.current.manualRefresh(); });
    expect(result.current.lastError).toBeNull();
    expect(result.current.lastUpdatedAt).not.toBeNull();
  });

  it("언마운트 시 setInterval 정리 (memory leak 방지)", async () => {
    const refresh = vi.fn(async () => {});
    const { unmount } = renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 1_000 }));

    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(refresh).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(1);
  });
});


describe("useAutoRefresh — visibility (탭 비활성 시 일시정지)", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function _setHidden(hidden) {
    Object.defineProperty(document, "visibilityState",
      { value: hidden ? "hidden" : "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  }

  it("hidden 상태에서는 폴링 정지, visible 복귀 시 즉시 1회 refresh", async () => {
    _setHidden(false);
    const refresh = vi.fn(async () => {});
    renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 1_000, pauseOnHidden: true }));

    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(refresh).toHaveBeenCalledTimes(1);

    await act(async () => { _setHidden(true); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(refresh).toHaveBeenCalledTimes(1);   // hidden → 폴링 멈춤

    await act(async () => { _setHidden(false); });
    // visible 복귀 트리거 effect가 즉시 1회 refresh 호출 (microtask 한 번 flush).
    await act(async () => { await Promise.resolve(); });
    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it("pauseOnHidden=false 면 hidden 이어도 폴링 유지", async () => {
    const refresh = vi.fn(async () => {});
    renderHook(() =>
      useAutoRefresh(refresh, { intervalMs: 1_000, pauseOnHidden: false }));

    await act(async () => { _setHidden(true); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(refresh.mock.calls.length).toBeGreaterThanOrEqual(2);   // 멈추지 않음
  });
});


describe("formatTimeAgo", () => {
  it("방금 전 / N초 / N분 / 절대시각", () => {
    const t = new Date("2026-05-29T12:00:00Z");
    expect(formatTimeAgo(t, new Date("2026-05-29T12:00:01Z"))).toBe("방금 전");
    expect(formatTimeAgo(t, new Date("2026-05-29T12:00:15Z"))).toBe("15초 전");
    expect(formatTimeAgo(t, new Date("2026-05-29T12:05:00Z"))).toBe("5분 전");
    expect(formatTimeAgo(t, new Date("2026-05-29T13:30:00Z"))).toMatch(/:/);
    expect(formatTimeAgo(null)).toBe("—");
  });
});


// 컴포넌트 통합 테스트는 의도적으로 생략 — renderHook 의 "언마운트 시 setInterval 정리"
// 가 동일 검증을 제공한다. (render(<Probe />) 는 fake-timer 환경에서 React effect 스케줄
// 링이 불안정.)
