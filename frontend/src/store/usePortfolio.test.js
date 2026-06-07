import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";

vi.mock("../services/backend/client", () => ({
  backendApi: {
    brokerBalance: vi.fn(),
    brokerPositions: vi.fn(),
    brokerPrice: vi.fn(async () => ({ symbol: "x", price: 0 })),
  },
}));

import { backendApi } from "../services/backend/client";
import { usePortfolio } from "./usePortfolio";

describe("usePortfolio B4 — 최초 실패 시 첫 성공까지 복구 재시도", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    backendApi.brokerBalance.mockReset();
    backendApi.brokerPositions.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("첫 조회 실패(백엔드 미준비) → 재시도 후 성공 시 잔고 표시", async () => {
    backendApi.brokerBalance
      .mockRejectedValueOnce(new Error("backend not ready"))      // 1차 실패
      .mockResolvedValue({ cash: 1_000_000, equity: 5_000_000 }); // 재시도 성공
    backendApi.brokerPositions
      .mockRejectedValueOnce(new Error("backend not ready"))
      .mockResolvedValue([]);

    const { result } = renderHook(() => usePortfolio());
    await vi.advanceTimersByTimeAsync(10);     // 1차 시도(실패) 정착
    // 5초 후 복구 재시도 → 성공으로 잔고 표시.
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(10);
    expect(result.current.error).toBe("");
    expect(result.current.totalAsset).toBe(5_000_000);
    // 1차 실패 + 재시도 1회 = 2회 호출(재시도가 실제로 일어났다는 증거).
    expect(backendApi.brokerBalance).toHaveBeenCalledTimes(2);
  });

  it("T2: stale 잔고 응답 → 숫자 + stale=true + asOf + brokerHealthy=false", async () => {
    vi.useRealTimers();   // 재시도 없는 단발 — 실시계 + waitFor 로 안정 검증
    backendApi.brokerBalance.mockResolvedValue({
      cash: 50, equity: 100_000_000, stale: true, as_of_kst: "09:27", broker_healthy: false,
    });
    backendApi.brokerPositions.mockResolvedValue([]);
    const { result } = renderHook(() => usePortfolio());
    await waitFor(() => expect(result.current.ready).toBe(true));
    expect(result.current.stale).toBe(true);
    expect(result.current.asOf).toBe("09:27");
    expect(result.current.brokerHealthy).toBe(false);
    expect(result.current.totalAsset).toBe(100_000_000);  // 옛값 숫자(가짜 0 아님)
  });

  it("첫 조회 성공 → 재시도 타이머 없음", async () => {
    backendApi.brokerBalance.mockResolvedValue({ cash: 200, equity: 300 });
    backendApi.brokerPositions.mockResolvedValue([]);
    const { result } = renderHook(() => usePortfolio());
    await vi.advanceTimersByTimeAsync(0);
    expect(result.current.error).toBe("");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(backendApi.brokerBalance).toHaveBeenCalledTimes(1); // 복구 재시도 0
  });
});
