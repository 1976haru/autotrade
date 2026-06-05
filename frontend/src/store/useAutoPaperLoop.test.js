import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useAutoPaperLoop } from "./useAutoPaperLoop";

function makeApi(overrides = {}) {
  return {
    autoPaperStatus: vi.fn(async () => ({ state: "PAUSED" })),
    autoPaperStart: vi.fn(async () => ({ ok: true })),
    autoPaperStop: vi.fn(async () => ({ ok: true })),
    ...overrides,
  };
}

describe("useAutoPaperLoop — 실제 루프 배선(기존 client 함수 재사용)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("start(): 기존 autoPaperStart 를 risk_profile + capital_settings payload로 호출", async () => {
    const api = makeApi();
    const { result } = renderHook(() =>
      useAutoPaperLoop({ api, riskProfile: "AGGRESSIVE", capitalSettings: { initialCash: 10000000, riskProfile: "AGGRESSIVE" }, pollMs: 0 }),
    );
    let res;
    await act(async () => { res = await result.current.start(); });
    expect(api.autoPaperStart).toHaveBeenCalledTimes(1);
    const body = api.autoPaperStart.mock.calls[0][0];
    expect(body.risk_profile).toBe("AGGRESSIVE");
    expect(body).toHaveProperty("capital_settings");
    expect(res.ok).toBe(true);
  });

  it("stop(): 기존 autoPaperStop 호출", async () => {
    const api = makeApi();
    const { result } = renderHook(() => useAutoPaperLoop({ api, pollMs: 0 }));
    await act(async () => { await result.current.stop(); });
    expect(api.autoPaperStop).toHaveBeenCalledTimes(1);
  });

  it("running 은 status state===RUNNING 기준", async () => {
    const api = makeApi({ autoPaperStatus: vi.fn(async () => ({ state: "RUNNING" })) });
    const { result } = renderHook(() => useAutoPaperLoop({ api, pollMs: 0 }));
    await waitFor(() => expect(result.current.running).toBe(true));
    expect(result.current.stopAllowed).toBe(true);
  });

  it("start 실패(서버 409 gate BLOCK) 시 reasons 추출", async () => {
    const err = new Error("blocked");
    err.detail = { reasons: ["PRE_MARKET_BLOCK", "MARKET_CLOSED"] };
    const api = makeApi({ autoPaperStart: vi.fn(async () => { throw err; }) });
    const { result } = renderHook(() => useAutoPaperLoop({ api, pollMs: 0 }));
    let res;
    await act(async () => { res = await result.current.start(); });
    expect(res.ok).toBe(false);
    expect(res.reasons).toContain("PRE_MARKET_BLOCK");
  });

  it("status 조회 실패는 크래시하지 않고 마지막 상태 유지", async () => {
    const api = makeApi({ autoPaperStatus: vi.fn(async () => { throw new Error("offline"); }) });
    const { result } = renderHook(() => useAutoPaperLoop({ api, pollMs: 0 }));
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(result.current.state).toBe("PAUSED"); // 기본 유지
  });
});
