import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RISK_POLICY_FIELDS } from "../../config/riskPolicy";
import {
  BackendPolicyCard,
  EmergencyStopConfirmModal,
  EmergencyStopHistoryCard,
  EmergencyStopHistoryRow,
} from "./StrategyRisk";


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

  it("opens the confirm modal on toggle-button click without firing toggleEmergency yet", () => {
    const toggleEmergency = vi.fn();
    const { getByText, queryByRole } = render(
      <BackendPolicyCard riskPolicy={{ ..._wrap(_DEFAULT_POLICY), toggleEmergency }} />,
    );
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByText("긴급 정지"));
    expect(queryByRole("dialog")).not.toBeNull();
    expect(toggleEmergency).not.toHaveBeenCalled();
  });

  it("modal confirm forwards decided_by + note to toggleEmergency and closes", async () => {
    const toggleEmergency = vi.fn().mockResolvedValue();
    const { getByText, getByPlaceholderText, queryByRole } = render(
      <BackendPolicyCard riskPolicy={{ ..._wrap(_DEFAULT_POLICY), toggleEmergency }} />,
    );
    fireEvent.click(getByText("긴급 정지"));
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: "ops1" } });
    fireEvent.change(getByPlaceholderText(/vol spike/), { target: { value: "circuit-breaker" } });
    await act(async () => {
      fireEvent.click(getByText("확인"));
    });
    expect(toggleEmergency).toHaveBeenCalledWith({
      decided_by: "ops1", note: "circuit-breaker",
    });
    expect(queryByRole("dialog")).toBeNull();
  });

  it("modal cancel closes without calling toggleEmergency", () => {
    const toggleEmergency = vi.fn();
    const { getByText, queryByRole } = render(
      <BackendPolicyCard riskPolicy={{ ..._wrap(_DEFAULT_POLICY), toggleEmergency }} />,
    );
    fireEvent.click(getByText("긴급 정지"));
    fireEvent.click(getByText("취소"));
    expect(queryByRole("dialog")).toBeNull();
    expect(toggleEmergency).not.toHaveBeenCalled();
  });

  it("forwards operatorName as the modal's prefilled decided_by", () => {
    const { getByText, getByPlaceholderText } = render(
      <BackendPolicyCard
        riskPolicy={_wrap(_DEFAULT_POLICY)}
        operatorName="ops-prefill"
      />,
    );
    fireEvent.click(getByText("긴급 정지"));
    expect(getByPlaceholderText(/ops1/).value).toBe("ops-prefill");
  });
});


describe("<EmergencyStopConfirmModal>", () => {
  afterEach(cleanup);

  it("titles the dialog as activation when targetEnabled=true", () => {
    const { getByRole } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("긴급 정지 활성화");
  });

  it("titles the dialog as release when targetEnabled=false", () => {
    const { getByRole } = render(
      <EmergencyStopConfirmModal
        targetEnabled={false} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("긴급 정지 해제");
  });

  it("trims surrounding whitespace before forwarding values", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: "  ops1 " } });
    fireEvent.change(getByPlaceholderText(/vol spike/), { target: { value: " note " } });
    fireEvent.click(getByText("확인"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "note" });
  });

  it("pre-fills decided_by from defaultDecidedBy when provided", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        defaultDecidedBy="ops-default"
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    expect(getByPlaceholderText(/ops1/).value).toBe("ops-default");
    // Confirming without editing should forward the prefilled value.
    fireEvent.click(getByText("확인"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-default", note: "" });
  });

  it("operator can override the prefilled decided_by before confirming", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        defaultDecidedBy="ops-default"
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: "ops-other" } });
    fireEvent.click(getByText("확인"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-other", note: "" });
  });

  it("disables both buttons while busy", () => {
    const { getByText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={true}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByText("취소").disabled).toBe(true);
    expect(getByText(/처리 중/).disabled).toBe(true);
  });
});


describe("<EmergencyStopHistoryRow>", () => {
  afterEach(cleanup);

  it("renders ON badge in red when enabled", () => {
    const { getByText } = render(
      <EmergencyStopHistoryRow event={{
        id: 1, created_at: "2026-05-05T12:00:00+00:00",
        enabled: true, decided_by: "ops", note: "vol spike",
      }} />,
    );
    const badge = getByText("ON");
    expect(badge.style.color).toBe("rgb(239, 68, 68)"); // #ef4444
  });

  it("renders OFF badge in green when disabled", () => {
    const { getByText } = render(
      <EmergencyStopHistoryRow event={{
        id: 2, created_at: "2026-05-05T12:05:00+00:00",
        enabled: false, decided_by: null, note: null,
      }} />,
    );
    expect(getByText("OFF").style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("renders decided_by + note metadata when present", () => {
    const { container } = render(
      <EmergencyStopHistoryRow event={{
        id: 3, created_at: "2026-05-05T12:00:00+00:00",
        enabled: true, decided_by: "trader1", note: "circuit breaker",
      }} />,
    );
    expect(container.textContent).toContain("by trader1");
    expect(container.textContent).toContain("circuit breaker");
  });
});


describe("<EmergencyStopHistoryCard>", () => {
  afterEach(cleanup);

  it("renders an empty state when there are no events", () => {
    const { getByText } = render(<EmergencyStopHistoryCard history={[]} />);
    expect(getByText(/기록된 토글 없음/)).toBeTruthy();
  });

  it("renders one row per event", () => {
    const { container } = render(
      <EmergencyStopHistoryCard history={[
        { id: 1, created_at: "2026-05-05T12:00:00+00:00", enabled: true,  note: "first" },
        { id: 2, created_at: "2026-05-05T12:05:00+00:00", enabled: false, note: "second" },
        { id: 3, created_at: "2026-05-05T12:10:00+00:00", enabled: true,  note: "third" },
      ]} />,
    );
    const onCount  = (container.textContent.match(/ON/g)  || []).length;
    const offCount = (container.textContent.match(/OFF/g) || []).length;
    expect(onCount).toBeGreaterThanOrEqual(2);
    expect(offCount).toBeGreaterThanOrEqual(1);
  });

  it("includes the explanatory note about the runtime flag resetting on restart", () => {
    const { container } = render(<EmergencyStopHistoryCard history={[]} />);
    expect(container.textContent).toContain("재시작");
  });
});
