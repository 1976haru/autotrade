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

// 가짜 타이머 하에서 React 상태 커밋(effect → setState → re-render)은 여러 마이크로/
// 매크로태스크 tick 에 걸쳐 일어난다 — 단발 advance 하나로는 부족할 수 있어 잘게
// 여러 번 나눠 흘려보낸다.
async function flush(times = 5, stepMs = 5) {
  for (let i = 0; i < times; i++) {
    await vi.advanceTimersByTimeAsync(stepMs);
  }
}

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


  it("B3: balance 실패 + positions 정상 → positions 독립 반영(전염 0)", async () => {
    vi.useRealTimers();
    backendApi.brokerBalance.mockRejectedValue(new Error("503"));
    backendApi.brokerPositions.mockResolvedValue([{ symbol: "005930", quantity: 3, avg_price: 70000, market_price: 75000 }]);
    const { result } = renderHook(() => usePortfolio());
    await waitFor(() => expect(result.current.positions.length).toBe(1));
    expect(result.current.positions[0].code).toBe("005930");  // balance 죽어도 positions 반영
    expect(result.current.error).toBeTruthy();                 // balance 실패는 정직 표시
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

describe("usePortfolio ratelimit_fix 권고1 — 시세 폴링 stale/부분실패 처리", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    backendApi.brokerBalance.mockReset();
    backendApi.brokerPositions.mockReset();
    backendApi.brokerPrice.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it("종목 시세가 stale:true 로 오면 이전 가격 유지 + position.stale=true + pricesStale=true", async () => {
    backendApi.brokerBalance.mockResolvedValue({ cash: 100, equity: 1_000_000 });
    backendApi.brokerPositions.mockResolvedValue([
      { symbol: "005930", quantity: 1, avg_price: 70_000, market_price: 71_000 },
    ]);
    const { result } = renderHook(() => usePortfolio());
    await flush();   // 최초 mount effect(balance+positions) + codesKey→interval 부착 정착
    expect(result.current.positions.length).toBe(1);
    expect(result.current.pricesStale).toBe(false);

    backendApi.brokerPrice.mockResolvedValue({
      symbol: "005930", price: 71_500, stale: true, as_of_kst: "09:27",
    });
    await vi.advanceTimersByTimeAsync(15_000);   // PRICE_TICK_MS 1회 경과
    await flush();

    expect(result.current.positions[0].cur).toBe(71_500);   // 백엔드가 준 마지막 정상값
    expect(result.current.positions[0].stale).toBe(true);
    expect(result.current.pricesStale).toBe(true);
  });

  it("한 종목 조회가 실패(reject)해도 다른 종목 시세는 정상 반영(배치 전체 무효화 금지)", async () => {
    backendApi.brokerBalance.mockResolvedValue({ cash: 100, equity: 1_000_000 });
    backendApi.brokerPositions.mockResolvedValue([
      { symbol: "005930", quantity: 1, avg_price: 70_000, market_price: 71_000 },
      { symbol: "000660", quantity: 1, avg_price: 200_000, market_price: 210_000 },
    ]);
    const { result } = renderHook(() => usePortfolio());
    await flush();
    expect(result.current.positions.length).toBe(2);

    backendApi.brokerPrice.mockImplementation(async (symbol) => {
      if (symbol === "005930") {
        const err = new Error("Backend API error: 503");
        err.status = 503;
        throw err;   // 캐시조차 없는 드문 실패(처음 보는 종목 등)
      }
      return { symbol, price: 999_000, stale: false, as_of_kst: "10:00" };
    });
    await vi.advanceTimersByTimeAsync(15_000);
    await flush();

    const p930 = result.current.positions.find((x) => x.code === "005930");
    const p660 = result.current.positions.find((x) => x.code === "000660");
    expect(p660.cur).toBe(999_000);     // 005930 실패와 무관하게 정상 반영
    expect(p930.cur).toBe(71_000);      // 실패분은 이전 값 유지(가짜 갱신 없음)
    expect(result.current.equity).toBe(1_000_000); // balance 갱신도 살아있음(allSettled 로 배치 전체가 안 죽음)
  });
});
