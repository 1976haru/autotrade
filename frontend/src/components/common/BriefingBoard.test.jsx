import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { BriefingBoard } from "./BriefingBoard";

afterEach(() => cleanup());

const marketsOk = {
  markets: [
    { key: "sp500", label: "S&P500", value: 5432.1, change_pct: 0.8, asof_kst: "07:00", available: true },
    { key: "nasdaq", label: "나스닥", value: 17000, change_pct: -0.3, asof_kst: "07:00", available: true },
    { key: "dow", label: "다우", value: null, change_pct: null, asof_kst: "07:00", available: false },
    { key: "sox", label: "필라델피아 반도체", value: 4800, change_pct: 1.2, asof_kst: "07:00", available: true },
    { key: "usdkrw", label: "원달러", value: null, change_pct: null, asof_kst: "07:00", available: false },
  ],
  note: "미국장 마감 기준 · KST",
};
const headlinesOk = {
  headlines: [
    { title: "경제 헤드라인 A", link: "http://x/a", published_kst: "06-04 12:00" },
    { title: "경제 헤드라인 B", link: "http://x/b", published_kst: "06-04 11:00" },
  ],
  source: "연합뉴스 경제", asof_kst: "07:00", available: true, stale: false,
};

describe("BriefingBoard (B3/B5)", () => {
  it("시세 칩(등락) + 실패 칸 '—(실패)' + 헤드라인 링크/시각", async () => {
    const api = { briefingMarkets: vi.fn(async () => marketsOk), briefingHeadlines: vi.fn(async () => headlinesOk) };
    const { getByTestId } = render(<BriefingBoard api={api} />);
    await waitFor(() => expect(getByTestId("briefing-mkt-sp500").textContent).toContain("5,432.1"));
    expect(getByTestId("briefing-mkt-sp500").textContent).toContain("▲0.80%");
    expect(getByTestId("briefing-mkt-nasdaq").textContent).toContain("▼0.30%");
    expect(getByTestId("briefing-mkt-dow").textContent).toContain("—(실패 07:00)");  // 칸별 graceful
    const hl = getByTestId("briefing-hl-0");
    expect(hl.textContent).toContain("경제 헤드라인 A");
    expect(hl.querySelector("a").getAttribute("href")).toBe("http://x/a");
    expect(hl.textContent).toContain("06-04 12:00");
  });

  it("접기/펴기 토글", async () => {
    const api = { briefingMarkets: vi.fn(async () => marketsOk), briefingHeadlines: vi.fn(async () => headlinesOk) };
    const { getByTestId, queryByTestId } = render(<BriefingBoard api={api} />);
    await waitFor(() => expect(getByTestId("briefing-markets")).toBeTruthy());
    fireEvent.click(getByTestId("briefing-toggle"));
    expect(queryByTestId("briefing-markets")).toBeNull();
  });

  it("시세 조회 실패 → '시세 불러오기 실패'(빈 칩 조립 0)", async () => {
    const api = {
      briefingMarkets: vi.fn(async () => { throw new Error("x"); }),
      briefingHeadlines: vi.fn(async () => headlinesOk),
    };
    const { getByTestId, queryByTestId } = render(<BriefingBoard api={api} />);
    await waitFor(() => expect(getByTestId("briefing-markets-fail")).toBeTruthy());
    expect(queryByTestId("briefing-markets")).toBeNull();
  });

  it("헤드라인 stale 캐시 → 기준시각 라벨", async () => {
    const api = {
      briefingMarkets: vi.fn(async () => marketsOk),
      briefingHeadlines: vi.fn(async () => ({ ...headlinesOk, stale: true, asof_kst: "06:30" })),
    };
    const { getByTestId } = render(<BriefingBoard api={api} />);
    await waitFor(() => expect(getByTestId("briefing-hl-0")).toBeTruthy());
    expect(getByTestId("briefing-board").textContent).toContain("06:30 기준");
  });
});
