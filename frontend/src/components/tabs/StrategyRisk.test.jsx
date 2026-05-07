import { act, cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RISK_POLICY_FIELDS } from "../../config/riskPolicy";
import {
  BackendPolicyCard,
  EmergencyStopConfirmModal,
  EmergencyStopHistoryCard,
  EmergencyStopHistoryRow,
  EmergencyStopSummaryCard,
} from "./StrategyRisk";


// Defaults from backend/app/risk/risk_manager.py::RiskPolicy() — kept in
// lockstep with the frontend constants table.
// 199: full 22-field surface — was 6.
const _DEFAULT_POLICY = {
  max_order_notional:               1_000_000,
  max_daily_loss:                     200_000,
  max_positions:                            5,
  max_symbol_exposure:              1_500_000,
  enable_live_trading:                  false,
  enable_ai_execution:                  false,
  disable_ai_orders:                    false,
  stale_price_max_age_seconds:             60,
  min_ai_confidence:                        0,
  enforce_ai_reasoning:                  true,
  ai_rate_limit_window_seconds:            60,
  ai_rate_limit_max_count:                  0,
  max_position_size_pct:                  0.0,
  symbol_whitelist:                        [],
  enforce_market_hours:                 false,
  global_rate_limit_window_seconds:        60,
  global_rate_limit_max_count:              0,
  max_total_exposure:                       0,
  max_total_exposure_pct:                 0.0,
  max_symbol_exposure_pct:                0.0,
  auto_stop_consecutive_rejections:         0,
  max_orders_per_day:                       0,
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

  it("renders every policy field when policy is loaded", () => {
    const { getAllByText, getByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY)} />);
    for (const f of RISK_POLICY_FIELDS) {
      // 199: a couple of labels could clash if we ever re-use them, so allow >=1 match.
      expect(getAllByText(f.label).length).toBeGreaterThanOrEqual(1);
      expect(getByText(f.envVar)).toBeTruthy();
    }
  });

  it("shows DEFAULT badge for every field at dataclass defaults", () => {
    const { container } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY)} />);
    const badges = Array.from(container.querySelectorAll("span"))
      .filter((el) => el.textContent === "DEFAULT" || el.textContent === "OVERRIDDEN");
    // 199: 22 fields surfaced → 22 badges.
    expect(badges).toHaveLength(RISK_POLICY_FIELDS.length);
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

  // 199: array fields (symbol_whitelist) need element-wise comparison, not ===.
  it("treats an empty whitelist as DEFAULT (not OVERRIDDEN)", () => {
    const { container } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
      symbol_whitelist: [],
    })} />);
    const overridden = Array.from(container.querySelectorAll("span"))
      .filter((el) => el.textContent === "OVERRIDDEN");
    expect(overridden).toHaveLength(0);
  });

  it("treats a populated whitelist as OVERRIDDEN", () => {
    const { container, getByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
      symbol_whitelist: ["005930", "000660"],
    })} />);
    const overridden = Array.from(container.querySelectorAll("span"))
      .filter((el) => el.textContent === "OVERRIDDEN");
    expect(overridden).toHaveLength(1);
    expect(getByText("005930, 000660")).toBeTruthy();
  });

  it("formats pct fields with 1-decimal % suffix", () => {
    const { getByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
      max_position_size_pct: 0.05,  // 5.0%
    })} />);
    expect(getByText("5.0%")).toBeTruthy();
  });

  it("formats seconds fields with 's' suffix", () => {
    const { getAllByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY)} />);
    // stale_price_max_age_seconds + 2 rate-limit window fields all default to 60.
    expect(getAllByText("60s").length).toBeGreaterThanOrEqual(1);
  });

  it("renders KRW amounts with the won suffix and bool flags as ON/OFF", () => {
    const { getByText, getAllByText } = render(<BackendPolicyCard riskPolicy={_wrap(_DEFAULT_POLICY, {
      enable_live_trading: true,
      enable_ai_execution: false,
    })} />);
    expect(getByText("1,000,000원")).toBeTruthy(); // max_order_notional
    // enable_live_trading=true + enforce_ai_reasoning=true (default) → ≥2 ON.
    expect(getAllByText("ON").length).toBeGreaterThanOrEqual(2);
    // enable_ai_execution=false + disable_ai_orders=false + enforce_market_hours=false → ≥3 OFF.
    expect(getAllByText("OFF").length).toBeGreaterThanOrEqual(3);
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
      decided_by: "ops1", note: "circuit-breaker", reason_code: null,
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
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "note", reason_code: null });
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
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-default", note: "", reason_code: null });
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
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-other", note: "", reason_code: null });
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

  it("auto-focuses decided_by input when defaultDecidedBy is empty", () => {
    const { getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(document.activeElement).toBe(getByPlaceholderText(/ops1/));
  });

  it("auto-focuses note input when defaultDecidedBy is pre-filled", () => {
    const { getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false} defaultDecidedBy="ops-default"
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(document.activeElement).toBe(getByPlaceholderText(/vol spike/));
  });

  it("Esc dispatches onCancel", () => {
    const onCancel = vi.fn();
    render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={() => {}} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Enter dispatches onConfirm with trimmed values", () => {
    const onConfirm = vi.fn();
    const { getByPlaceholderText } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: " ops1 " } });
    fireEvent.change(getByPlaceholderText(/vol spike/), { target: { value: " spike " } });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "spike", reason_code: null });
  });

  it("ignores Esc and Enter while busy", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={true}
        onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  // 153: reason_code dropdown
  it("renders reason_code dropdown with all 9 codes", () => {
    const { getByTestId } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    const select = getByTestId("emergency-stop-reason-select");
    // 9 enum + 1 "미지정" placeholder = 10 options
    expect(select.querySelectorAll("option").length).toBe(10);
    expect(select.value).toBe("");
  });

  it("forwards selected reason_code with payload", () => {
    const onConfirm = vi.fn();
    const { getByText, getByTestId } = render(
      <EmergencyStopConfirmModal
        targetEnabled={true} busy={false}
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    const select = getByTestId("emergency-stop-reason-select");
    fireEvent.change(select, { target: { value: "daily_loss_limit" } });
    fireEvent.click(getByText("확인"));
    expect(onConfirm).toHaveBeenCalledWith({
      decided_by: "", note: "", reason_code: "daily_loss_limit",
    });
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

  // 153: reason_code badge
  it("renders reason_code badge when present", () => {
    const { getByTestId } = render(
      <EmergencyStopHistoryRow event={{
        id: 4, created_at: "2026-05-05T12:00:00+00:00",
        enabled: true, decided_by: "ops", note: "auto", reason_code: "daily_loss_limit",
      }} />,
    );
    const badge = getByTestId("reason-code-badge");
    expect(badge.textContent).toBe("daily_loss_limit");
  });

  it("does not render reason_code badge when null", () => {
    const { queryByTestId } = render(
      <EmergencyStopHistoryRow event={{
        id: 5, created_at: "2026-05-05T12:00:00+00:00",
        enabled: true, decided_by: "ops", note: "x", reason_code: null,
      }} />,
    );
    expect(queryByTestId("reason-code-badge")).toBeNull();
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


describe("<EmergencyStopSummaryCard> (208)", () => {
  afterEach(cleanup);

  const _SUMMARY = {
    currently_active: false,
    active_since:     null,
    total_toggles:    8,
    total_activations: 3,
    by_reason: { data_stale: 2, broker_error: 1 },
  };

  it("renders loading state", () => {
    const { container } = render(<EmergencyStopSummaryCard loading />);
    expect(container.textContent).toContain("로딩");
  });

  it("renders error state", () => {
    // 245 (Light-008): friendly copy. raw error string은 friendlyErrorMessage
    // 통과 — 의미 있는 메시지로 간주돼 그대로 hint 영역에 노출.
    const { container } = render(<EmergencyStopSummaryCard error="boom" />);
    expect(container.textContent).toContain("boom");
  });

  it("renders OFF state with totals + reason rows", () => {
    const { getByTestId } = render(<EmergencyStopSummaryCard summary={_SUMMARY} />);
    expect(getByTestId("es-tile-state").textContent).toContain("OFF");
    expect(getByTestId("es-reason-data_stale").textContent).toContain("2");
    expect(getByTestId("es-reason-broker_error").textContent).toContain("1");
  });

  it("renders ACTIVE banner with active_since when currently_active", () => {
    const { getByTestId } = render(<EmergencyStopSummaryCard summary={{
      ..._SUMMARY,
      currently_active: true,
      active_since:     "2026-05-07T01:23:45",
    }} />);
    expect(getByTestId("es-tile-state").textContent).toContain("ACTIVE");
    expect(getByTestId("es-active-since").textContent).toContain("2026-05-07 01:23:45");
  });

  it("renders empty body when no reasons recorded", () => {
    const { container } = render(<EmergencyStopSummaryCard summary={{
      ..._SUMMARY, by_reason: {}, total_activations: 0,
    }} />);
    expect(container.textContent).toContain("활성 사유 없음");
  });

  it("refresh button fires onRefresh", () => {
    const onRefresh = vi.fn();
    const { getByText } = render(
      <EmergencyStopSummaryCard summary={_SUMMARY} onRefresh={onRefresh} />,
    );
    fireEvent.click(getByText(/새로고침/));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it("orders reasons by count desc", () => {
    const { container } = render(<EmergencyStopSummaryCard summary={{
      ..._SUMMARY,
      by_reason: { data_stale: 2, broker_error: 5, manual_operator: 1 },
    }} />);
    const text = container.textContent;
    expect(text.indexOf("broker_error")).toBeLessThan(text.indexOf("data_stale"));
    expect(text.indexOf("data_stale")).toBeLessThan(text.indexOf("manual_operator"));
  });
});
