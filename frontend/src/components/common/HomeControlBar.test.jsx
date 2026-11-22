import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup } from "@testing-library/react";
import { HomeControlBar } from "./HomeControlBar";

describe("HomeControlBar", () => {
  afterEach(() => cleanup());

  it("stopped: shows 시작, click calls onStart (existing handler)", () => {
    const onStart = vi.fn(); const onStop = vi.fn();
    const { getByTestId } = render(
      <HomeControlBar running={false} onStart={onStart} onStop={onStop}
        emergencyStop={false} onEmergencyStop={vi.fn()} />,
    );
    const btn = getByTestId("home-control-startstop");
    expect(btn.textContent).toContain("시작");
    fireEvent.click(btn);
    expect(onStart).toHaveBeenCalledTimes(1);
    expect(onStop).not.toHaveBeenCalled();
  });

  it("running: shows 정지, click calls onStop", () => {
    const onStart = vi.fn(); const onStop = vi.fn();
    const { getByTestId } = render(
      <HomeControlBar running={true} onStart={onStart} onStop={onStop}
        emergencyStop={false} onEmergencyStop={vi.fn()} />,
    );
    const btn = getByTestId("home-control-startstop");
    expect(btn.textContent).toContain("정지");
    fireEvent.click(btn);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("emergency button calls onEmergencyStop", () => {
    const onEmergencyStop = vi.fn();
    const { getByTestId } = render(
      <HomeControlBar running={false} onStart={vi.fn()} onStop={vi.fn()}
        emergencyStop={false} onEmergencyStop={onEmergencyStop} />,
    );
    fireEvent.click(getByTestId("home-control-emergency"));
    expect(onEmergencyStop).toHaveBeenCalledTimes(1);
  });

  it("preserves safety text (실거래 OFF · KIS_IS_PAPER)", () => {
    const { getByTestId } = render(
      <HomeControlBar running={false} onStart={vi.fn()} onStop={vi.fn()}
        emergencyStop={false} onEmergencyStop={vi.fn()} />,
    );
    const bar = getByTestId("home-control-bar").parentElement;
    expect(bar.textContent).toContain("실거래 OFF");
    expect(bar.textContent).toContain("KIS_IS_PAPER");
  });
});
