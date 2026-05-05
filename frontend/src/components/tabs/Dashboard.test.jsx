import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Activity24hCard,
  EmergencyStopStuckBanner,
  StatusPin,
  StatusSummaryCard,
  computeActivity24h,
  emergencyStopOnSince,
} from "./Dashboard";


// Activity24hCard용 audit hook 모킹 — 네트워크/상태 흐름 자체는
// useAuditLogs.test.js에서 별도로 검증.
const _orderHook = { items: [], loading: false, error: "", refresh: vi.fn() };
const _stopHook  = { items: [], loading: false, error: "", refresh: vi.fn() };

vi.mock("../../store/useAuditLogs", () => ({
  useOrderAudits:         () => _orderHook,
  useAiAudits:            () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useBacktestRuns:        () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useEmergencyStopAudits: () => _stopHook,
}));


function _resetAuditHooks(orderOverrides = {}, stopOverrides = {}) {
  Object.assign(_orderHook, { items: [], loading: false, error: "", ...orderOverrides });
  Object.assign(_stopHook,  { items: [], loading: false, error: "", ...stopOverrides });
}


describe("<StatusPin>", () => {
  afterEach(cleanup);

  it("renders neutral colors when alarm is false", () => {
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="OFF"
        alarm={false} accent="#ef4444"
        onClick={() => {}} testId="pin-x"
      />,
    );
    const pin = getByTestId("pin-x");
    expect(pin.style.color).toBe("rgb(148, 163, 184)"); // #94a3b8
  });

  it("switches to accent color when alarm is true", () => {
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="ACTIVE"
        alarm={true} accent="#ef4444"
        onClick={() => {}} testId="pin-x"
      />,
    );
    expect(getByTestId("pin-x").style.color).toBe("rgb(239, 68, 68)");
  });

  it("invokes onClick when clicked", () => {
    const onClick = vi.fn();
    const { getByTestId } = render(
      <StatusPin
        icon="🛑" label="긴급 정지" value="OFF"
        alarm={false} accent="#ef4444"
        onClick={onClick} testId="pin-x"
      />,
    );
    fireEvent.click(getByTestId("pin-x"));
    expect(onClick).toHaveBeenCalled();
  });
});


describe("<StatusSummaryCard>", () => {
  afterEach(cleanup);

  it("renders all three pins regardless of state", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    expect(getByTestId("status-pin-emergency-stop")).toBeTruthy();
    expect(getByTestId("status-pin-pending-approvals")).toBeTruthy();
    expect(getByTestId("status-pin-bot")).toBeTruthy();
  });

  it("emergency-stop pin shows ACTIVE in red when on, OFF in neutral when off", () => {
    const { rerender, getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={true} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    let pin = getByTestId("status-pin-emergency-stop");
    expect(pin.textContent).toContain("ACTIVE");
    expect(pin.style.color).toBe("rgb(239, 68, 68)");

    rerender(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    pin = getByTestId("status-pin-emergency-stop");
    expect(pin.textContent).toContain("OFF");
    expect(pin.style.color).toBe("rgb(148, 163, 184)");
  });

  it("pending-approvals pin shows count in amber when none are stale", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={3} stalePendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-pending-approvals");
    expect(pin.textContent).toContain("3건");
    expect(pin.textContent).not.toContain("stale");
    expect(pin.style.color).toBe("rgb(245, 158, 11)"); // #f59e0b
  });

  it("pending-approvals pin escalates to red when at least one is stale", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={3} stalePendingCount={1} running={false}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-pending-approvals");
    expect(pin.textContent).toContain("3건");
    expect(pin.textContent).toContain("(1 stale)");
    expect(pin.style.color).toBe("rgb(239, 68, 68)"); // #ef4444
  });

  it("pending-approvals pin shows '없음' in neutral when count is 0", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-pending-approvals");
    expect(pin.textContent).toContain("없음");
    expect(pin.style.color).toBe("rgb(148, 163, 184)");
  });

  it("bot pin shows RUNNING in green when running", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={true}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).toContain("RUNNING");
    expect(pin.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("clicking each pin calls onJumpTab with the correct tab id", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        onJumpTab={onJumpTab}
      />,
    );
    fireEvent.click(getByTestId("status-pin-emergency-stop"));
    expect(onJumpTab).toHaveBeenLastCalledWith("strat");
    fireEvent.click(getByTestId("status-pin-pending-approvals"));
    expect(onJumpTab).toHaveBeenLastCalledWith("approve");
    fireEvent.click(getByTestId("status-pin-bot"));
    expect(onJumpTab).toHaveBeenLastCalledWith("bot");
  });

  it("works without onJumpTab (clicks do not throw)", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
      />,
    );
    // Should not throw
    fireEvent.click(getByTestId("status-pin-bot"));
  });
});


describe("computeActivity24h", () => {
  // Anchor "now" so the cutoff math is predictable. Anything older than
  // 2026-05-04T12:00 should be excluded; anything from then forward should
  // be included.
  const NOW = new Date("2026-05-05T12:00:00Z").getTime();

  function _o(decision, hoursAgo) {
    return {
      id: Math.random(), decision,
      created_at: new Date(NOW - hoursAgo * 3600_000).toISOString(),
    };
  }
  function _s(enabled, hoursAgo) {
    return {
      id: Math.random(), enabled,
      created_at: new Date(NOW - hoursAgo * 3600_000).toISOString(),
    };
  }

  it("returns all-zero counts when both lists are empty", () => {
    expect(computeActivity24h([], [], NOW)).toEqual({
      orders: 0, approved: 0, rejected: 0, pending: 0,
      stops:  0, stopsOn: 0, stopsOff: 0,
    });
  });

  it("counts decisions within the last 24 hours and excludes older rows", () => {
    const orders = [
      _o("APPROVED",        1),  // 1h ago — included
      _o("APPROVED",        12), // 12h ago — included
      _o("REJECTED",        20), // 20h ago — included
      _o("NEEDS_APPROVAL",  23), // 23h ago — included
      _o("APPROVED",        25), // 25h ago — excluded
      _o("REJECTED",        100),
    ];
    const a = computeActivity24h(orders, [], NOW);
    expect(a.orders).toBe(4);
    expect(a.approved).toBe(2);
    expect(a.rejected).toBe(1);
    expect(a.pending).toBe(1);
  });

  it("counts stop toggles within the window and splits ON/OFF", () => {
    const stops = [
      _s(true,  2),  // included
      _s(false, 8),  // included
      _s(true,  18), // included
      _s(false, 30), // excluded
    ];
    const a = computeActivity24h([], stops, NOW);
    expect(a.stops).toBe(3);
    expect(a.stopsOn).toBe(2);
    expect(a.stopsOff).toBe(1);
  });

  it("uses Date.now() when now is omitted", () => {
    // Just verify the call shape — exact value depends on real clock.
    const a = computeActivity24h([], []);
    expect(a).toEqual({
      orders: 0, approved: 0, rejected: 0, pending: 0,
      stops:  0, stopsOn: 0, stopsOff: 0,
    });
  });
});


describe("<Activity24hCard>", () => {
  beforeEach(() => { _resetAuditHooks(); });
  afterEach(cleanup);

  it("renders zero-state copy when there is no recent activity", () => {
    const { container } = render(<Activity24hCard />);
    expect(container.textContent).toContain("최근 24시간");
    expect(container.textContent).toContain("주문");
    expect(container.textContent).toContain("0건");
  });

  it("shows loading state when either source is loading", () => {
    _resetAuditHooks({ loading: true }, {});
    const { getByText } = render(<Activity24hCard />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("surfaces error from either source", () => {
    _resetAuditHooks({}, { error: "stops broke" });
    const { getByText } = render(<Activity24hCard />);
    expect(getByText("stops broke")).toBeTruthy();
  });

  it("renders aggregated counts when data is present", () => {
    // Pin time so our test data falls inside the 24h window deterministically.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-05T12:00:00Z"));
    const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString();
    _resetAuditHooks(
      { items: [
        { id: 1, decision: "APPROVED",       created_at: minutesAgo(10) },
        { id: 2, decision: "APPROVED",       created_at: minutesAgo(20) },
        { id: 3, decision: "REJECTED",       created_at: minutesAgo(30) },
        { id: 4, decision: "NEEDS_APPROVAL", created_at: minutesAgo(60) },
      ]},
      { items: [
        { id: 1, enabled: true,  created_at: minutesAgo(40) },
        { id: 2, enabled: false, created_at: minutesAgo(35) },
      ]},
    );
    const { container } = render(<Activity24hCard />);
    expect(container.textContent).toContain("4건");
    expect(container.textContent).toContain("승인 2");
    expect(container.textContent).toContain("거부 1");
    expect(container.textContent).toContain("대기 1");
    expect(container.textContent).toContain("긴급정지 토글");
    expect(container.textContent).toContain("ON 1");
    expect(container.textContent).toContain("OFF 1");
    vi.useRealTimers();
  });
});


describe("emergencyStopOnSince", () => {
  it("returns null when emergency_stop is off", () => {
    const history = [{ id: 1, enabled: true, created_at: "2026-05-06T11:00:00+00:00" }];
    expect(emergencyStopOnSince(false, history)).toBeNull();
  });

  it("returns null when history is empty (e.g. backend restart with on flag)", () => {
    expect(emergencyStopOnSince(true, [])).toBeNull();
    expect(emergencyStopOnSince(true, undefined)).toBeNull();
    expect(emergencyStopOnSince(true, null)).toBeNull();
  });

  it("returns the latest event's created_at when on and history[0] is ON", () => {
    const history = [
      { id: 3, enabled: true,  created_at: "2026-05-06T11:00:00+00:00" },
      { id: 2, enabled: false, created_at: "2026-05-06T10:30:00+00:00" },
      { id: 1, enabled: true,  created_at: "2026-05-06T10:00:00+00:00" },
    ];
    expect(emergencyStopOnSince(true, history)).toBe("2026-05-06T11:00:00+00:00");
  });

  it("returns null when on flag and latest event is OFF (data inconsistency)", () => {
    const history = [
      { id: 2, enabled: false, created_at: "2026-05-06T11:00:00+00:00" },
      { id: 1, enabled: true,  created_at: "2026-05-06T10:00:00+00:00" },
    ];
    expect(emergencyStopOnSince(true, history)).toBeNull();
  });
});


describe("<EmergencyStopStuckBanner>", () => {
  afterEach(cleanup);

  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const minutesAgo = (m) => new Date(NOW - m * 60_000).toISOString();

  it("renders nothing when since is null", () => {
    const { container } = render(
      <EmergencyStopStuckBanner since={null} now={NOW} />,
    );
    expect(container.querySelector('[data-testid="emergency-stop-stuck-banner"]')).toBeNull();
  });

  it("renders nothing when elapsed is below the 30-minute threshold", () => {
    const { container } = render(
      <EmergencyStopStuckBanner since={minutesAgo(15)} now={NOW} />,
    );
    expect(container.querySelector('[data-testid="emergency-stop-stuck-banner"]')).toBeNull();
  });

  it("renders the banner once elapsed reaches 30 minutes", () => {
    const { getByTestId } = render(
      <EmergencyStopStuckBanner since={minutesAgo(30)} now={NOW} />,
    );
    const banner = getByTestId("emergency-stop-stuck-banner");
    expect(banner.textContent).toContain("긴급 정지");
    expect(banner.textContent).toContain("30분 전");
    expect(banner.textContent).toContain("모든 신규 주문이 차단");
  });

  it("renders relative time at hour granularity for long stuck states", () => {
    const { getByTestId } = render(
      <EmergencyStopStuckBanner since={minutesAgo(180)} now={NOW} />,
    );
    expect(getByTestId("emergency-stop-stuck-banner").textContent).toContain("3시간 전");
  });

  it("clicking the banner fires onClick", () => {
    const onClick = vi.fn();
    const { getByTestId } = render(
      <EmergencyStopStuckBanner since={minutesAgo(45)} now={NOW} onClick={onClick} />,
    );
    fireEvent.click(getByTestId("emergency-stop-stuck-banner"));
    expect(onClick).toHaveBeenCalled();
  });
});


describe("<Activity24hCard> drilldown to AuditLog", () => {
  const STORAGE_KEY = "autotrade.eventKindFilter";

  beforeEach(() => { _resetAuditHooks(); localStorage.clear(); });
  afterEach(() => { cleanup(); localStorage.clear(); });

  it("clicking 주문 row sets order filter and jumps to audit tab", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = render(<Activity24hCard onJumpTab={onJumpTab} />);
    fireEvent.click(getByTestId("activity-orders-row"));
    expect(localStorage.getItem(STORAGE_KEY)).toBe("order");
    expect(onJumpTab).toHaveBeenCalledWith("audit");
  });

  it("clicking 긴급정지 row sets stop filter and jumps to audit tab", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = render(<Activity24hCard onJumpTab={onJumpTab} />);
    fireEvent.click(getByTestId("activity-stops-row"));
    expect(localStorage.getItem(STORAGE_KEY)).toBe("stop");
    expect(onJumpTab).toHaveBeenCalledWith("audit");
  });

  it("clicks are no-ops when onJumpTab is missing (no localStorage write)", () => {
    const { getByTestId } = render(<Activity24hCard />);
    fireEvent.click(getByTestId("activity-orders-row"));
    fireEvent.click(getByTestId("activity-stops-row"));
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("renders rows as buttons (clickable) when onJumpTab is provided", () => {
    const { getByTestId } = render(<Activity24hCard onJumpTab={() => {}} />);
    expect(getByTestId("activity-orders-row").tagName).toBe("BUTTON");
    expect(getByTestId("activity-orders-row").style.cursor).toBe("pointer");
  });
});
