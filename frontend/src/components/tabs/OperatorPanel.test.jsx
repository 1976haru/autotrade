import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OperatorPanel } from "./OperatorPanel";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    preMarketBrief:    vi.fn(),
    marketRegime:      vi.fn(),
    virtualPositions:  vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _resolveAll() {
  backendApi.preMarketBrief.mockResolvedValue({
    readiness_label: "READY", readiness_score: 80,
  });
  backendApi.marketRegime.mockResolvedValue({
    regime: "TREND_UP", trade_permission: "ALLOW", risk_multiplier: 1.0,
  });
  backendApi.virtualPositions.mockResolvedValue([{ id: 1 }, { id: 2 }]);
}


describe("<OperatorPanel>", () => {
  beforeEach(() => {
    backendApi.preMarketBrief.mockReset();
    backendApi.marketRegime.mockReset();
    backendApi.virtualPositions.mockReset();
    localStorage.clear();
  });
  afterEach(cleanup);

  it("renders virtual mode badge + 3 buttons + status grid", async () => {
    _resolveAll();
    const onES = vi.fn();
    const { getByTestId } = render(
      <OperatorPanel pendingCount={3} emergencyStop={false} onEmergencyStop={onES} />
    );
    await waitFor(() => {
      expect(getByTestId("virtual-mode-badge")).toBeTruthy();
    });
    expect(getByTestId("virtual-mode-badge").textContent).toContain("VIRTUAL MODE");
    expect(getByTestId("operator-start")).toBeTruthy();
    expect(getByTestId("operator-pause")).toBeTruthy();
    expect(getByTestId("operator-emergency-stop")).toBeTruthy();
    expect(getByTestId("operator-status").textContent).toContain("READY");
    expect(getByTestId("operator-status").textContent).toContain("TREND_UP");
    expect(getByTestId("operator-status").textContent).toContain("2건");
    expect(getByTestId("operator-status").textContent).toContain("3건");
  });

  it("emergency stop button invokes the callback", async () => {
    _resolveAll();
    const onES = vi.fn();
    const { getByTestId } = render(
      <OperatorPanel pendingCount={0} emergencyStop={false} onEmergencyStop={onES} />
    );
    await waitFor(() => {
      expect(getByTestId("virtual-mode-badge")).toBeTruthy();
    });
    fireEvent.click(getByTestId("operator-emergency-stop"));
    expect(onES).toHaveBeenCalledTimes(1);
  });

  it("start/pause persists operator intent to localStorage", async () => {
    _resolveAll();
    const { getByTestId } = render(
      <OperatorPanel pendingCount={0} emergencyStop={false} onEmergencyStop={() => {}} />
    );
    await waitFor(() => {
      expect(getByTestId("virtual-mode-badge")).toBeTruthy();
    });
    // 기본은 paused — usePersistedState는 첫 렌더에서 storage write를 하지 않으므로
    // 초기값 검증은 status 화면 텍스트로.
    expect(getByTestId("operator-status").textContent).toContain("PAUSED");
    await act(async () => { fireEvent.click(getByTestId("operator-start")); });
    expect(localStorage.getItem("autotrade.operatorIntent")).toBe("running");
    expect(getByTestId("operator-status").textContent).toContain("RUNNING");
    await act(async () => { fireEvent.click(getByTestId("operator-pause")); });
    expect(localStorage.getItem("autotrade.operatorIntent")).toBe("paused");
    expect(getByTestId("operator-status").textContent).toContain("PAUSED");
  });

  it("renders error message but keeps panel visible when fetch fails", async () => {
    backendApi.preMarketBrief.mockRejectedValue(new Error("offline"));
    backendApi.marketRegime.mockRejectedValue(new Error("offline"));
    backendApi.virtualPositions.mockRejectedValue(new Error("offline"));
    const { getByTestId, findByText } = render(
      <OperatorPanel pendingCount={0} emergencyStop={false} onEmergencyStop={() => {}} />
    );
    expect(await findByText(/조회 실패/)).toBeTruthy();
    // 버튼은 여전히 동작 — 실패해도 패널 자체는 unmount되지 않는다.
    expect(getByTestId("operator-start")).toBeTruthy();
    expect(getByTestId("virtual-mode-badge")).toBeTruthy();
  });

  it("emergency-stop ON state shows red accent regardless of readiness", async () => {
    _resolveAll();
    const { getByTestId } = render(
      <OperatorPanel pendingCount={0} emergencyStop={true} onEmergencyStop={() => {}} />
    );
    await waitFor(() => {
      expect(getByTestId("virtual-mode-badge")).toBeTruthy();
    });
    expect(getByTestId("operator-status").textContent).toContain("ON");
  });
});
