import { describe, expect, it } from "vitest";

import {
  SIGNAL_COLOR,
  confluenceColor,
  fmtKRW,
  fmtPct,
  pnlColor,
} from "./format";


describe("fmtKRW", () => {
  it("groups thousands with the Korean locale separator", () => {
    expect(fmtKRW(123456)).toBe("123,456");
    expect(fmtKRW(1_234_567)).toBe("1,234,567");
  });

  it("formats zero and negatives", () => {
    expect(fmtKRW(0)).toBe("0");
    expect(fmtKRW(-50000)).toBe("-50,000");
  });

  it("treats null/undefined as zero", () => {
    expect(fmtKRW(null)).toBe("0");
    expect(fmtKRW(undefined)).toBe("0");
  });
});


describe("fmtPct", () => {
  it("prefixes positive values with +", () => {
    expect(fmtPct(2.345)).toBe("+2.3%");
    expect(fmtPct(0)).toBe("+0.0%");
  });

  it("keeps the minus sign for negatives", () => {
    expect(fmtPct(-1.5)).toBe("-1.5%");
  });

  it("respects the decimals argument", () => {
    expect(fmtPct(2.345, 2)).toBe("+2.35%");
    expect(fmtPct(2.345, 0)).toBe("+2%");
  });
});


describe("pnlColor", () => {
  it("returns green for positive, red for negative, gray for zero", () => {
    expect(pnlColor(100)).toBe("#22c55e");
    expect(pnlColor(-100)).toBe("#ef4444");
    expect(pnlColor(0)).toBe("#64748b");
  });
});


describe("confluenceColor", () => {
  it("buckets scores into green/yellow/red", () => {
    expect(confluenceColor(70)).toBe("#22c55e");
    expect(confluenceColor(85)).toBe("#22c55e");
    expect(confluenceColor(50)).toBe("#facc15");
    expect(confluenceColor(69)).toBe("#facc15");
    expect(confluenceColor(49)).toBe("#ef4444");
    expect(confluenceColor(0)).toBe("#ef4444");
  });
});


describe("SIGNAL_COLOR", () => {
  it("covers the five Korean signal labels", () => {
    expect(SIGNAL_COLOR["강력매수"]).toBeDefined();
    expect(SIGNAL_COLOR["매수"]).toBeDefined();
    expect(SIGNAL_COLOR["관망"]).toBeDefined();
    expect(SIGNAL_COLOR["매도"]).toBeDefined();
    expect(SIGNAL_COLOR["강력매도"]).toBeDefined();
  });
});
