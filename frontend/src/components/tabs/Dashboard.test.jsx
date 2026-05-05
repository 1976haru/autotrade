import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StatusPin, StatusSummaryCard } from "./Dashboard";


describe("<StatusPin>", () => {
  afterEach(cleanup);

  it("renders neutral colors when alarm is false", () => {
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="OFF"
        alarm={false} accent="#ef4444"
        onClick={() => {}} testId="pin-x"
      />,
    );
    const pin = getByTestId("pin-x");
    expect(pin.style.color).toBe("rgb(148, 163, 184)"); // #94a3b8
  });

  it("switches to accent color when alarm is true", () => {
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="ACTIVE"
        alarm={true} accent="#ef4444"
        onClick={() => {}} testId="pin-x"
      />,
    );
    expect(getByTestId("pin-x").style.color).toBe("rgb(239, 68, 68)");
  });

  it("invokes onClick when clicked", () => {
    const onClick = vi.fn();
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="OFF"
        alarm={false} accent="#ef4444"
        onClick={onClick} testId="pin-x"
      />,
    );
    fireEvent.click(getByTestId("pin-x"));
    expect(onClick).toHaveBeenCalled();
  });
});


describe("<StatusSummaryCard>", () => {
  afterEach(cleanup);

  it("renders all three pins regardless of state", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    expect(getByTestId("status-pin-emergency-stop")).toBeTruthy();
    expect(getByTestId("status-pin-pending-approvals")).toBeTruthy();
    expect(getByTestId("status-pin-bot")).toBeTruthy();
  });

  it("emergency-stop pin shows ACTIVE in red when on, OFF in neutral when off", () => {
    const { rerender, getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={true} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    let pin = getByTestId("status-pin-emergency-stop");
    expect(pin.textContent).toContain("ACTIVE");
    expect(pin.style.color).toBe("rgb(239, 68, 68)");

    rerender(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    pin = getByTestId("status-pin-emergency-stop");
    expect(pin.textContent).toContain("OFF");
    expect(pin.style.color).toBe("rgb(148, 163, 184)");
  });

  it("pending-approvals pin shows count when > 0 and is alarm", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={3} running={false}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-pending-approvals");
    expect(pin.textContent).toContain("3건");
    expect(pin.style.color).toBe("rgb(245, 158, 11)"); // #f59e0b
  });

  it("pending-approvals pin shows '없음' in neutral when count is 0", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-pending-approvals");
    expect(pin.textContent).toContain("없음");
    expect(pin.style.color).toBe("rgb(148, 163, 184)");
  });

  it("bot pin shows RUNNING in green when running", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={true}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).toContain("RUNNING");
    expect(pin.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("clicking each pin calls onJumpTab with the correct tab id", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={onJumpTab}
      />,
    );
    fireEvent.click(getByTestId("status-pin-emergency-stop"));
    expect(onJumpTab).toHaveBeenLastCalledWith("strat");
    fireEvent.click(getByTestId("status-pin-pending-approvals"));
    expect(onJumpTab).toHaveBeenLastCalledWith("approve");
    fireEvent.click(getByTestId("status-pin-bot"));
    expect(onJumpTab).toHaveBeenLastCalledWith("bot");
  });

  it("works without onJumpTab (clicks do not throw)", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
      />,
    );
    // Should not throw
    fireEvent.click(getByTestId("status-pin-bot"));
  });
});
