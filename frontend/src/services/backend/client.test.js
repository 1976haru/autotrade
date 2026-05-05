import { describe, expect, it } from "vitest";

import { formatBackendErrorDetail } from "./client";


describe("formatBackendErrorDetail", () => {
  it("returns null for nullish detail", () => {
    expect(formatBackendErrorDetail(null)).toBeNull();
    expect(formatBackendErrorDetail(undefined)).toBeNull();
  });

  it("passes through string detail unchanged (FastAPI default error shape)", () => {
    expect(formatBackendErrorDetail("approval not found")).toBe("approval not found");
  });

  it("formats 070 risk_check_failed_at_approve into a Korean operator message", () => {
    const detail = {
      error: "risk_check_failed_at_approve",
      reasons: ["emergency stop is enabled", "max_order_notional 초과"],
    };
    expect(formatBackendErrorDetail(detail)).toBe(
      "승인 시점 재평가에서 거부됨: emergency stop is enabled / max_order_notional 초과",
    );
  });

  it("joins generic reasons array with slashes", () => {
    const detail = {
      decision: "REJECTED",
      reasons: ["a", "b", "c"],
    };
    expect(formatBackendErrorDetail(detail)).toBe("a / b / c");
  });

  it("falls back to JSON stringify for unfamiliar object shapes", () => {
    const detail = { weird: "shape", n: 42 };
    expect(formatBackendErrorDetail(detail)).toBe('{"weird":"shape","n":42}');
  });

  it("empty reasons array yields an empty string (caller falls back)", () => {
    const detail = { reasons: [] };
    expect(formatBackendErrorDetail(detail)).toBe("");
  });
});
