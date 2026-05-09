import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NewsTrendCard, useNewsTrend } from "./NewsTrendCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { newsTrend: vi.fn() },
}));


const _DEFAULT_SNAPSHOT = {
  recommended_action: "MONITOR",
  summary_lines: [
    "테마 신호 모니터링 중.",
    "전체 신호 4건, 상위 테마 2개, 후보 종목 3개.",
    "상위 테마: AI, 2차전지",
    "본 요약은 *주문 신호가 아닙니다*. 후보 필터 / Agent context 전용.",
  ],
  top_themes: [
    {
      theme: "AI", score: 85, grade: "STRONG", confidence: 70,
      related_symbols: ["005930", "000660"], keywords: ["gpu", "ai"],
      sample_summary: "AI demand surge", provider: "mock",
      signal_count: 3,
    },
    {
      theme: "2차전지", score: 70, grade: "WATCH", confidence: 65,
      related_symbols: ["051910"], keywords: ["배터리"],
      sample_summary: null, provider: "mock", signal_count: 1,
    },
  ],
  rising_keywords: [
    { keyword: "gpu", occurrence: 2 },
    { keyword: "ai", occurrence: 2 },
    { keyword: "배터리", occurrence: 1 },
  ],
  related_candidates: [
    { symbol: "005930", occurrence: 2, themes: ["AI"] },
    { symbol: "000660", occurrence: 1, themes: ["AI"] },
    { symbol: "051910", occurrence: 1, themes: ["2차전지"] },
  ],
  caution_themes: [],
  overheating_warnings: [],
  used_for_order_warnings: [],
  total_signal_count: 4,
  window_seconds: null,
  is_order_signal: false,
  created_at: "2026-05-09T12:00:00+00:00",
};


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.newsTrend.mockResolvedValue(_DEFAULT_SNAPSHOT);
});


describe("<NewsTrendCard>", () => {
  it("renders '주문 신호 아님' badge prominently", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    expect(getByTestId("news-trend-not-order-badge").textContent)
      .toMatch(/주문 신호 아님|후보 필터/);
  });

  it("renders summary lines", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    expect(getByTestId("news-trend-line-0").textContent)
      .toMatch(/모니터링|모니터링 중/);
    expect(getByTestId("news-trend-line-1").textContent)
      .toMatch(/전체 신호|상위 테마/);
  });

  it("renders top themes with scores", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    const themes = getByTestId("news-trend-top-themes");
    expect(themes.textContent).toContain("AI");
    expect(themes.textContent).toContain("2차전지");
    expect(themes.textContent).toContain("85");   // score
    expect(themes.textContent).toContain("70");
  });

  it("renders rising keywords as chips", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    const chips = getByTestId("news-trend-keywords");
    expect(chips.textContent).toContain("gpu");
    expect(chips.textContent).toContain("ai");
  });

  it("renders related candidates as chips", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    const chips = getByTestId("news-trend-candidates");
    expect(chips.textContent).toContain("005930");
  });

  it("does NOT render any BUY/SELL/HOLD buttons or text in primary CTA", () => {
    const { container } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      expect(text).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행/);
    }
  });

  it("renders disclaimer that this is NOT an order signal", () => {
    const { container } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error="" />,
    );
    expect(container.textContent).toMatch(/주문 신호가 아닙니다/);
  });

  it("renders overheating warnings prominently when present", () => {
    const overheatedSnap = {
      ..._DEFAULT_SNAPSHOT,
      recommended_action: "OVERHEAT_WARN",
      overheating_warnings: ["AI: score=95 signal_count=6 — 추격 매수 자제"],
    };
    const { getByTestId } = render(
      <NewsTrendCard snapshot={overheatedSnap}
                       loading={false} error="" />,
    );
    const warn = getByTestId("news-trend-overheating");
    expect(warn.textContent).toContain("과열 경고");
    expect(warn.textContent).toContain("AI");
  });

  it("renders invariant warning when used_for_order rows present", () => {
    const violatingSnap = {
      ..._DEFAULT_SNAPSHOT,
      recommended_action: "CAUTION",
      used_for_order_warnings: [
        "theme_signal id=42 (theme=AI) used_for_order=True — 주문 사용 의심",
      ],
    };
    const { getByTestId } = render(
      <NewsTrendCard snapshot={violatingSnap}
                       loading={false} error="" />,
    );
    const warn = getByTestId("news-trend-invariant-warn");
    expect(warn.textContent).toContain("invariant 위반 의심");
    expect(warn.textContent).toContain("id=42");
  });

  it("shows loading state without snapshot", () => {
    const { getByText } = render(
      <NewsTrendCard snapshot={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows friendly fallback on error", () => {
    const { getByTestId } = render(
      <NewsTrendCard snapshot={null} loading={false} error="boom" />,
    );
    const err = getByTestId("news-trend-error");
    expect(err.textContent).toMatch(/뉴스\/트렌드 데이터/);
    // raw "boom" 메시지를 그대로 보여주지 않음.
  });

  it("renders nothing when snapshot is null and no loading/error", () => {
    const { container } = render(
      <NewsTrendCard snapshot={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getAllByText } = render(
      <NewsTrendCard snapshot={_DEFAULT_SNAPSHOT}
                       loading={false} error=""
                       onRefresh={onRefresh} />,
    );
    fireEvent.click(getAllByText(/새로고침/)[0]);
    expect(onRefresh).toHaveBeenCalled();
  });
});


describe("useNewsTrend integration", () => {
  it("calls API on mount and renders into card", async () => {
    function Probe() {
      const r = useNewsTrend();
      return <NewsTrendCard
        snapshot={r.snapshot} loading={r.loading} error={r.error}
        onRefresh={r.refresh}
      />;
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.newsTrend).toHaveBeenCalled());
    const action = await findByTestId("news-trend-action");
    expect(action.textContent).toContain("모니터링");
  });
});
