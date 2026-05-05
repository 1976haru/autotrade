import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BottomNav, _badgeLabel } from "./BottomNav";


describe("_badgeLabel", () => {
  it("returns the count as string for normal values", () => {
    expect(_badgeLabel(1)).toBe("1");
    expect(_badgeLabel(99)).toBe("99");
  });

  it("caps at 99+ for runaway queues", () => {
    expect(_badgeLabel(100)).toBe("99+");
    expect(_badgeLabel(500)).toBe("99+");
  });
});


describe("<BottomNav> badge rendering", () => {
  afterEach(cleanup);

  it("renders no badge when count is 0 or missing", () => {
    const { queryByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} badges={{ approve: 0 }} />,
    );
    expect(queryByTestId("badge-approve")).toBeNull();
  });

  it("renders a badge with the count when > 0", () => {
    const { getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} badges={{ approve: 3 }} />,
    );
    const badge = getByTestId("badge-approve");
    expect(badge.textContent).toBe("3");
    // Red so it actually catches the eye in a glance
    expect(badge.style.background).toBe("rgb(239, 68, 68)");
  });

  it("caps display at 99+ for large counts", () => {
    const { getByTestId } = render(
      <BottomNav active="dash" onChange={() => {}} badges={{ approve: 250 }} />,
    );
    expect(getByTestId("badge-approve").textContent).toBe("99+");
  });

  it("renders multiple badges independently when multiple tabs have counts", () => {
    const { getByTestId, queryByTestId } = render(
      <BottomNav
        active="dash"
        onChange={() => {}}
        badges={{ approve: 2, audit: 0, signal: 5 }}
      />,
    );
    expect(getByTestId("badge-approve").textContent).toBe("2");
    expect(getByTestId("badge-signal").textContent).toBe("5");
    expect(queryByTestId("badge-audit")).toBeNull();
  });

  it("works with no badges prop (back-compat)", () => {
    // No throw, no badges rendered
    const { queryByTestId } = render(<BottomNav active="dash" onChange={() => {}} />);
    expect(queryByTestId("badge-approve")).toBeNull();
  });

  it("clicking a tab still fires onChange when a badge is present", () => {
    const onChange = vi.fn();
    const { getByText } = render(
      <BottomNav active="dash" onChange={onChange} badges={{ approve: 1 }} />,
    );
    fireEvent.click(getByText("승인"));
    expect(onChange).toHaveBeenCalledWith("approve");
  });
});
