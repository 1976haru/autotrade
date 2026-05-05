import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { RISK_POLICY_FIELDS } from "../../config/riskPolicy";
import { BackendPolicyCard } from "./StrategyRisk";


// Defaults from backend/app/risk/risk_manager.py::RiskPolicy() — kept in
// lockstep with the frontend constants table.
const _DEFAULT_POLICY = {
  max_order_notional:  1_000_000,
  max_daily_loss:        200_000,
  max_positions:               5,
  max_symbol_exposure: 1_500_000,
  enable_live_trading:     false,
  enable_ai_execution:     false,
};


function _wrap(policy, overrides = {}) {
  return {
    policy: policy === null ? null : { ..._DEFAULT_POLICY, ...overrides },
    loading: false,
    error: "",
    emergencyStop: false,
    busy: false,
    toggleEmergency: () => {},
  };
}


describe("<BackendPolicyCard>", () => {
  afterEach(cleanup);

  it("renders all six policy fields when policy is loaded", () => {
    const { getByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY)} />);
    for (const f of RISK_POLICY_FIELDS) {
      expect(getByText(f.label)).toBeTruthy();
      expect(getByText(f.envVar)).toBeTruthy();
    }
  });

  it("shows DEFAULT badge for every field at dataclass defaults", () => {
    const { container } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY)} />);
    const badges = Array.from(container.querySelectorAll("span"))
      .filter((el) => el.textContent === "DEFAULT" || el.textContent === "OVERRIDDEN");
    // 6 fields × 1 badge each
    expect(badges).toHaveLength(6);
    for (const b of badges) {
      expect(b.textContent).toBe("DEFAULT");
    }
  });

  it("flags only the changed fields as OVERRIDDEN", () => {
    const { container } = render(
      <BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
        max_order_notional: 50_000,
        enable_live_trading: true,
      })} />,
    );
    const overridden = Array.from(container.querySelectorAll("span"))
      .filter((el) => el.textContent === "OVERRIDDEN");
    expect(overridden).toHaveLength(2);
  });

  it("renders KRW amounts with the won suffix and bool flags as ON/OFF", () => {
    const { getByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
      enable_live_trading: true,
      enable_ai_execution: false,
    })} />);
    expect(getByText("1,000,000원")).toBeTruthy(); // max_order_notional
    expect(getByText("ON")).toBeTruthy();           // enable_live_trading
    expect(getByText("OFF")).toBeTruthy();          // enable_ai_execution
  });

  it("renders a loading state when policy is null and loading is true", () => {
    const { getByText } = render(
      <BackendPolicyCard riskPolicy={{
        policy: null, loading: true, error: "",
        emergencyStop: false, busy: false, toggleEmergency: () => {},
      }} />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("renders the error message when fetch fails", () => {
    const { getByText } = render(
      <BackendPolicyCard riskPolicy={{
        policy: null, loading: false, error: "policy fetch failed",
        emergencyStop: false, busy: false, toggleEmergency: () => {},
      }} />,
    );
    expect(getByText("policy fetch failed")).toBeTruthy();
  });

  it("changes emergency-stop block accent when stop is active", () => {
    const { getByText } = render(
      <BackendPolicyCard riskPolicy={{
        policy: _DEFAULT_POLICY, loading: false, error: "",
        emergencyStop: true, busy: false, toggleEmergency: () => {},
      }} />,
    );
    // The text content in the emergency-stop banner contains "ACTIVE" when on
    const banner = getByText(/긴급 정지/);
    expect(within(banner.parentElement).getByText(/ACTIVE/)).toBeTruthy();
  });
});
