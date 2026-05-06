import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChipFilterBar } from "./ChipFilterBar";


// 052/073/083 wrappers each thin-shell this primitive. Wrapper tests cover
// the named-and-themed variants (e.g. "이벤트 종류 필터" with KIND_FILTERS);
// these tests cover the shared a11y/click/style contract.

describe("<ChipFilterBar>", () => {
  afterEach(cleanup);

  const _items = [
    { id: "all",   label: "전체", color: "#7dd3fc" },
    { id: "a",     label: "옵션A", color: "#22c55e" },
    { id: "b",     label: "옵션B", color: "#ef4444" },
  ];

  it("renders a radiogroup with the provided aria-label", () => {
    const { getByRole } = render(
      <ChipFilterBar items={_items} active="all" onChange={() => {}}
        ariaLabel="테스트 필터" />,
    );
    expect(getByRole("radiogroup", { name: "테스트 필터" })).toBeTruthy();
  });

  it("renders one chip per item", () => {
    const { getAllByRole } = render(
      <ChipFilterBar items={_items} active="all" onChange={() => {}}
        ariaLabel="x" />,
    );
    expect(getAllByRole("radio")).toHaveLength(3);
  });

  it("marks the active chip with aria-checked=true and others false", () => {
    const { getByRole } = render(
      <ChipFilterBar items={_items} active="a" onChange={() => {}}
        ariaLabel="x" />,
    );
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "옵션A" }).getAttribute("aria-checked")).toBe("true");
    expect(getByRole("radio", { name: "옵션B" }).getAttribute("aria-checked")).toBe("false");
  });

  it("calls onChange with the chip's id on click", () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <ChipFilterBar items={_items} active="all" onChange={onChange}
        ariaLabel="x" />,
    );
    fireEvent.click(getByRole("radio", { name: "옵션B" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });

  it("active chip uses its item color; inactive uses a neutral palette", () => {
    const { getByRole } = render(
      <ChipFilterBar items={_items} active="b" onChange={() => {}}
        ariaLabel="x" />,
    );
    const active   = getByRole("radio", { name: "옵션B" });
    const inactive = getByRole("radio", { name: "옵션A" });
    expect(active.style.color).toBe("rgb(239, 68, 68)");      // #ef4444 — own color
    expect(inactive.style.color).toBe("rgb(71, 85, 105)");    // #475569 — neutral
  });
});
