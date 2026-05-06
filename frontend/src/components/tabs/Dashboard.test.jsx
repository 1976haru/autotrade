import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  Activity24hCard,
  BOT_SIGNAL_DISPLAY,
  EmergencyStopStuckBanner,
  MODE_DISPLAY,
  ModeBreakdownRow,
  StatusPin,
  StatusSummaryCard,
  botIdleSignal,
  computeActivity24h,
  emergencyStopOnSince,
  formatModeBreakdown,
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
    expect(pin.textContent).not.toContain("idle");
    expect(pin.textContent).not.toContain("24h 0건");
  });

  // 097: 봇 RUNNING + 24h 주문 0건이면 노란 dot으로 escalate.
  it("bot pin shows yellow idle warning when running but 24h orders is 0 (097)", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={true}
        ordersIn24h={0}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).toContain("RUNNING (24h 0건)");
    expect(pin.style.color).toBe("rgb(251, 191, 36)"); // #fbbf24
  });

  it("bot pin stays green when running and at least one order in 24h (097)", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={true}
        ordersIn24h={1}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).toContain("RUNNING");
    expect(pin.textContent).not.toContain("0건");
    expect(pin.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("bot pin shows STOPPED when not running, regardless of 24h orders (097)", () => {
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={false}
        ordersIn24h={0}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).toContain("STOPPED");
    expect(pin.style.color).toBe("rgb(148, 163, 184)"); // neutral
  });

  it("ordersIn24h defaults to 1 so existing callers don't trigger idle (097 back-compat)", () => {
    // No ordersIn24h prop passed — should not show idle even though running.
    const { getByTestId } = render(
      <StatusSummaryCard
        emergencyStop={false} pendingCount={0} running={true}
        onJumpTab={() => {}}
      />,
    );
    const pin = getByTestId("status-pin-bot");
    expect(pin.textContent).not.toContain("0건");
    expect(pin.style.color).toBe("rgb(34, 197, 94)");
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


describe("botIdleSignal (097)", () => {
  it("returns 'off' when not running, regardless of order count", () => {
    expect(botIdleSignal(false, 0)).toBe("off");
    expect(botIdleSignal(false, 100)).toBe("off");
  });

  it("returns 'idle' when running but 24h orders is 0", () => {
    expect(botIdleSignal(true, 0)).toBe("idle");
  });

  it("returns 'running' when running and at least one order in 24h", () => {
    expect(botIdleSignal(true, 1)).toBe("running");
    expect(botIdleSignal(true, 50)).toBe("running");
  });

  it("treats null/undefined ordersIn24h as 0 (idle)", () => {
    expect(botIdleSignal(true, null)).toBe("idle");
    expect(botIdleSignal(true, undefined)).toBe("idle");
  });
});


describe("BOT_SIGNAL_DISPLAY (097)", () => {
  it("covers the three signal states", () => {
    expect(BOT_SIGNAL_DISPLAY.off).toBeDefined();
    expect(BOT_SIGNAL_DISPLAY.running).toBeDefined();
    expect(BOT_SIGNAL_DISPLAY.idle).toBeDefined();
  });

  it("idle uses amber and the explicit 24h count value", () => {
    expect(BOT_SIGNAL_DISPLAY.idle.color).toBe("#fbbf24");
    expect(BOT_SIGNAL_DISPLAY.idle.value).toContain("24h 0건");
    expect(BOT_SIGNAL_DISPLAY.idle.alarm).toBe(true);
  });

  it("running stays green; off stays neutral non-alarm", () => {
    expect(BOT_SIGNAL_DISPLAY.running.color).toBe("#22c55e");
    expect(BOT_SIGNAL_DISPLAY.running.alarm).toBe(true);
    expect(BOT_SIGNAL_DISPLAY.off.alarm).toBe(false);
    expect(BOT_SIGNAL_DISPLAY.off.value).toBe("STOPPED");
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

  function _att(hoursAgo) {
    return {
      approval_id: 1, symbol: "X", side: "BUY", quantity: 1,
      at: new Date(NOW - hoursAgo * 3600_000).toISOString(),
    };
  }

  it("returns all-zero counts when all sources are empty", () => {
    expect(computeActivity24h([], [], [], NOW)).toEqual({
      orders: 0, approved: 0, rejected: 0, pending: 0,
      byMode: {},
      stops:  0, stopsOn: 0, stopsOff: 0,
      attempts: 0,
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
    const a = computeActivity24h(orders, [], [], NOW);
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
    const a = computeActivity24h([], stops, [], NOW);
    expect(a.stops).toBe(3);
    expect(a.stopsOn).toBe(2);
    expect(a.stopsOff).toBe(1);
  });

  it("counts approve attempts within the window using the `at` field", () => {
    const attempts = [
      _att(1),   // included
      _att(12),  // included
      _att(25),  // excluded
    ];
    const a = computeActivity24h([], [], attempts, NOW);
    expect(a.attempts).toBe(2);
  });

  it("uses Date.now() when now is omitted", () => {
    const a = computeActivity24h([], [], []);
    expect(a).toEqual({
      orders: 0, approved: 0, rejected: 0, pending: 0,
      byMode: {},
      stops:  0, stopsOn: 0, stopsOff: 0,
      attempts: 0,
    });
  });

  it("buckets recent orders by mode (093)", () => {
    const orders = [
      { id: 1, mode: "SIMULATION",           decision: "APPROVED",
        created_at: new Date(NOW - 1 * 3600_000).toISOString() },
      { id: 2, mode: "SIMULATION",           decision: "APPROVED",
        created_at: new Date(NOW - 2 * 3600_000).toISOString() },
      { id: 3, mode: "PAPER",                decision: "APPROVED",
        created_at: new Date(NOW - 3 * 3600_000).toISOString() },
      { id: 4, mode: "LIVE_MANUAL_APPROVAL", decision: "NEEDS_APPROVAL",
        created_at: new Date(NOW - 4 * 3600_000).toISOString() },
      // 25h ago — outside 24h window, should not count
      { id: 5, mode: "SIMULATION",           decision: "APPROVED",
        created_at: new Date(NOW - 25 * 3600_000).toISOString() },
    ];
    const a = computeActivity24h(orders, [], [], NOW);
    expect(a.byMode).toEqual({
      SIMULATION: 2,
      PAPER: 1,
      LIVE_MANUAL_APPROVAL: 1,
    });
  });

  it("skips orders without a mode field defensively (093)", () => {
    const orders = [
      { id: 1, decision: "APPROVED",
        created_at: new Date(NOW - 1 * 3600_000).toISOString() },
      { id: 2, mode: "PAPER", decision: "APPROVED",
        created_at: new Date(NOW - 1 * 3600_000).toISOString() },
    ];
    const a = computeActivity24h(orders, [], [], NOW);
    expect(a.byMode).toEqual({ PAPER: 1 });
    expect(a.orders).toBe(2); // total still counts both
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


describe("formatModeBreakdown (093)", () => {
  it("returns empty array when nothing has count", () => {
    expect(formatModeBreakdown({})).toEqual([]);
  });

  it("orders cells by MODE_DISPLAY (least to most risky)", () => {
    const cells = formatModeBreakdown({
      LIVE_AI_EXECUTION: 1,
      SIMULATION: 5,
      LIVE_MANUAL_APPROVAL: 2,
    });
    expect(cells.map((c) => c.id)).toEqual([
      "SIMULATION",
      "LIVE_MANUAL_APPROVAL",
      "LIVE_AI_EXECUTION",
    ]);
    expect(cells.map((c) => c.count)).toEqual([5, 2, 1]);
  });

  it("omits modes with count <= 0", () => {
    const cells = formatModeBreakdown({ SIMULATION: 0, PAPER: 3 });
    expect(cells.map((c) => c.id)).toEqual(["PAPER"]);
  });

  it("appends unknown mode ids at the end with neutral color", () => {
    const cells = formatModeBreakdown({ SIMULATION: 1, FUTURES_SIMULATION: 2 });
    expect(cells[0].id).toBe("SIMULATION");
    expect(cells[1].id).toBe("FUTURES_SIMULATION");
    expect(cells[1].label).toBe("FUTURES_SIMULATION"); // raw fallback
    expect(cells[1].color).toBe("#475569");
  });

  it("attaches each known mode's display label + color", () => {
    const cells = formatModeBreakdown({ LIVE_MANUAL_APPROVAL: 1 });
    expect(cells[0].label).toBe("MANUAL");
    expect(cells[0].color).toBe("#22c55e");
  });
});


describe("MODE_DISPLAY (093)", () => {
  it("covers all six operating modes from backend modes.py", () => {
    const ids = MODE_DISPLAY.map((m) => m.id);
    expect(ids).toEqual([
      "SIMULATION", "PAPER", "LIVE_SHADOW",
      "LIVE_MANUAL_APPROVAL", "LIVE_AI_ASSIST", "LIVE_AI_EXECUTION",
    ]);
  });
});


describe("<ModeBreakdownRow> (093)", () => {
  afterEach(cleanup);

  it("renders nothing when byMode is empty", () => {
    const { container } = render(<ModeBreakdownRow byMode={{}} />);
    expect(container.querySelector('[data-testid="activity-mode-breakdown"]')).toBeNull();
  });

  it("renders one chip per mode with count", () => {
    const { getByTestId, container } = render(
      <ModeBreakdownRow byMode={{ SIMULATION: 5, PAPER: 2 }} />,
    );
    expect(getByTestId("activity-mode-breakdown")).toBeTruthy();
    expect(getByTestId("activity-mode-cell-SIMULATION").textContent).toContain("SIM 5");
    expect(getByTestId("activity-mode-cell-PAPER").textContent).toContain("PAPER 2");
    // Order: SIMULATION before PAPER (per MODE_DISPLAY)
    const cells = container.querySelectorAll('[data-testid^="activity-mode-cell-"]');
    expect(cells[0].dataset.testid).toBe("activity-mode-cell-SIMULATION");
    expect(cells[1].dataset.testid).toBe("activity-mode-cell-PAPER");
  });
});


describe("<Activity24hCard> mode breakdown (093)", () => {
  beforeEach(() => { _resetAuditHooks(); });
  afterEach(cleanup);

  it("renders the breakdown row when orders span multiple modes", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-05T12:00:00Z"));
    const m = (n) => new Date(Date.now() - n * 60_000).toISOString();
    _resetAuditHooks(
      { items: [
        { id: 1, mode: "SIMULATION",           decision: "APPROVED", created_at: m(10) },
        { id: 2, mode: "SIMULATION",           decision: "APPROVED", created_at: m(20) },
        { id: 3, mode: "PAPER",                decision: "APPROVED", created_at: m(30) },
        { id: 4, mode: "LIVE_MANUAL_APPROVAL", decision: "APPROVED", created_at: m(40) },
      ]},
    );
    const { getByTestId } = render(<Activity24hCard />);
    const row = getByTestId("activity-mode-breakdown");
    expect(row.textContent).toContain("SIM 2");
    expect(row.textContent).toContain("PAPER 1");
    expect(row.textContent).toContain("MANUAL 1");
    vi.useRealTimers();
  });

  it("hides the breakdown row when there are no recent orders", () => {
    const { container } = render(<Activity24hCard />);
    expect(container.querySelector('[data-testid="activity-mode-breakdown"]')).toBeNull();
  });

  it("excludes orders older than 24h from the breakdown", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-05T12:00:00Z"));
    const hAgo = (h) => new Date(Date.now() - h * 3600_000).toISOString();
    _resetAuditHooks(
      { items: [
        { id: 1, mode: "PAPER",      decision: "APPROVED", created_at: hAgo(2) },
        { id: 2, mode: "SIMULATION", decision: "APPROVED", created_at: hAgo(48) }, // outside
      ]},
    );
    const { getByTestId, container } = render(<Activity24hCard />);
    const row = getByTestId("activity-mode-breakdown");
    expect(row.textContent).toContain("PAPER 1");
    // 48h-old SIMULATION row should not contribute a chip
    expect(container.querySelector('[data-testid="activity-mode-cell-SIMULATION"]')).toBeNull();
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


describe("<Activity24hCard> attempts row (079 + 080)", () => {
  beforeEach(() => { _resetAuditHooks(); localStorage.clear(); });
  afterEach(() => { cleanup(); localStorage.clear(); });

  function _approvals(pending = [], history = []) {
    return { pending, history };
  }

  it("hides the attempts row when there are no recent attempts", () => {
    const { queryByTestId } = render(
      <Activity24hCard onJumpTab={() => {}} approvals={_approvals()} />,
    );
    expect(queryByTestId("activity-attempts-row")).toBeNull();
  });

  it("renders the attempts row when approvals contain recent attempts", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-06T12:00:00Z"));
    const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString();
    const approvals = _approvals([
      { id: 1, symbol: "A", side: "BUY", quantity: 1, attempts: [
        { at: minutesAgo(10), reasons: ["x"] },
        { at: minutesAgo(60), reasons: ["y"] },
      ]},
    ]);
    const { getByTestId } = render(
      <Activity24hCard onJumpTab={() => {}} approvals={approvals} />,
    );
    expect(getByTestId("activity-attempts-row").textContent).toContain("2건");
    vi.useRealTimers();
  });

  it("clicking the attempts row sets kind=attempt and jumps to audit", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-06T12:00:00Z"));
    const minutesAgo = (m) => new Date(Date.now() - m * 60_000).toISOString();
    const onJumpTab = vi.fn();
    const approvals = _approvals([
      { id: 1, symbol: "A", side: "BUY", quantity: 1, attempts: [
        { at: minutesAgo(10), reasons: ["x"] },
      ]},
    ]);
    const { getByTestId } = render(
      <Activity24hCard onJumpTab={onJumpTab} approvals={approvals} />,
    );
    fireEvent.click(getByTestId("activity-attempts-row"));
    expect(localStorage.getItem("autotrade.eventKindFilter")).toBe("attempt");
    expect(onJumpTab).toHaveBeenCalledWith("audit");
    vi.useRealTimers();
  });

  it("excludes attempts older than 24h from the count", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-06T12:00:00Z"));
    const hoursAgo = (h) => new Date(Date.now() - h * 3600_000).toISOString();
    const approvals = _approvals([
      { id: 1, symbol: "A", side: "BUY", quantity: 1, attempts: [
        { at: hoursAgo(1), reasons: [] },   // included
        { at: hoursAgo(25), reasons: [] },  // excluded
      ]},
    ]);
    const { getByTestId } = render(
      <Activity24hCard onJumpTab={() => {}} approvals={approvals} />,
    );
    // 2 attempts total, but only 1 within 24h
    expect(getByTestId("activity-attempts-row").textContent).toContain("1건");
    vi.useRealTimers();
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
