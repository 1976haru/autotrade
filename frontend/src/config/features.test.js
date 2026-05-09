import { afterEach, describe, expect, it } from "vitest";

import {
  FEATURES,
  __resetFeaturesForTest,
  __setFeatureForTest,
  getActiveFeatureSnapshot,
} from "./features";

afterEach(() => { __resetFeaturesForTest(); });


describe("FEATURES", () => {
  it("defaults to false for futuresTab when env var not set", () => {
    // vitest는 VITE_ENABLE_FUTURES_TAB을 setup하지 않은 상태가 default.
    // 운영자가 명시 옵트인 안 한 상태에서 본 flag가 자동으로 켜지면 안 됨.
    __resetFeaturesForTest();
    expect(FEATURES.futuresTab).toBe(false);
  });

  it("returns false for unknown flags (defensive)", () => {
    expect(FEATURES.bogusFlag).toBe(false);
  });

  it("__setFeatureForTest can flip a known flag in tests", () => {
    __setFeatureForTest("futuresTab", true);
    expect(FEATURES.futuresTab).toBe(true);
    __resetFeaturesForTest();
    expect(FEATURES.futuresTab).toBe(false);
  });

  it("__setFeatureForTest rejects unknown flags", () => {
    expect(() => __setFeatureForTest("bogus", true)).toThrow(
      /unknown feature flag/,
    );
  });

  it("getActiveFeatureSnapshot returns all known flags", () => {
    const snap = getActiveFeatureSnapshot();
    expect(snap).toHaveProperty("futuresTab");
    expect(snap.futuresTab).toBe(false);
  });

  it("__setFeatureForTest accepts truthy/falsy normalization", () => {
    __setFeatureForTest("futuresTab", 1);
    expect(FEATURES.futuresTab).toBe(true);
    __setFeatureForTest("futuresTab", 0);
    expect(FEATURES.futuresTab).toBe(false);
  });
});
