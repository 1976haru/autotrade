import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfigureCard, PositionBlock } from "./LiveEngine";


const _REGISTRY = [
  {
    name: "sma_crossover",
    class_name: "SmaCrossoverStrategy",
    description: "단기/장기 이동평균 교차 전략",
    params: [
      { name: "short", type: "int", default: 5,  required: false },
      { name: "long",  type: "int", default: 20, required: false },
    ],
  },
  {
    name: "needs_threshold",
    class_name: "ThresholdStrategy",
    description: "synthetic test strategy",
    params: [
      { name: "threshold", type: "int", default: null, required: true },
    ],
  },
];


describe("<ConfigureCard>", () => {
  afterEach(cleanup);

  it("shows a loading placeholder while registry is null", () => {
    const { getByText } = render(<ConfigureCard busy={false} registry={null} onConfigure={() => {}} />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows an empty-registry message when no strategies are registered", () => {
    const { getByText } = render(<ConfigureCard busy={false} registry={[]} onConfigure={() => {}} />);
    expect(getByText(/등록된 전략이 없습니다/)).toBeTruthy();
  });

  it("renders a dropdown with one option per registered strategy", async () => {
    const { container } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    const select = container.querySelector("select");
    expect(select).toBeTruthy();
    expect(select.options).toHaveLength(2);
    expect(select.options[0].value).toBe("sma_crossover");
    expect(select.options[1].value).toBe("needs_threshold");
  });

  it("renders param inputs prefilled from defaults when a strategy is selected", async () => {
    const { container } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    // sma_crossover is the default-selected strategy; both params should appear
    // with their defaults populated as strings.
    await waitFor(() => {
      const inputs = container.querySelectorAll('input[type="number"]');
      // 2 strategy params + quantity input = 3
      expect(inputs.length).toBe(3);
    });
    const numberInputs = container.querySelectorAll('input[type="number"]');
    expect(numberInputs[0].value).toBe("5");   // short
    expect(numberInputs[1].value).toBe("20");  // long
    expect(numberInputs[2].value).toBe("1");   // quantity default
  });

  it("submits typed param values via onConfigure", async () => {
    const onConfigure = vi.fn();
    const { container, getByRole } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={onConfigure} />,
    );

    await waitFor(() => {
      expect(container.querySelectorAll('input[type="number"]').length).toBe(3);
    });

    fireEvent.click(getByRole("button"));
    expect(onConfigure).toHaveBeenCalledTimes(1);
    expect(onConfigure).toHaveBeenCalledWith({
      strategy: "sma_crossover",
      params:   { short: 5, long: 20 },
      quantity: 1,
    });
  });

  it("resets param values to the new strategy's schema when selection changes", async () => {
    const onConfigure = vi.fn();
    const { container, getByRole } = render(
      <ConfigureCard busy={false} registry={_REGISTRY} onConfigure={onConfigure} />,
    );

    const select = container.querySelector("select");
    await act(async () => {
      fireEvent.change(select, { target: { value: "needs_threshold" } });
    });

    await waitFor(() => {
      // sma's two params replaced by threshold's single param + quantity = 2
      expect(container.querySelectorAll('input[type="number"]').length).toBe(2);
    });

    // threshold has default=null/required so its input is empty.
    const inputs = container.querySelectorAll('input[type="number"]');
    expect(inputs[0].value).toBe("");
    // submit it; empty cast yields undefined, so params should be {} — backend
    // will then surface a validation error rather than the frontend silently
    // sending threshold=NaN.
    fireEvent.click(getByRole("button"));
    expect(onConfigure).toHaveBeenCalledWith({
      strategy: "needs_threshold",
      params:   {},
      quantity: 1,
    });
  });

  it("disables the submit button while busy", () => {
    const { getByRole } = render(
      <ConfigureCard busy={true} registry={_REGISTRY} onConfigure={() => {}} />,
    );
    expect(getByRole("button").disabled).toBe(true);
  });
});


describe("<PositionBlock>", () => {
  afterEach(cleanup);

  function _status(overrides = {}) {
    return {
      entry_price: 75_000, last_price: 76_000,
      unrealized_pnl: 10_000, unrealized_pnl_pct: 0.0133,
      ...overrides,
    };
  }

  it("renders entry / current / pnl values with green when profitable", () => {
    const { container, getByText } = render(<PositionBlock status={_status()} />);
    expect(getByText("75,000원")).toBeTruthy();   // entry
    expect(getByText("76,000원")).toBeTruthy();   // current
    const pnlBlock = container.querySelector('[data-testid="position-block"]');
    expect(pnlBlock.textContent).toContain("+10,000");
    expect(pnlBlock.textContent).toContain("+1.33%");
  });

  it("uses red color and minus sign when pnl is negative", () => {
    const { container } = render(
      <PositionBlock status={_status({ unrealized_pnl: -5_000, unrealized_pnl_pct: -0.0667 })} />,
    );
    const block = container.querySelector('[data-testid="position-block"]');
    expect(block.textContent).toContain("-5,000");
    expect(block.textContent).toContain("-6.67%");
    // At least one descendant carries the red color inline.
    const reds = Array.from(block.querySelectorAll("*"))
      .filter((el) => el.style?.color === "rgb(239, 68, 68)"); // #ef4444
    expect(reds.length).toBeGreaterThan(0);
  });

  it("falls back to em-dashes when fields are null", () => {
    const { container } = render(
      <PositionBlock status={{
        entry_price: 75_000, last_price: null,
        unrealized_pnl: null, unrealized_pnl_pct: null,
      }} />,
    );
    const block = container.querySelector('[data-testid="position-block"]');
    expect(block.textContent).toContain("75,000원");
    expect(block.textContent).toContain("—"); // last_price + pnl
  });
});
