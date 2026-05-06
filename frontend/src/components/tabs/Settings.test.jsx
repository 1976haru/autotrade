import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModeWarningBanner, Settings, computeModeWarning } from "./Settings";


// useBackendStatus 자체는 별도 테스트 파일에서 다룸. 여기선 Settings 본체 통합
// 시나리오를 위해 모킹.
const _statusHook = { status: null, loading: false, error: "" };
vi.mock("../../store/useBackendStatus", () => ({
  useBackendStatus: () => _statusHook,
}));


function _makeSettings(overrides = {}) {
  return {
    brokerId: "kis", broker: { id: "kis", name: "KIS", color: "#7dd3fc", fields: [] },
    tradeMode: "sim", apiKeys: { appKey: "", appSecret: "", accountNo: "" },
    connected: false, connecting: false, connMsg: "",
    switchBroker: () => {}, switchMode: () => {},
    updateKey: () => {}, connect: () => {},
    operatorName: "", setOperatorName: () => {},
    ...overrides,
  };
}


describe("computeModeWarning", () => {
  it("returns null when status is missing", () => {
    expect(computeModeWarning(null)).toBeNull();
    expect(computeModeWarning(undefined)).toBeNull();
  });

  it("returns null for SIMULATION/PAPER (no LIVE risk)", () => {
    expect(computeModeWarning({ default_mode: "SIMULATION", enable_live_trading: false }))
      .toBeNull();
    expect(computeModeWarning({ default_mode: "PAPER", enable_live_trading: false }))
      .toBeNull();
  });

  it("returns null for LIVE_SHADOW (rejection is by-design, not an accident)", () => {
    expect(computeModeWarning({ default_mode: "LIVE_SHADOW", enable_live_trading: false }))
      .toBeNull();
  });

  it("warns when LIVE_MANUAL_APPROVAL is set without ENABLE_LIVE_TRADING", () => {
    const w = computeModeWarning({
      default_mode: "LIVE_MANUAL_APPROVAL", enable_live_trading: false,
    });
    expect(w).not.toBeNull();
    expect(w.title).toContain("REJECTED");
    expect(w.detail).toContain("LIVE_MANUAL_APPROVAL");
    expect(w.detail).toContain("ENABLE_LIVE_TRADING");
  });

  it("warns for LIVE_AI_ASSIST with flag off", () => {
    const w = computeModeWarning({
      default_mode: "LIVE_AI_ASSIST", enable_live_trading: false,
    });
    expect(w).not.toBeNull();
    expect(w.detail).toContain("LIVE_AI_ASSIST");
  });

  it("warns for LIVE_AI_EXECUTION with flag off", () => {
    const w = computeModeWarning({
      default_mode: "LIVE_AI_EXECUTION", enable_live_trading: false,
    });
    expect(w).not.toBeNull();
  });

  it("returns null when LIVE mode + flag are both ON (operator opted in)", () => {
    expect(computeModeWarning({
      default_mode: "LIVE_MANUAL_APPROVAL", enable_live_trading: true,
    })).toBeNull();
  });
});


describe("<ModeWarningBanner>", () => {
  afterEach(cleanup);

  it("renders nothing when warning is null", () => {
    const { queryByTestId } = render(<ModeWarningBanner warning={null} />);
    expect(queryByTestId("mode-warning-banner")).toBeNull();
  });

  it("renders title and detail when warning is set", () => {
    const { getByTestId, container } = render(
      <ModeWarningBanner warning={{ title: "위험!", detail: "사유 텍스트" }} />,
    );
    expect(getByTestId("mode-warning-banner").textContent).toContain("위험!");
    expect(container.textContent).toContain("사유 텍스트");
  });
});


describe("<Settings> integrates the warning banner", () => {
  beforeEach(() => {
    Object.assign(_statusHook, { status: null, loading: false, error: "" });
  });
  afterEach(cleanup);

  it("renders the banner when backend reports a dangerous combination", () => {
    Object.assign(_statusHook, {
      status: { default_mode: "LIVE_MANUAL_APPROVAL", enable_live_trading: false },
    });
    const { getByTestId } = render(<Settings settings={_makeSettings()} />);
    expect(getByTestId("mode-warning-banner")).toBeTruthy();
  });

  it("does not render the banner for safe combinations", () => {
    Object.assign(_statusHook, {
      status: { default_mode: "SIMULATION", enable_live_trading: false },
    });
    const { queryByTestId } = render(<Settings settings={_makeSettings()} />);
    expect(queryByTestId("mode-warning-banner")).toBeNull();
  });

  it("does not render the banner before status has loaded", () => {
    Object.assign(_statusHook, { status: null, loading: true });
    const { queryByTestId } = render(<Settings settings={_makeSettings()} />);
    expect(queryByTestId("mode-warning-banner")).toBeNull();
  });
});
