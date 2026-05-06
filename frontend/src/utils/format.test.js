import { describe, expect, it } from "vitest";

import {
  PENDING_STALE_THRESHOLD_MS,
  SIGNAL_COLOR,
  confluenceColor,
  fmtKRW,
  fmtPct,
  formatPendingAge,
  isPendingStale,
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


describe("formatPendingAge", () => {
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const ago = (ms) => new Date(NOW - ms).toISOString();

  it("returns '방금' for ages under 30 seconds", () => {
    expect(formatPendingAge(ago(0), NOW)).toBe("방금");
    expect(formatPendingAge(ago(15_000), NOW)).toBe("방금");
  });

  it("returns minutes when between 30s and 1h", () => {
    expect(formatPendingAge(ago(60_000), NOW)).toBe("1분 전");
    expect(formatPendingAge(ago(5 * 60_000), NOW)).toBe("5분 전");
    expect(formatPendingAge(ago(59 * 60_000), NOW)).toBe("59분 전");
  });

  it("returns hours when between 1h and 24h", () => {
    expect(formatPendingAge(ago(60 * 60_000), NOW)).toBe("1시간 전");
    expect(formatPendingAge(ago(5 * 60 * 60_000), NOW)).toBe("5시간 전");
  });

  it("returns days when 24h or more", () => {
    expect(formatPendingAge(ago(24 * 60 * 60_000), NOW)).toBe("1일 전");
    expect(formatPendingAge(ago(3 * 24 * 60 * 60_000), NOW)).toBe("3일 전");
  });

  it("clamps negative deltas (clock skew) to '방금'", () => {
    const future = new Date(NOW + 60_000).toISOString();
    expect(formatPendingAge(future, NOW)).toBe("방금");
  });
});


describe("isPendingStale", () => {
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const ago = (ms) => new Date(NOW - ms).toISOString();

  it("uses the documented 10-minute threshold", () => {
    expect(PENDING_STALE_THRESHOLD_MS).toBe(10 * 60 * 1000);
  });

  it("returns false under 10 minutes", () => {
    expect(isPendingStale(ago(0), NOW)).toBe(false);
    expect(isPendingStale(ago(9 * 60_000), NOW)).toBe(false);
  });

  it("returns true at or over 10 minutes", () => {
    expect(isPendingStale(ago(10 * 60_000), NOW)).toBe(true);
    expect(isPendingStale(ago(60 * 60_000), NOW)).toBe(true);
  });
});
