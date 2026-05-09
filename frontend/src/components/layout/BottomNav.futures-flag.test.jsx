import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// 50: Futures 탭은 navigation에서 *feature flag로만* 노출된다. 본 테스트는
// flag false / true 두 분기에서 BottomNav (mobile) + TopNav (PC)가 각각
// futures를 어떻게 처리하는지 lock한다.
//
// 정책:
//   - flag=false → BottomNav / TopNav / TABS 어디에도 futures 미노출
//   - flag=true  → TopNav (PC)에는 노출되지만 BottomNav (mobile)에서는
//                  여전히 mobileExclude=true로 숨겨짐 (모바일 사용자 혼동 방지)

import {
  __resetFeaturesForTest,
  __setFeatureForTest,
} from "../../config/features";
import {
  BottomNav,
  TABS,
  getMobileNavTabs,
  getNavTabs,
  isTabVisible,
} from "./BottomNav";
import { TopNav } from "./TopNav";


afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  __resetFeaturesForTest();
});

beforeEach(() => {
  __resetFeaturesForTest();
});


// ====================================================================
// 1. Function-style accessors recompute on each call
// ====================================================================


describe("getNavTabs() respects FEATURES.futuresTab", () => {
  it("excludes futures when flag is false (default)", () => {
    __setFeatureForTest("futuresTab", false);
    const tabs = getNavTabs();
    expect(tabs.find((t) => t.id === "futures")).toBeUndefined();
  });

  it("includes futures when flag is true", () => {
    __setFeatureForTest("futuresTab", true);
    const tabs = getNavTabs();
    const fut = tabs.find((t) => t.id === "futures");
    expect(fut).toBeDefined();
    expect(fut.label).toBe("선물");
  });

  it("retoggling flag immediately reflects in subsequent calls", () => {
    __setFeatureForTest("futuresTab", false);
    expect(getNavTabs().find((t) => t.id === "futures")).toBeUndefined();
    __setFeatureForTest("futuresTab", true);
    expect(getNavTabs().find((t) => t.id === "futures")).toBeDefined();
  });
});


describe("getMobileNavTabs()", () => {
  it("excludes futures even when flag is true (mobileExclude policy)", () => {
    __setFeatureForTest("futuresTab", true);
    const mobile = getMobileNavTabs();
    expect(mobile.find((t) => t.id === "futures")).toBeUndefined();
    // 다른 탭들은 그대로 — 모바일 default tab 정책 유지.
    expect(mobile.find((t) => t.id === "dash")).toBeDefined();
    expect(mobile.find((t) => t.id === "config")).toBeDefined();
  });

  it("excludes futures when flag is false (mobile + flag)", () => {
    __setFeatureForTest("futuresTab", false);
    expect(getMobileNavTabs().find((t) => t.id === "futures")).toBeUndefined();
  });
});


describe("isTabVisible()", () => {
  it("reflects flag state for futures", () => {
    __setFeatureForTest("futuresTab", false);
    expect(isTabVisible("futures")).toBe(false);
    expect(isTabVisible("dash")).toBe(true);

    __setFeatureForTest("futuresTab", true);
    expect(isTabVisible("futures")).toBe(true);
  });
});


// ====================================================================
// 2. Backwards-compat TABS proxy
// ====================================================================


describe("TABS export (Proxy, backwards-compat)", () => {
  it("recomputes on each property access", () => {
    __setFeatureForTest("futuresTab", false);
    expect(TABS.length).toBe(getNavTabs().length);
    expect(TABS.find((t) => t.id === "futures")).toBeUndefined();

    __setFeatureForTest("futuresTab", true);
    expect(TABS.find((t) => t.id === "futures")).toBeDefined();
  });

  it("supports for-of iteration", () => {
    __setFeatureForTest("futuresTab", true);
    const ids = [];
    for (const t of TABS) ids.push(t.id);
    expect(ids).toContain("futures");

    __setFeatureForTest("futuresTab", false);
    const ids2 = [];
    for (const t of TABS) ids2.push(t.id);
    expect(ids2).not.toContain("futures");
  });
});


// ====================================================================
// 3. BottomNav DOM (mobile) — futures hidden in both flag states
// ====================================================================


describe("BottomNav DOM rendering", () => {
  it("does not render futures button when flag is false", () => {
    __setFeatureForTest("futuresTab", false);
    const { container } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    expect(container.textContent).not.toContain("선물");
  });

  it("still does not render futures button when flag is true (mobileExclude)", () => {
    __setFeatureForTest("futuresTab", true);
    const { container } = render(
      <BottomNav active="dash" onChange={() => {}} />,
    );
    expect(container.textContent).not.toContain("선물");
  });
});


// ====================================================================
// 4. TopNav DOM (PC) — futures shown only when flag is true
// ====================================================================


describe("TopNav DOM rendering", () => {
  it("does not render futures button when flag is false", () => {
    __setFeatureForTest("futuresTab", false);
    const { queryByTestId } = render(
      <TopNav active="dash" onChange={() => {}} />,
    );
    expect(queryByTestId("top-nav-futures")).toBeNull();
  });

  it("renders futures button when flag is true (PC nav)", () => {
    __setFeatureForTest("futuresTab", true);
    const { queryByTestId } = render(
      <TopNav active="dash" onChange={() => {}} />,
    );
    expect(queryByTestId("top-nav-futures")).toBeTruthy();
  });
});
