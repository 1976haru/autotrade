import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetFeaturesForTest,
  __setFeatureForTest,
} from "../../config/features";
import { TopNav } from "./TopNav";


describe("<TopNav>", () => {
  // 50: Futures 탭은 feature flag로만 노출. default(false)에서는 10개 탭만
  // 보이고, flag=true일 때 11개 — 본 테스트는 두 분기를 모두 lock한다.
  afterEach(() => { cleanup(); __resetFeaturesForTest(); });
  beforeEach(() => { __resetFeaturesForTest(); });

  it("renders 10 tabs by default (futures hidden by feature flag)", () => {
    const { getByTestId, queryByTestId } = render(
      <TopNav active="dash" onChange={() => {}} />
    );
    expect(getByTestId("top-nav")).toBeTruthy();
    for (const id of [
      "dash", "strat", "bot", "approve", "chart",
      "backtest", "audit", "signal", "engine", "config",
    ]) {
      expect(getByTestId(`top-nav-${id}`)).toBeTruthy();
    }
    // futures 탭은 default flag(false)에서 미노출.
    expect(queryByTestId("top-nav-futures")).toBeNull();
  });

  it("renders futures tab when FEATURES.futuresTab=true", () => {
    __setFeatureForTest("futuresTab", true);
    const { getByTestId } = render(
      <TopNav active="dash" onChange={() => {}} />
    );
    expect(getByTestId("top-nav-futures")).toBeTruthy();
  });

  it("marks the active tab with aria-current=page and is-active class", () => {
    const { getByTestId } = render(
      <TopNav active="approve" onChange={() => {}} />
    );
    const active = getByTestId("top-nav-approve");
    expect(active.getAttribute("aria-current")).toBe("page");
    expect(active.className).toContain("is-active");
    const inactive = getByTestId("top-nav-dash");
    expect(inactive.getAttribute("aria-current")).toBeNull();
  });

  it("calls onChange with tab id when clicked", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(
      <TopNav active="dash" onChange={onChange} />
    );
    fireEvent.click(getByTestId("top-nav-approve"));
    expect(onChange).toHaveBeenCalledWith("approve");
  });

  it("renders pending badge only for tabs with non-zero badges", () => {
    const { getByTestId, queryByTestId } = render(
      <TopNav active="dash" onChange={() => {}} badges={{ approve: 4 }} />
    );
    expect(getByTestId("top-nav-badge-approve").textContent).toBe("4");
    expect(queryByTestId("top-nav-badge-dash")).toBeNull();
  });

  it("caps badge count at 99+", () => {
    const { getByTestId } = render(
      <TopNav active="dash" onChange={() => {}} badges={{ approve: 350 }} />
    );
    expect(getByTestId("top-nav-badge-approve").textContent).toBe("99+");
  });
});
