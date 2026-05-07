import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ModeWarningBanner, SafetyFlagsCard, Settings, computeModeWarning } from "./Settings";


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


describe("<SafetyFlagsCard>", () => {
  afterEach(cleanup);

  const _SAFE_STATUS = {
    safety_flags: {
      default_mode:                "SIMULATION",
      enable_live_trading:         false,
      enable_ai_execution:         false,
      enable_futures_live_trading: false,
      kis_is_paper:                true,
      market_data_provider:        "mock",
      enable_fill_polling:         false,
      stale_price_max_age_seconds: 60,
    },
  };

  it("renders loading state", () => {
    const { getByText } = render(<SafetyFlagsCard loading />);
    expect(getByText(/로딩/)).toBeTruthy();
  });

  it("renders error state", () => {
    // 245 (Light-008): friendly copy via friendlyErrorMessage. raw 'boom'은
    // 의미 있는 메시지로 간주돼 그대로 통과.
    const { getByText } = render(<SafetyFlagsCard error="boom" />);
    expect(getByText(/boom/)).toBeTruthy();
  });

  it("renders fallback message when safety_flags absent (older API)", () => {
    const { getByText } = render(<SafetyFlagsCard status={{}} />);
    expect(getByText(/구버전 API/)).toBeTruthy();
  });

  it("renders every flag with 안전 badge at default values", () => {
    const { getByTestId, getAllByText } = render(<SafetyFlagsCard status={_SAFE_STATUS} />);
    expect(getByTestId("safety-flag-default_mode")).toBeTruthy();
    expect(getByTestId("safety-flag-enable_live_trading")).toBeTruthy();
    expect(getByTestId("safety-flag-kis_is_paper")).toBeTruthy();
    expect(getByTestId("safety-flag-market_data_provider")).toBeTruthy();
    // 8 rows × "안전" badge.
    expect(getAllByText("안전")).toHaveLength(8);
  });

  it("flags only the unsafe deviations", () => {
    const status = {
      safety_flags: {
        ..._SAFE_STATUS.safety_flags,
        enable_live_trading: true,
        kis_is_paper:        false,
      },
    };
    const { getAllByText } = render(<SafetyFlagsCard status={status} />);
    expect(getAllByText("위험")).toHaveLength(2);
    expect(getAllByText("안전")).toHaveLength(6);
  });

  it("formats bool ON/OFF + seconds suffix", () => {
    const { getAllByText, getByText } = render(<SafetyFlagsCard status={_SAFE_STATUS} />);
    // kis_is_paper=true → ON; many others false → OFF.
    expect(getAllByText("ON").length).toBeGreaterThanOrEqual(1);
    expect(getAllByText("OFF").length).toBeGreaterThanOrEqual(4);
    expect(getByText("60s")).toBeTruthy();
  });
});
