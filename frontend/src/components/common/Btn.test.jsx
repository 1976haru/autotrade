import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Btn } from "./index";


describe("<Btn>", () => {
  afterEach(cleanup);

  it("renders the children", () => {
    const { getByRole } = render(<Btn>저장</Btn>);
    expect(getByRole("button").textContent).toBe("저장");
  });

  it("invokes onClick when clicked", () => {
    const onClick = vi.fn();
    const { getByRole } = render(<Btn onClick={onClick}>실행</Btn>);
    fireEvent.click(getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not invoke onClick when disabled", () => {
    const onClick = vi.fn();
    const { getByRole } = render(<Btn onClick={onClick} disabled>실행</Btn>);
    fireEvent.click(getByRole("button"));
    expect(onClick).not.toHaveBeenCalled();
  });

  it("applies the disabled attribute when disabled", () => {
    const { getByRole } = render(<Btn disabled>실행</Btn>);
    expect(getByRole("button").disabled).toBe(true);
  });

  it("uses 100% width when full prop is set", () => {
    const { getByRole } = render(<Btn full>전체</Btn>);
    expect(getByRole("button").style.width).toBe("100%");
  });
});
