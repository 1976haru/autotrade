import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatElapsedDuration, KillSwitchBanner } from "./KillSwitchBanner";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    emergencyStopStatus: vi.fn(),
    setEmergencyStop:    vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _status(overrides = {}) {
  return {
    level: "OFF", emergency_stop: false, reason_code: null,
    decided_by: null, note: null, active_since: null,
    cancel_candidate_count: 0, liquidation_candidate_count: 0,
    ...overrides,
  };
}

// 2026-07-14 18:00 KST (화요일, 정규장 종료 후) — 시장시간 강조 분기가 꺼져
// 있는 기본 fixture 시각.
const _AFTER_HOURS_KST = new Date("2026-07-14T09:00:00.000Z").getTime();
// 2026-07-14 10:00 KST (화요일, 정규장 중) — 강조 분기 fixture.
const _MARKET_HOURS_KST = new Date("2026-07-14T01:00:00.000Z").getTime();


describe("formatElapsedDuration", () => {
  it("formats sub-minute as 방금 경과", () => {
    const since = new Date(Date.now() - 10_000).toISOString();
    expect(formatElapsedDuration(since, Date.now())).toBe("방금 경과");
  });
  it("formats hours", () => {
    const now = Date.now();
    const since = new Date(now - 5 * 60 * 60 * 1000).toISOString();
    expect(formatElapsedDuration(since, now)).toBe("5시간 경과");
  });
  it("formats multi-day with remaining hours", () => {
    const now = Date.now();
    const since = new Date(now - (26 * 60 * 60 * 1000)).toISOString();
    // 26h = 1일 2시간
    expect(formatElapsedDuration(since, now)).toBe("1일 2시간 경과");
  });
});


describe("<KillSwitchBanner>", () => {
  beforeEach(() => {
    backendApi.emergencyStopStatus.mockReset();
    backendApi.setEmergencyStop.mockReset();
  });
  afterEach(cleanup);

  it("renders nothing while loading", () => {
    backendApi.emergencyStopStatus.mockReturnValue(new Promise(() => {})); // never resolves
    const { queryByTestId } = render(<KillSwitchBanner />);
    expect(queryByTestId("kill-switch-banner")).toBeNull();
  });

  it("renders nothing when level is OFF", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "OFF" }));
    const { queryByTestId } = render(<KillSwitchBanner />);
    await waitFor(() => expect(backendApi.emergencyStopStatus).toHaveBeenCalled());
    expect(queryByTestId("kill-switch-banner")).toBeNull();
  });

  it("renders a prominent banner when LEVEL_1 is active, with activation time and elapsed duration", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_1",
      decided_by: "operator panel",
      active_since: new Date(_AFTER_HOURS_KST - 5 * 60 * 60 * 1000).toISOString(),
    }));
    const { getByTestId } = render(
      <KillSwitchBanner now={_AFTER_HOURS_KST} />
    );

    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner-title").textContent).toContain("긴급정지 활성 중");
    expect(getByTestId("kill-switch-banner-detail").textContent).toContain("LEVEL 1");
    expect(getByTestId("kill-switch-banner-since").textContent).toContain("5시간 경과");
    expect(getByTestId("kill-switch-banner-detail").textContent).toContain("operator panel");
  });

  it("marks market-hours emphasis via data-market-hours during regular trading hours", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { getByTestId } = render(<KillSwitchBanner now={_MARKET_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner").getAttribute("data-market-hours")).toBe("true");
  });

  it("does not mark market-hours emphasis after regular trading hours", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { getByTestId } = render(<KillSwitchBanner now={_AFTER_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner").getAttribute("data-market-hours")).toBe("false");
  });

  it("the disable button calls setEmergencyStop(false, ...) and the banner disappears once OFF", async () => {
    backendApi.emergencyStopStatus
      .mockResolvedValueOnce(_status({ level: "LEVEL_1" }))
      .mockResolvedValueOnce(_status({ level: "OFF" }));
    backendApi.setEmergencyStop.mockResolvedValueOnce({ emergency_stop: false, level: "OFF" });

    const { getByTestId, queryByTestId } = render(
      <KillSwitchBanner operatorName="tester" />
    );
    await waitFor(() => getByTestId("kill-switch-banner"));

    fireEvent.click(getByTestId("kill-switch-banner-disable-btn"));

    await waitFor(() => expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(
      false, { decided_by: "tester", note: "배너에서 해제" },
    ));
    await waitFor(() => expect(queryByTestId("kill-switch-banner")).toBeNull());
  });
});
