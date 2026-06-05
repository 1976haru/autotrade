import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { PerformanceCard } from "./PerformanceCard";

afterEach(() => cleanup());

const ok = (over = {}) => ({
  no_data: false, small_sample: false,
  win_rate: 0.6667, win_count: 6, loss_count: 3, payoff_ratio: 1.8,
  net_pnl_krw: 12_400, period_return_pct: 0.8, period_start_kst: "2026-06-05",
  market: { available: true, kospi_return_pct: 1.2, kosdaq_return_pct: 0.9, fetched_at_kst: "09:05" },
  comparison: { bot_return_pct: 0.8, kospi_return_pct: 1.2, kosdaq_return_pct: 0.9, vs_kospi_pp: -0.4, vs_kosdaq_pp: -0.1 },
  ...over,
});

describe("PerformanceCard (P3/P4)", () => {
  it("정상: 승률 N승 M패 / 손익비 / 순손익 / 봇 vs 지수", async () => {
    const api = { performanceGet: vi.fn(async () => ok()) };
    const { getByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(getByTestId("perf-winrate").textContent).toContain("67% (6승 3패)"));
    expect(getByTestId("perf-payoff").textContent).toBe("1.8");
    expect(getByTestId("perf-net").textContent).toBe("+12,400원");
    const m = getByTestId("perf-market").textContent;
    expect(m).toContain("봇 +0.8%");
    expect(m).toContain("코스피 +1.2%");
    expect(m).toContain("-0.4%p");
    expect(m).toContain("코스닥 +0.9%");
  });

  it("no_data: 지표 '—' + 안내 (가짜 0% 금지)", async () => {
    const api = { performanceGet: vi.fn(async () => ok({ no_data: true, win_rate: null, payoff_ratio: null, net_pnl_krw: 0, comparison: null, market: { available: false } })) };
    const { getByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(getByTestId("perf-nodata")).toBeTruthy());
    expect(getByTestId("perf-winrate").textContent).toBe("—");
    expect(getByTestId("perf-payoff").textContent).toBe("—");
    expect(getByTestId("perf-net").textContent).toBe("—");
  });

  it("small_sample: '표본 적음 · 참고용' 뱃지", async () => {
    const api = { performanceGet: vi.fn(async () => ok({ small_sample: true })) };
    const { getByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(getByTestId("perf-smallsample-badge")).toBeTruthy());
  });

  it("시장 데이터 실패: 2행만 실패 표시, 1행(지표)은 정상", async () => {
    const api = { performanceGet: vi.fn(async () => ok({ market: { available: false, reason: "MARKET_FETCH_FAILED" }, comparison: null })) };
    const { getByTestId, queryByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(getByTestId("perf-market-fail").textContent).toContain("시장 데이터 불러오기 실패"));
    expect(getByTestId("perf-winrate").textContent).toContain("67%"); // 1행은 정상
    expect(queryByTestId("perf-market")).toBeNull();
  });

  it("기간 탭 전환 시 해당 period로 재조회", async () => {
    const api = { performanceGet: vi.fn(async () => ok()) };
    const { getByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(api.performanceGet).toHaveBeenCalledWith(expect.objectContaining({ period: "daily" })));
    fireEvent.click(getByTestId("perf-tab-weekly"));
    await waitFor(() => expect(api.performanceGet).toHaveBeenCalledWith(expect.objectContaining({ period: "weekly" })));
  });

  it("조회 실패(throw) → 정직한 실패 표시", async () => {
    const api = { performanceGet: vi.fn(async () => { throw new Error("net"); }) };
    const { getByTestId } = render(<PerformanceCard api={api} />);
    await waitFor(() => expect(getByTestId("perf-market-fail")).toBeTruthy());
  });
});
