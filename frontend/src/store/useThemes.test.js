import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useThemes, useThemesSummary } from "./useThemes";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    themeSignals:   vi.fn(),
    themesScan:     vi.fn(),
    themesSummary:  vi.fn(),
  },
}));


describe("useThemes", () => {
  beforeEach(() => {
    Object.values(backendApi).forEach((fn) => fn?.mockReset?.());
    backendApi.themeSignals.mockResolvedValue({
      signals: [], used_for_order: false,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads signals on mount and exposes empty list when nothing", async () => {
    const { result } = renderHook(() => useThemes());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.signals).toEqual([]);
    expect(result.current.error).toBe("");
  });

  it("scan() updates candidates + provider + scanMsg", async () => {
    backendApi.themeSignals
      .mockResolvedValueOnce({ signals: [], used_for_order: false })
      .mockResolvedValueOnce({ signals: [
        { id: 1, theme: "AI 반도체", grade: "STRONG", score: 90,
          provider: "mock", source: "trends", confidence: 80,
          related_symbols: ["005930"], keywords: ["HBM"], used_for_order: false },
      ], used_for_order: false });
    backendApi.themesScan.mockResolvedValue({
      persisted: 5, records: [],
      candidate_symbols: [
        { symbol: "005930", themes: ["AI 반도체"], best_score: 90, best_grade: "STRONG" },
      ],
      provider: "mock", is_provider_enabled: true, used_for_order: false,
    });

    const { result } = renderHook(() => useThemes());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await result.current.scan({ universe: ["005930"], limit: 10 });

    await waitFor(() => expect(result.current.candidates).toHaveLength(1));
    expect(result.current.provider).toBe("mock");
    expect(result.current.providerEnabled).toBe(true);
    expect(result.current.scanMsg).toMatch(/완료/);
  });

  it("captures fetch error", async () => {
    backendApi.themeSignals.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useThemes());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("offline");
  });
});


describe("useThemesSummary", () => {
  beforeEach(() => {
    backendApi.themesSummary.mockReset();
  });

  it("loads summary on mount", async () => {
    backendApi.themesSummary.mockResolvedValue({
      total: 3, by_grade: { STRONG: 1, WATCH: 2 },
      top_themes: [{ theme: "AI", score: 85, grade: "STRONG", provider: "mock", related_symbols: ["005930"] }],
      used_for_order: false,
    });

    const { result } = renderHook(() => useThemesSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.summary.total).toBe(3);
    expect(result.current.summary.used_for_order).toBe(false);
  });

  it("falls back to null on error", async () => {
    backendApi.themesSummary.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useThemesSummary());
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.summary).toBe(null);
  });
});
