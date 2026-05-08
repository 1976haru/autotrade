import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KillSwitchPanel } from "./KillSwitchPanel";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    emergencyStopStatus: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _status(overrides = {}) {
  return {
    level:                       "OFF",
    emergency_stop:              false,
    reason_code:                 null,
    decided_by:                  null,
    note:                        null,
    active_since:                null,
    cancel_candidate_count:      0,
    liquidation_candidate_count: 0,
    ...overrides,
  };
}


describe("<KillSwitchPanel>", () => {
  beforeEach(() => {
    backendApi.emergencyStopStatus.mockReset();
  });
  afterEach(cleanup);

  it("renders loading first then panel", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<KillSwitchPanel />);
    expect(getByTestId("killswitch-panel-loading")).toBeTruthy();
    await waitFor(() => getByTestId("killswitch-panel"));
  });

  it("renders OFF state with success badge", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "OFF" }));
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    expect(getByTestId("killswitch-panel-level-badge").textContent).toContain("OFF");
  });

  it("renders LEVEL_2 state highlighting LEVEL_1 + LEVEL_2 rows as active", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_2", emergency_stop: true, reason_code: "data_stale",
      decided_by: "ops1", cancel_candidate_count: 4,
    }));
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    expect(getByTestId("killswitch-level-row-LEVEL_1").getAttribute("data-active")).toBe("true");
    expect(getByTestId("killswitch-level-row-LEVEL_2").getAttribute("data-active")).toBe("true");
    expect(getByTestId("killswitch-level-row-LEVEL_3").getAttribute("data-active")).toBe("false");
    expect(getByTestId("killswitch-panel-cancel-count").textContent).toContain("4");
  });

  it("renders LEVEL_3 highlighting all three rows", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_3", emergency_stop: true,
      cancel_candidate_count: 2, liquidation_candidate_count: 5,
    }));
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    expect(getByTestId("killswitch-level-row-LEVEL_1").getAttribute("data-active")).toBe("true");
    expect(getByTestId("killswitch-level-row-LEVEL_2").getAttribute("data-active")).toBe("true");
    expect(getByTestId("killswitch-level-row-LEVEL_3").getAttribute("data-active")).toBe("true");
    expect(getByTestId("killswitch-panel-liquidation-count").textContent).toContain("5");
  });

  it("renders the warning about manual approval for liquidation", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_3" }));
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    const warning = getByTestId("killswitch-panel-warning");
    expect(warning.textContent).toContain("자동 청산은 비활성화");
    expect(warning.textContent).toContain("수동 승인");
  });

  it("does NOT render any auto-liquidation or auto-cancel button", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_3" }));
    const { container } = render(<KillSwitchPanel />);
    await waitFor(() =>
      expect(container.querySelector('[data-testid="killswitch-panel"]')).toBeTruthy(),
    );
    // 자동 청산 / 자동 취소 버튼은 절대 만들지 않는다 (#37 절대 원칙).
    expect(container.querySelector('button[data-testid*="liquidate"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="auto-cancel"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="cancel-all"]')).toBeNull();
  });

  it("does not expose raw 'Failed to fetch' error text", async () => {
    backendApi.emergencyStopStatus.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), {}),
    );
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel-error"));
    const errPanel = getByTestId("killswitch-panel-error");
    expect(errPanel.textContent).toContain("백엔드 서버에 연결할 수 없습니다");
    expect(errPanel.textContent).not.toContain("Failed to fetch");
  });

  it("refresh button re-fetches", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(1);
    fireEvent.click(getByTestId("killswitch-panel-refresh"));
    await waitFor(() => expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(2));
  });

  it("renders reason_code and decided_by metadata when active", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_1", emergency_stop: true,
      reason_code: "broker_error", decided_by: "ops1",
    }));
    const { getByTestId } = render(<KillSwitchPanel />);
    await waitFor(() => getByTestId("killswitch-panel"));
    const panel = getByTestId("killswitch-panel");
    expect(panel.textContent).toContain("broker_error");
    expect(panel.textContent).toContain("ops1");
  });
});
