import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TopNav } from "./TopNav";


describe("<TopNav>", () => {
  afterEach(cleanup);

  it("renders all 11 tabs as buttons", () => {
    const { getByTestId } = render(
      <TopNav active="dash" onChange={() => {}} />
    );
    expect(getByTestId("top-nav")).toBeTruthy();
    for (const id of [
      "dash", "strat", "bot", "approve", "chart",
      "backtest", "audit", "signal", "engine", "futures", "config",
    ]) {
      expect(getByTestId(`top-nav-${id}`)).toBeTruthy();
    }
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
