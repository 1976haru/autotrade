import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BottomNav,
  getMobilePrimaryTabs,
  getMobileSecondaryTabs,
  getMobileNavTabs,
  getNavTabs,
} from "./BottomNav";
import { __setFeatureForTest } from "../../config/features";


// UI Final Pass — 모바일 BottomNav 5 슬롯 (primary 4 + 더보기) 구조 + 더보기
// menu가 secondary 탭 노출. 기존 navigation API (getNavTabs / TABS / 기본
// badge 동작) backwards compat은 BottomNav.test.jsx + BottomNav.futures-flag
// .test.jsx에서 별도 검증.

beforeEach(() => {
  __setFeatureForTest("futuresTab", false);
});
afterEach(() => {
  cleanup();
  __setFeatureForTest("futuresTab", false);
});


describe("getMobilePrimaryTabs / getMobileSecondaryTabs", () => {
  it("returns exactly 4 primary tabs (홈/에이전트/승인/리스크)", () => {
    const primary = getMobilePrimaryTabs();
    expect(primary).toHaveLength(4);
    const ids = primary.map((t) => t.id);
    expect(ids).toEqual(["dash", "signal", "approve", "strat"]);
  });

  it("primary tabs contain the expected labels", () => {
    const labels = getMobilePrimaryTabs().map((t) => t.label);
    expect(labels).toEqual(["홈", "에이전트", "승인", "리스크"]);
  });

  it("secondary tabs include 자동봇 / 차트 / 백테스트 / 로그 / 엔진 / 설정", () => {
    const ids = getMobileSecondaryTabs().map((t) => t.id);
    expect(ids).toContain("bot");
    expect(ids).toContain("chart");
    expect(ids).toContain("backtest");
    expect(ids).toContain("audit");
    expect(ids).toContain("engine");
    expect(ids).toContain("config");
  });

  it("secondary tabs do NOT include futures (mobileExclude=true)", () => {
    __setFeatureForTest("futuresTab", true);
    const ids = getMobileSecondaryTabs().map((t) => t.id);
    expect(ids).not.toContain("futures");
  });

  it("primary + secondary together cover all visible mobile tabs except futures", () => {
    const all = new Set(getMobileNavTabs().map((t) => t.id));
    const combined = new Set([
      ...getMobilePrimaryTabs().map((t) => t.id),
      ...getMobileSecondaryTabs().map((t) => t.id),
    ]);
    expect(combined).toEqual(all);
  });
});


describe("<BottomNav> 5-slot mobile layout", () => {
  it("renders exactly 5 mobile slots (4 primary + 더보기)", () => {
    const { container } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    const nav = container.querySelector(".app-bottomnav");
    expect(nav).toBeTruthy();
    const slots = within(nav).getAllByRole("button");
    expect(slots).toHaveLength(5);
  });

  it("renders the 더보기 toggle as 5th slot", () => {
    const { getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    const more = getByTestId("bottomnav-more-toggle");
    expect(more.textContent).toMatch(/더보기/);
  });

  it("does NOT render legacy primary tabs (자동봇 / 로그 / 차트) in main bar", () => {
    const { container } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    const nav = container.querySelector(".app-bottomnav");
    const text = nav.textContent;
    // 핵심 5 슬롯 라벨만 노출
    expect(text).toContain("홈");
    expect(text).toContain("에이전트");
    expect(text).toContain("승인");
    expect(text).toContain("리스크");
    expect(text).toContain("더보기");
    // 자동봇 / 차트 / 로그 / 백테스트 / 엔진 / 설정은 더보기 안에만
    expect(text).not.toContain("자동봇");
    expect(text).not.toContain("차트");
    expect(text).not.toContain("로그");
    expect(text).not.toContain("백테스트");
    expect(text).not.toContain("엔진");
    expect(text).not.toContain("설정");
  });
});


describe("<BottomNav> 더보기 menu", () => {
  it("does not render 더보기 menu by default", () => {
    const { queryByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    expect(queryByTestId("bottomnav-more-menu")).toBeNull();
    expect(queryByTestId("bottomnav-more-backdrop")).toBeNull();
  });

  it("opens 더보기 menu on toggle click", () => {
    const { getByTestId, queryByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    fireEvent.click(getByTestId("bottomnav-more-toggle"));
    expect(queryByTestId("bottomnav-more-menu")).not.toBeNull();
    expect(queryByTestId("bottomnav-more-backdrop")).not.toBeNull();
  });

  it("renders all secondary tabs inside 더보기 menu", () => {
    const { getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    fireEvent.click(getByTestId("bottomnav-more-toggle"));
    expect(getByTestId("bottomnav-more-bot")).toBeTruthy();
    expect(getByTestId("bottomnav-more-chart")).toBeTruthy();
    expect(getByTestId("bottomnav-more-backtest")).toBeTruthy();
    expect(getByTestId("bottomnav-more-audit")).toBeTruthy();
    expect(getByTestId("bottomnav-more-engine")).toBeTruthy();
    expect(getByTestId("bottomnav-more-config")).toBeTruthy();
  });

  it("clicking a 더보기 item fires onChange and closes menu", () => {
    const onChange = vi.fn();
    const { getByTestId, queryByTestId } = render(
      <BottomNav active="dash" onChange={onChange} />,
    );
    fireEvent.click(getByTestId("bottomnav-more-toggle"));
    fireEvent.click(getByTestId("bottomnav-more-audit"));
    expect(onChange).toHaveBeenCalledWith("audit");
    expect(queryByTestId("bottomnav-more-menu")).toBeNull();
  });

  it("backdrop click closes 더보기 menu", () => {
    const { getByTestId, queryByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    fireEvent.click(getByTestId("bottomnav-more-toggle"));
    fireEvent.click(getByTestId("bottomnav-more-backdrop"));
    expect(queryByTestId("bottomnav-more-menu")).toBeNull();
  });

  it("does NOT show futures in 더보기 even when flag is enabled (mobileExclude)", () => {
    __setFeatureForTest("futuresTab", true);
    const { getByTestId, queryByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    fireEvent.click(getByTestId("bottomnav-more-toggle"));
    expect(queryByTestId("bottomnav-more-futures")).toBeNull();
  });

  it("highlights 더보기 toggle when active tab is a secondary tab", () => {
    const { getByTestId } = render(
      <BottomNav active="audit" onChange={() => {}} />,
    );
    const more = getByTestId("bottomnav-more-toggle");
    // Active state visually applied — borderTop is the primary indicator.
    // 본 구현에서 active이면 borderTop이 var(--c-info) 색으로 강조.
    expect(more.style.borderTop).toMatch(/var\(--c-info\)|2px solid/);
    // 라벨 텍스트가 bold weight로 렌더되는지: button 안의 모든 span 중 라벨
    // ("더보기")을 포함한 span을 찾아 fontWeight 검증.
    const labelSpan = Array.from(more.querySelectorAll("span"))
      .find((s) => s.textContent === "더보기");
    expect(labelSpan).toBeTruthy();
    expect(labelSpan.style.fontWeight).toBe("700");
  });

  it("aggregates secondary badges into 더보기 toggle badge", () => {
    const { getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}}
                   badges={{ audit: 3, backtest: 2 }} />,
    );
    const moreBadge = getByTestId("badge-more");
    expect(moreBadge.textContent).toBe("5");
  });

  it("does NOT aggregate primary tab badges into 더보기", () => {
    const { queryByTestId, getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}}
                   badges={{ approve: 7 }} />,
    );
    // approve is primary — its own badge is rendered, but more aggregation
    // should be 0 (no badge rendered for more).
    expect(getByTestId("badge-approve").textContent).toBe("7");
    expect(queryByTestId("badge-more")).toBeNull();
  });
});


describe("getNavTabs (PC TopNav) backwards compat", () => {
  it("PC navigation still includes all visible tabs", () => {
    const ids = getNavTabs().map((t) => t.id);
    // 핵심 ids 모두 PC에서 노출
    for (const id of ["dash", "signal", "approve", "strat", "bot",
                       "chart", "backtest", "audit", "engine", "config"]) {
      expect(ids).toContain(id);
    }
  });

  it("PC navigation respects futures flag (off)", () => {
    __setFeatureForTest("futuresTab", false);
    expect(getNavTabs().map((t) => t.id)).not.toContain("futures");
  });

  it("PC navigation respects futures flag (on)", () => {
    __setFeatureForTest("futuresTab", true);
    expect(getNavTabs().map((t) => t.id)).toContain("futures");
  });
});
