import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  EmergencyStopAuditRow,
  EventTimelineView,
  KindFilterBar,
  OrderAuditRow,
  mergeEvents,
} from "./AuditLog";


// EventTimelineView 통합 테스트는 두 개의 audit 훅을 모킹한다 — 네트워크/상태
// 흐름 자체는 useAuditLogs.test.js에서 별도로 검증.
const _orderHook = { items: [], loading: false, error: "", refresh: vi.fn() };
const _stopHook  = { items: [], loading: false, error: "", refresh: vi.fn() };

vi.mock("../../store/useAuditLogs", () => ({
  useOrderAudits:          () => _orderHook,
  useAiAudits:             () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useBacktestRuns:         () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useEmergencyStopAudits:  () => _stopHook,
}));


function _resetHooks(orderOverrides = {}, stopOverrides = {}) {
  Object.assign(_orderHook, { items: [], loading: false, error: "", ...orderOverrides });
  Object.assign(_stopHook,  { items: [], loading: false, error: "", ...stopOverrides });
}


const _ORDER = (overrides = {}) => ({
  id: 1, mode: "SIMULATION", requested_by_ai: false,
  symbol: "005930", side: "BUY", quantity: 1, order_type: "MARKET",
  limit_price: null, latest_price: 75_000,
  decision: "APPROVED", reasons: [],
  executed: true, broker_order_id: "MOCK-1", broker_status: "FILLED",
  filled_quantity: 1, avg_fill_price: 75_000, message: "",
  created_at: "2026-05-05T12:00:00+00:00",
  ...overrides,
});

const _STOP = (overrides = {}) => ({
  id: 1, enabled: true, decided_by: null, note: null,
  created_at: "2026-05-05T12:05:00+00:00",
  ...overrides,
});


describe("mergeEvents", () => {
  it("merges two sources by created_at descending", () => {
    const orders = [
      _ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" }),
      _ORDER({ id: 11, created_at: "2026-05-05T12:10:00+00:00" }),
    ];
    const stops = [
      _STOP({ id: 1, created_at: "2026-05-05T12:05:00+00:00" }),
    ];
    const events = mergeEvents(orders, stops);
    expect(events.map((e) => `${e.kind}-${e.row.id}`)).toEqual([
      "order-11", "stop-1", "order-10",
    ]);
  });

  it("respects limit (top-N most recent)", () => {
    const orders = Array.from({ length: 60 }, (_, i) =>
      _ORDER({ id: i, created_at: new Date(2026, 4, 5, 12, 0, i).toISOString() }),
    );
    const events = mergeEvents(orders, [], 50);
    expect(events).toHaveLength(50);
    // Most recent = highest second value (id 59)
    expect(events[0].row.id).toBe(59);
  });

  it("returns an empty list when both sources are empty", () => {
    expect(mergeEvents([], [])).toEqual([]);
  });
});


describe("<OrderAuditRow>", () => {
  afterEach(cleanup);

  it("shows the 주문 kind badge so the row is identifiable in a mixed list", () => {
    const { getByText } = render(<OrderAuditRow r={_ORDER()} />);
    expect(getByText("주문")).toBeTruthy();
  });

  it("colors decision green for APPROVED, red for REJECTED, amber for NEEDS_APPROVAL", () => {
    const cases = [
      ["APPROVED",       "rgb(34, 197, 94)"],
      ["REJECTED",       "rgb(239, 68, 68)"],
      ["NEEDS_APPROVAL", "rgb(245, 158, 11)"],
    ];
    for (const [decision, color] of cases) {
      cleanup();
      const { getByText } = render(<OrderAuditRow r={_ORDER({ decision })} />);
      expect(getByText(decision).style.color).toBe(color);
    }
  });

  it("renders broker fill summary when executed", () => {
    const { container } = render(
      <OrderAuditRow r={_ORDER({ executed: true, broker_status: "FILLED",
                                  filled_quantity: 1, avg_fill_price: 75_000 })} />,
    );
    expect(container.textContent).toContain("FILLED 1@75,000");
  });

  it("renders 미체결 when not executed", () => {
    const { container } = render(<OrderAuditRow r={_ORDER({ executed: false })} />);
    expect(container.textContent).toContain("미체결");
  });
});


describe("<EmergencyStopAuditRow>", () => {
  afterEach(cleanup);

  it("shows the 긴급정지 kind badge", () => {
    const { getByText } = render(<EmergencyStopAuditRow r={_STOP()} />);
    expect(getByText("긴급정지")).toBeTruthy();
  });

  it("renders ON badge in red when enabled", () => {
    const { getByText } = render(<EmergencyStopAuditRow r={_STOP({ enabled: true })} />);
    expect(getByText("ON").style.color).toBe("rgb(239, 68, 68)");
  });

  it("renders OFF badge in green when disabled", () => {
    const { getByText } = render(<EmergencyStopAuditRow r={_STOP({ enabled: false })} />);
    expect(getByText("OFF").style.color).toBe("rgb(34, 197, 94)");
  });

  it("renders decided_by + note when present", () => {
    const { container } = render(
      <EmergencyStopAuditRow r={_STOP({ decided_by: "ops1", note: "vol spike" })} />,
    );
    expect(container.textContent).toContain("by ops1");
    expect(container.textContent).toContain("vol spike");
  });
});


describe("<EventTimelineView> integration", () => {
  beforeEach(() => { _resetHooks(); });
  afterEach(cleanup);

  it("renders rows from both sources interleaved by time", () => {
    _resetHooks(
      { items: [
          _ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" }),
          _ORDER({ id: 11, created_at: "2026-05-05T12:10:00+00:00" }),
      ]},
      { items: [_STOP({ id: 7, created_at: "2026-05-05T12:05:00+00:00" })] },
    );
    const { container } = render(<EventTimelineView />);
    // Section label includes the merged count
    expect(container.textContent).toContain("이벤트 타임라인 (3)");
    // 1 stop badge + 2 order badges should appear
    const orderBadges = within(container).getAllByText("주문");
    const stopBadges  = within(container).getAllByText("긴급정지");
    // Both filter chips and row kind badges include those texts; just check
    // at least the row badges are present.
    expect(orderBadges.length).toBeGreaterThanOrEqual(2);
    expect(stopBadges.length).toBeGreaterThanOrEqual(1);
  });

  it("renders empty state when both sources are empty", () => {
    _resetHooks();
    const { getByText } = render(<EventTimelineView />);
    expect(getByText("이벤트 없음")).toBeTruthy();
  });

  it("surfaces an error from either source", () => {
    _resetHooks({ error: "orders broke" }, {});
    const { getByText } = render(<EventTimelineView />);
    expect(getByText("orders broke")).toBeTruthy();
  });

  it("surfaces emergency-stop fetch error too", () => {
    _resetHooks({}, { error: "stops broke" });
    const { getByText } = render(<EventTimelineView />);
    expect(getByText("stops broke")).toBeTruthy();
  });

  it("shows loading state when either source is still loading", () => {
    _resetHooks({}, { loading: true });
    const { getByText } = render(<EventTimelineView />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });
});


describe("<KindFilterBar>", () => {
  afterEach(cleanup);

  it("renders three chips and highlights the active one", () => {
    const { getByRole } = render(
      <KindFilterBar active="all" onChange={() => {}} />,
    );
    expect(getByRole("radiogroup")).toBeTruthy();
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
    expect(getByRole("radio", { name: "주문" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "긴급정지" }).getAttribute("aria-checked")).toBe("false");
  });

  it("calls onChange with the chip's id on click", () => {
    const onChange = vi.fn();
    const { getByRole } = render(
      <KindFilterBar active="all" onChange={onChange} />,
    );
    fireEvent.click(getByRole("radio", { name: "긴급정지" }));
    expect(onChange).toHaveBeenCalledWith("stop");
    fireEvent.click(getByRole("radio", { name: "주문" }));
    expect(onChange).toHaveBeenLastCalledWith("order");
  });
});


describe("<EventTimelineView> kind filter", () => {
  beforeEach(() => {
    _resetHooks(
      { items: [
          _ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" }),
          _ORDER({ id: 11, created_at: "2026-05-05T12:10:00+00:00" }),
      ]},
      { items: [_STOP({ id: 7, created_at: "2026-05-05T12:05:00+00:00" })] },
    );
  });
  afterEach(cleanup);

  it("defaults to 전체 (all kinds visible, count = 3)", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    expect(container.textContent).toContain("이벤트 타임라인 (3)");
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
  });

  it("주문 chip hides emergency-stop rows", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "주문" }));
    expect(container.textContent).toContain("이벤트 타임라인 (2)");
    // Only kind badge in chip bar should match "긴급정지" (the row badge gone)
    const stopBadges = within(container).getAllByText("긴급정지");
    expect(stopBadges).toHaveLength(1);
  });

  it("긴급정지 chip hides order rows", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "긴급정지" }));
    expect(container.textContent).toContain("이벤트 타임라인 (1)");
    const orderBadges = within(container).getAllByText("주문");
    expect(orderBadges).toHaveLength(1); // chip only
  });

  it("filtered empty state explains it's a filter, not actual emptiness", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" })] },
      { items: [] },
    );
    const { getByText, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "긴급정지" }));
    expect(getByText("해당 종류의 이벤트 없음")).toBeTruthy();
  });

  it("switching back to 전체 restores the merged view", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "주문" }));
    expect(container.textContent).toContain("(2)");
    fireEvent.click(getByRole("radio", { name: "전체" }));
    expect(container.textContent).toContain("(3)");
  });
});
