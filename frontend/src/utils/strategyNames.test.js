import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  _resetStrategyDisplayLookupForTests,
  fetchStrategyDisplayLookup,
  formatStrategyName,
  strategyDisplayShort,
} from "./strategyNames";

vi.mock("../services/backend/client", () => ({
  backendApi: { engineBeginnerRegistry: vi.fn() },
}));

import { backendApi } from "../services/backend/client";


const _SIX = [
  { strategy_id: "sma_crossover", display_name: "단기/장기 이동평균 교차" },
  { strategy_id: "rsi_reversion", display_name: "RSI 과매도/과매수 회복" },
  { strategy_id: "vwap_strategy", display_name: "VWAP 평균 회귀" },
  { strategy_id: "orb_vwap",      display_name: "ORB + VWAP 돌파" },
  { strategy_id: "volume_breakout",  display_name: "거래량 급증 돌파" },
  { strategy_id: "pullback_rebreak", display_name: "눌림목 재돌파" },
];


beforeEach(() => {
  _resetStrategyDisplayLookupForTests();
  backendApi.engineBeginnerRegistry.mockReset();
});

afterEach(() => {
  _resetStrategyDisplayLookupForTests();
});


describe("formatStrategyName", () => {
  it("displayName + (internal_id) 함께 표시 (default)", () => {
    const lookup = Object.fromEntries(_SIX.map((e) => [e.strategy_id, e]));
    expect(formatStrategyName("sma_crossover", lookup))
      .toBe("단기/장기 이동평균 교차 (sma_crossover)");
  });

  it("compact=true 면 displayName 만 (internal id 없음)", () => {
    const lookup = Object.fromEntries(_SIX.map((e) => [e.strategy_id, e]));
    expect(formatStrategyName("rsi_reversion", lookup, { compact: true }))
      .toBe("RSI 과매도/과매수 회복");
  });

  it("lookup 없으면 internal id 그대로 (graceful degradation)", () => {
    expect(formatStrategyName("sma_crossover", null))
      .toBe("sma_crossover");
    expect(formatStrategyName("sma_crossover", {}))
      .toBe("sma_crossover");
  });

  it("미등록 id 는 internal id 그대로 — 운영자 매핑 보존", () => {
    const lookup = Object.fromEntries(_SIX.map((e) => [e.strategy_id, e]));
    expect(formatStrategyName("unknown_strategy", lookup))
      .toBe("unknown_strategy");
  });

  it("null / undefined / 빈 문자열은 '—' 반환", () => {
    expect(formatStrategyName(null, {})).toBe("—");
    expect(formatStrategyName(undefined, {})).toBe("—");
    expect(formatStrategyName("", {})).toBe("—");
  });
});


describe("strategyDisplayShort", () => {
  it("compact 동일 — displayName 만", () => {
    const lookup = Object.fromEntries(_SIX.map((e) => [e.strategy_id, e]));
    expect(strategyDisplayShort("vwap_strategy", lookup))
      .toBe("VWAP 평균 회귀");
  });

  it("미등록 id 는 internal id", () => {
    expect(strategyDisplayShort("unknown", {})).toBe("unknown");
  });
});


describe("fetchStrategyDisplayLookup (cache + dedup)", () => {
  it("backend 응답을 lookup dict 로 변환", async () => {
    backendApi.engineBeginnerRegistry.mockResolvedValueOnce(_SIX);
    const lookup = await fetchStrategyDisplayLookup();
    expect(lookup["sma_crossover"].display_name)
      .toBe("단기/장기 이동평균 교차");
    expect(Object.keys(lookup).length).toBe(6);
  });

  it("두 번째 호출은 캐시 사용 (backend 1회만 호출)", async () => {
    backendApi.engineBeginnerRegistry.mockResolvedValue(_SIX);
    const a = await fetchStrategyDisplayLookup();
    const b = await fetchStrategyDisplayLookup();
    expect(a).toBe(b);
    expect(backendApi.engineBeginnerRegistry).toHaveBeenCalledTimes(1);
  });

  it("동시 호출 dedup — in-flight Promise 공유", async () => {
    let resolveFn;
    backendApi.engineBeginnerRegistry.mockReturnValueOnce(
      new Promise((resolve) => { resolveFn = resolve; }),
    );
    const p1 = fetchStrategyDisplayLookup();
    const p2 = fetchStrategyDisplayLookup();
    resolveFn(_SIX);
    const [a, b] = await Promise.all([p1, p2]);
    expect(a).toBe(b);
    expect(backendApi.engineBeginnerRegistry).toHaveBeenCalledTimes(1);
  });

  it("backend 실패 시 in-flight 해제 — 다음 호출 재시도 가능", async () => {
    backendApi.engineBeginnerRegistry.mockRejectedValueOnce(new Error("net"));
    await expect(fetchStrategyDisplayLookup()).rejects.toThrow("net");
    backendApi.engineBeginnerRegistry.mockResolvedValueOnce(_SIX);
    const second = await fetchStrategyDisplayLookup();
    expect(second["orb_vwap"].display_name).toBe("ORB + VWAP 돌파");
  });
});


describe("invariant", () => {
  it("internal id 는 항상 보존 — 6개 모두 default 포맷에 internal id 포함", () => {
    const lookup = Object.fromEntries(_SIX.map((e) => [e.strategy_id, e]));
    for (const e of _SIX) {
      const out = formatStrategyName(e.strategy_id, lookup);
      expect(out).toContain(e.strategy_id);
      expect(out).toContain(e.display_name);
    }
  });
});
