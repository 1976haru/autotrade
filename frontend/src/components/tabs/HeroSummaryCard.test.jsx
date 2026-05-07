import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HeroSummaryCard } from "./HeroSummaryCard";


// 230: useBackendStatus 모킹 — 정상 / 에러 / 로딩 분기 모두.
const _hookState = { status: null, loading: false, error: "" };
vi.mock("../../store/useBackendStatus", () => ({
  useBackendStatus: () => _hookState,
}));


function _set(over) {
  Object.assign(_hookState, { status: null, loading: false, error: "" }, over);
}


describe("<HeroSummaryCard>", () => {
  beforeEach(() => _set({}));
  afterEach(cleanup);

  it("renders app name + SIMULATION mode badge by default", async () => {
    _set({ status: { default_mode: "SIMULATION" } });
    const { getByTestId } = render(
      <HeroSummaryCard emergencyStop={false} pendingCount={0} />
    );
    await waitFor(() => {
      expect(getByTestId("hero-summary")).toBeTruthy();
    });
    expect(getByTestId("hero-mode-badge").textContent).toContain("SIMULATION");
    expect(getByTestId("hero-summary").textContent).toContain("AI 단타 자동매매");
  });

  it("shows Demo Mode pill when backend is unreachable", () => {
    _set({ error: "Failed to fetch" });
    const { getByTestId } = render(
      <HeroSummaryCard emergencyStop={false} pendingCount={0} />
    );
    expect(getByTestId("hero-conn-pill").textContent).toContain("Demo Mode");
  });

  it("shows emergency-stop pill in danger color when ON", () => {
    _set({ status: { default_mode: "SIMULATION" } });
    const { getByTestId } = render(
      <HeroSummaryCard emergencyStop={true} pendingCount={0} />
    );
    const pill = getByTestId("hero-emergency-pill");
    expect(pill.textContent).toContain("긴급 정지 ON");
  });

  it("shows pending pill only when there is pending work", () => {
    _set({ status: { default_mode: "SIMULATION" } });
    const { queryByTestId, rerender } = render(
      <HeroSummaryCard emergencyStop={false} pendingCount={0} />
    );
    expect(queryByTestId("hero-pending-pill")).toBeNull();
    rerender(<HeroSummaryCard emergencyStop={false} pendingCount={3} stalePendingCount={1} />);
    const pill = queryByTestId("hero-pending-pill");
    expect(pill).toBeTruthy();
    expect(pill.textContent).toContain("3건");
    expect(pill.textContent).toContain("stale");
  });

  it("falls back to SIMULATION mode label for unknown values", () => {
    _set({ status: { default_mode: "UNKNOWN_MODE" } });
    const { getByTestId } = render(
      <HeroSummaryCard emergencyStop={false} pendingCount={0} />
    );
    expect(getByTestId("hero-mode-badge").textContent).toContain("SIMULATION");
  });

  it("shows mode note explaining the current mode", () => {
    _set({ status: { default_mode: "VIRTUAL_AI_EXECUTION" } });
    const { getByTestId } = render(
      <HeroSummaryCard emergencyStop={false} pendingCount={0} />
    );
    expect(getByTestId("hero-mode-note").textContent).toContain("가상");
  });
});
