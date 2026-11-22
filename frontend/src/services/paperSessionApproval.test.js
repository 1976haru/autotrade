import { describe, expect, it } from "vitest";

import { kstDate } from "./paperSessionApproval";

describe("paper session approval date", () => {
  it("uses the actual KST calendar date instead of a fixed legacy date", () => {
    expect(kstDate(new Date("2026-11-16T15:01:00Z"))).toBe("2026-11-17");
  });
});
