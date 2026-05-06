import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApprovalAttemptAuditRow,
  EmergencyStopAuditRow,
  EventTimelineView,
  KindFilterBar,
  OrderAuditRow,
  TimeBucketBar,
  flattenApprovalAttempts,
  mergeEvents,
  setEventKindFilter,
} from "./AuditLog";


// EventTimelineView 통합 테스트는 두 개의 audit 훅을 모킹한다 — 네트워크/상태
// 흐름 자체는 useAuditLogs.test.js에서 별도로 검증.
const _orderHook = {
  items: [], loading: false, loadingMore: false, hasMore: false,
  error: "", refresh: vi.fn(), loadMore: vi.fn(),
};
const _stopHook  = {
  items: [], loading: false, loadingMore: false, hasMore: false,
  error: "", refresh: vi.fn(), loadMore: vi.fn(),
};

vi.mock("../../store/useAuditLogs", () => ({
  useOrderAudits:          () => _orderHook,
  useAiAudits:             () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useBacktestRuns:         () => ({ items: [], loading: false, error: "", refresh: vi.fn() }),
  useEmergencyStopAudits:  () => _stopHook,
}));


function _resetHooks(orderOverrides = {}, stopOverrides = {}) {
  Object.assign(_orderHook, {
    items: [], loading: false, loadingMore: false, hasMore: false,
    error: "", ...orderOverrides,
  });
  Object.assign(_stopHook,  {
    items: [], loading: false, loadingMore: false, hasMore: false,
    error: "", ...stopOverrides,
  });
  _orderHook.loadMore = vi.fn();
  _stopHook.loadMore  = vi.fn();
  _orderHook.refresh  = vi.fn();
  _stopHook.refresh   = vi.fn();
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
    const events = mergeEvents(orders, [], [], 50);
    expect(events).toHaveLength(50);
    // Most recent = highest second value (id 59)
    expect(events[0].row.id).toBe(59);
  });

  it("with no limit (default), returns every row sorted desc", () => {
    const orders = Array.from({ length: 80 }, (_, i) =>
      _ORDER({ id: i, created_at: new Date(2026, 4, 5, 12, 0, i).toISOString() }),
    );
    const events = mergeEvents(orders, []);
    expect(events).toHaveLength(80);
    expect(events[0].row.id).toBe(79);
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


describe("flattenApprovalAttempts", () => {
  it("returns [] for empty inputs", () => {
    expect(flattenApprovalAttempts([], [])).toEqual([]);
    expect(flattenApprovalAttempts(undefined, undefined)).toEqual([]);
  });

  it("hoists symbol/side/quantity/approval_id from each parent into entries", () => {
    const pending = [{
      id: 17, symbol: "005930", side: "BUY", quantity: 5,
      attempts: [
        { at: "2026-05-06T11:00:00+00:00", decided_by: "ops1", reasons: ["x"] },
        { at: "2026-05-06T11:30:00+00:00", decided_by: "ops2", reasons: ["y"] },
      ],
    }];
    const flat = flattenApprovalAttempts(pending, []);
    expect(flat).toHaveLength(2);
    expect(flat[0]).toMatchObject({
      approval_id: 17, symbol: "005930", side: "BUY", quantity: 5,
      decided_by: "ops1",
    });
  });

  it("merges entries from both pending and history sources", () => {
    const pending = [{ id: 1, symbol: "A", side: "BUY", quantity: 1,
                       attempts: [{ at: "t1", reasons: [] }] }];
    const history = [{ id: 2, symbol: "B", side: "SELL", quantity: 2,
                       attempts: [{ at: "t2", reasons: [] }] }];
    expect(flattenApprovalAttempts(pending, history)).toHaveLength(2);
  });

  it("skips approvals with empty/missing attempts", () => {
    const rows = [
      { id: 1, symbol: "A", side: "BUY", quantity: 1, attempts: [] },
      { id: 2, symbol: "B", side: "SELL", quantity: 1 },  // no attempts field
      { id: 3, symbol: "C", side: "BUY", quantity: 1,
        attempts: [{ at: "t", reasons: [] }] },
    ];
    expect(flattenApprovalAttempts(rows, [])).toHaveLength(1);
  });
});


describe("<ApprovalAttemptAuditRow>", () => {
  afterEach(cleanup);

  function _attempt(overrides = {}) {
    return {
      approval_id: 17, symbol: "005930", side: "BUY", quantity: 5,
      at: "2026-05-06T11:00:00+00:00", decided_by: "ops1",
      reasons: ["emergency stop is enabled"],
      ...overrides,
    };
  }

  it("shows the 결재 시도 kind badge so it's identifiable in a mixed list", () => {
    const { getByText } = render(<ApprovalAttemptAuditRow r={_attempt()} />);
    expect(getByText("결재 시도")).toBeTruthy();
  });

  it("renders symbol, side, quantity, approval id, and reasons", () => {
    const { container } = render(<ApprovalAttemptAuditRow r={_attempt()} />);
    expect(container.textContent).toContain("005930");
    expect(container.textContent).toContain("BUY");
    expect(container.textContent).toContain("5주");
    expect(container.textContent).toContain("승인 #17");
    expect(container.textContent).toContain("by ops1");
    expect(container.textContent).toContain("emergency stop is enabled");
  });

  it("renders 거부됨 status badge in red", () => {
    const { getByText } = render(<ApprovalAttemptAuditRow r={_attempt()} />);
    expect(getByText("거부됨").style.color).toBe("rgb(239, 68, 68)");
  });
});


describe("mergeEvents with attempts", () => {
  it("interleaves all three kinds by their respective timestamp fields", () => {
    const orders = [{ id: 1, created_at: "2026-05-05T12:00:00+00:00" }];
    const stops  = [{ id: 1, enabled: true, created_at: "2026-05-05T12:05:00+00:00" }];
    const attempts = [{
      approval_id: 7, symbol: "X", side: "BUY", quantity: 1,
      at: "2026-05-05T12:10:00+00:00",
    }];
    const events = mergeEvents(orders, stops, attempts);
    expect(events.map((e) => e.kind)).toEqual(["attempt", "stop", "order"]);
  });

  it("defaults attempts to empty list (back-compat with pre-079 callers)", () => {
    const orders = [{ id: 1, created_at: "2026-05-05T12:00:00+00:00" }];
    const events = mergeEvents(orders, []);
    expect(events).toHaveLength(1);
    expect(events[0].kind).toBe("order");
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


describe("<EventTimelineView> integrates approvals.attempts", () => {
  beforeEach(() => {
    localStorage.clear();
    _resetHooks();
  });
  afterEach(() => { cleanup(); localStorage.clear(); });

  const _approvals = (pending = [], history = []) => ({ pending, history });

  it("renders attempt rows from pending approvals merged into the timeline", () => {
    const approvals = _approvals(
      [{ id: 17, symbol: "005930", side: "BUY", quantity: 5,
         attempts: [{ at: "2026-05-05T12:00:00+00:00", decided_by: "ops",
                      reasons: ["emergency stop is enabled"] }] }],
      [],
    );
    const { container } = render(<EventTimelineView approvals={approvals} />);
    expect(container.textContent).toContain("이벤트 타임라인 (1)");
    // Attempt-row badge present
    const attemptBadges = within(container).getAllByText("결재 시도");
    // 1 from chip bar + 1 from row badge
    expect(attemptBadges.length).toBeGreaterThanOrEqual(2);
  });

  it("kind=결재 시도 hides orders and stops, shows only attempts", () => {
    _resetHooks(
      { items: [{
          id: 1, mode: "SIMULATION", requested_by_ai: false,
          symbol: "X", side: "BUY", quantity: 1, order_type: "MARKET",
          limit_price: null, latest_price: 1, decision: "APPROVED",
          reasons: [], executed: true, broker_order_id: "M",
          broker_status: "FILLED", filled_quantity: 1, avg_fill_price: 1,
          message: "", created_at: "2026-05-05T12:00:00+00:00",
      }]},
      { items: [{ id: 1, enabled: true, decided_by: null, note: null,
                  created_at: "2026-05-05T12:05:00+00:00" }]},
    );
    const approvals = _approvals(
      [{ id: 17, symbol: "Y", side: "BUY", quantity: 1,
         attempts: [{ at: "2026-05-05T12:10:00+00:00", reasons: [] }] }],
      [],
    );
    const { container, getByRole } = render(<EventTimelineView approvals={approvals} />);
    fireEvent.click(getByRole("radio", { name: "결재 시도" }));
    expect(container.textContent).toContain("이벤트 타임라인 (1)");
  });

  it("symbol filter narrows attempts by ticker too", () => {
    const approvals = _approvals(
      [
        { id: 1, symbol: "AAA", side: "BUY", quantity: 1,
          attempts: [{ at: "2026-05-05T12:00:00+00:00", reasons: [] }] },
        { id: 2, symbol: "BBB", side: "BUY", quantity: 1,
          attempts: [{ at: "2026-05-05T12:05:00+00:00", reasons: [] }] },
      ],
      [],
    );
    const { container, getByPlaceholderText, getByRole } = render(
      <EventTimelineView approvals={approvals} />,
    );
    fireEvent.click(getByRole("radio", { name: "결재 시도" }));
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "BBB" } });
    expect(container.textContent).toContain("이벤트 타임라인 (1)");
  });

  it("works without approvals prop (back-compat default)", () => {
    const { container } = render(<EventTimelineView />);
    expect(container.textContent).toContain("이벤트 타임라인 (0)");
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


describe("<EventTimelineView> symbol filter", () => {
  beforeEach(() => {
    localStorage.clear();
    _resetHooks(
      { items: [
          _ORDER({ id: 10, symbol: "005930", created_at: "2026-05-05T12:00:00+00:00" }),
          _ORDER({ id: 11, symbol: "000660", created_at: "2026-05-05T12:05:00+00:00" }),
          _ORDER({ id: 12, symbol: "005930", created_at: "2026-05-05T12:10:00+00:00" }),
      ]},
      { items: [_STOP({ id: 1, created_at: "2026-05-05T12:07:00+00:00" })] },
    );
  });
  afterEach(() => { cleanup(); localStorage.clear(); });

  it("renders the symbol input with placeholder hint", () => {
    const { getByPlaceholderText } = render(<EventTimelineView />);
    expect(getByPlaceholderText(/종목/)).toBeTruthy();
  });

  it("default empty filter shows all rows (3 orders + 1 stop = 4)", () => {
    const { container } = render(<EventTimelineView />);
    expect(container.textContent).toContain("(4)");
  });

  it("typing a matching symbol narrows orders to that ticker (stop preserved)", () => {
    const { container, getByPlaceholderText } = render(<EventTimelineView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "005930" } });
    // 2 matching orders + 1 stop (stop is mode-wide, not symbol-bound) = 3
    expect(container.textContent).toContain("(3)");
  });

  it("substring match works case-insensitively", () => {
    const { container, getByPlaceholderText } = render(<EventTimelineView />);
    // "0066" should match symbol "000660"
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "0066" } });
    expect(container.textContent).toContain("(2)"); // 1 order + 1 stop
  });

  it("non-matching symbol leaves only stops visible", () => {
    const { container, getByPlaceholderText } = render(<EventTimelineView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "999999" } });
    // 0 orders match + 1 stop preserved
    expect(container.textContent).toContain("(1)");
  });

  it("symbol filter combined with kind=주문 hides stops as before", () => {
    const { container, getByPlaceholderText, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "주문" }));
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "005930" } });
    // 2 matching orders, 0 stops
    expect(container.textContent).toContain("(2)");
  });

  it("symbol filter is ignored when kind=긴급정지 (no symbol on stops)", () => {
    const { container, getByPlaceholderText, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "긴급정지" }));
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "005930" } });
    // 0 orders, 1 stop (not affected by symbol filter)
    expect(container.textContent).toContain("(1)");
  });

  it("trims whitespace before matching (operator paste habits)", () => {
    const { container, getByPlaceholderText } = render(<EventTimelineView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "  005930  " } });
    expect(container.textContent).toContain("(3)");
  });
});


describe("<EventTimelineView> load-more", () => {
  beforeEach(() => {
    // Direct describes write to localStorage; clear so leakage doesn't make
    // EventTimelineView mount with kind="stop" and skip the orders we set up.
    localStorage.clear();
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" })],
        hasMore: true },
      { items: [], hasMore: false },
    );
  });
  afterEach(() => { cleanup(); localStorage.clear(); });

  it("renders 더 보기 button when at least one source has more", () => {
    const { getByText } = render(<EventTimelineView />);
    expect(getByText(/더 보기/)).toBeTruthy();
  });

  it("clicking 더 보기 calls loadMore on sources that still have more", () => {
    const { getByText } = render(<EventTimelineView />);
    fireEvent.click(getByText(/더 보기/));
    expect(_orderHook.loadMore).toHaveBeenCalled();
    expect(_stopHook.loadMore).not.toHaveBeenCalled();
  });

  it("hides 더 보기 and shows end marker when both sources are exhausted", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" })],
        hasMore: false },
      { items: [], hasMore: false },
    );
    const { getByText, queryByText } = render(<EventTimelineView />);
    expect(queryByText(/더 보기/)).toBeNull();
    expect(getByText(/모든 이벤트를 불러왔습니다/)).toBeTruthy();
  });

  it("kind=stop only requests more from stops, even if orders still have more", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" })],
        hasMore: true },
      { items: [_STOP({ id: 1, created_at: "2026-05-05T12:05:00+00:00" })],
        hasMore: true },
    );
    const { getByRole, getByText } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "긴급정지" }));
    fireEvent.click(getByText(/더 보기/));
    expect(_orderHook.loadMore).not.toHaveBeenCalled();
    expect(_stopHook.loadMore).toHaveBeenCalled();
  });

  it("button shows loading text while a load is in flight", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" })],
        hasMore: true, loadingMore: true },
      { items: [], hasMore: false },
    );
    const { getByText } = render(<EventTimelineView />);
    expect(getByText(/불러오는 중/)).toBeTruthy();
  });
});


describe("<EventTimelineView> kind filter persistence", () => {
  const STORAGE_KEY = "autotrade.eventKindFilter";

  beforeEach(() => {
    localStorage.clear();
    _resetHooks(
      { items: [
          _ORDER({ id: 10, created_at: "2026-05-05T12:00:00+00:00" }),
          _ORDER({ id: 11, created_at: "2026-05-05T12:10:00+00:00" }),
      ]},
      { items: [_STOP({ id: 7, created_at: "2026-05-05T12:05:00+00:00" })] },
    );
  });
  afterEach(() => { cleanup(); localStorage.clear(); });

  it("defaults to 전체 when localStorage is empty", () => {
    const { getByRole } = render(<EventTimelineView />);
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
  });

  it("hydrates the chip from localStorage on mount", () => {
    localStorage.setItem(STORAGE_KEY, "stop");
    const { container, getByRole } = render(<EventTimelineView />);
    expect(getByRole("radio", { name: "긴급정지" }).getAttribute("aria-checked")).toBe("true");
    // Filter is actually applied to the rendered list, not just the chip
    expect(container.textContent).toContain("이벤트 타임라인 (1)");
  });

  it("persists the new selection on click", () => {
    const { getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "주문" }));
    expect(localStorage.getItem(STORAGE_KEY)).toBe("order");
  });

  it("falls back to 전체 when the stored value is unknown (forward-compat)", () => {
    localStorage.setItem(STORAGE_KEY, "garbage-from-future-build");
    const { getByRole } = render(<EventTimelineView />);
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
  });
});


describe("<TimeBucketBar>", () => {
  afterEach(cleanup);

  it("renders four chips and highlights the active one", () => {
    const { getByRole } = render(<TimeBucketBar active="all" onChange={() => {}} />);
    expect(getByRole("radiogroup", { name: "시간 범위 필터" })).toBeTruthy();
    expect(getByRole("radio", { name: "전 기간" }).getAttribute("aria-checked")).toBe("true");
    expect(getByRole("radio", { name: "1시간" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "24시간" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "7일" }).getAttribute("aria-checked")).toBe("false");
  });

  it("calls onChange with the chip id", () => {
    const onChange = vi.fn();
    const { getByRole } = render(<TimeBucketBar active="all" onChange={onChange} />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    expect(onChange).toHaveBeenCalledWith("1h");
    fireEvent.click(getByRole("radio", { name: "24시간" }));
    expect(onChange).toHaveBeenLastCalledWith("24h");
    fireEvent.click(getByRole("radio", { name: "7일" }));
    expect(onChange).toHaveBeenLastCalledWith("7d");
  });
});


describe("<EventTimelineView> time-bucket filter", () => {
  const STORAGE_KEY = "autotrade.eventTimeBucket";
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const minutesAgo = (m) => new Date(NOW - m * 60_000).toISOString();

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    _resetHooks(
      { items: [
          _ORDER({ id: 10, created_at: minutesAgo(30) }),       // 30 min ago
          _ORDER({ id: 11, created_at: minutesAgo(120) }),      // 2 h ago
          _ORDER({ id: 12, created_at: minutesAgo(60 * 26) }),  // 26 h ago
          _ORDER({ id: 13, created_at: minutesAgo(60 * 24 * 8) }), // 8 d ago
      ]},
      { items: [] },
    );
  });
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.useRealTimers();
  });

  it("default is 전 기간 — every row visible", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    expect(getByRole("radio", { name: "전 기간" }).getAttribute("aria-checked")).toBe("true");
    expect(container.textContent).toContain("(4)");
  });

  it("1시간 chip narrows to events from the last hour", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    // Only the 30-min-ago row falls inside
    expect(container.textContent).toContain("(1)");
  });

  it("24시간 chip includes intra-day rows but excludes older", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "24시간" }));
    // 30 min, 2 h fit; 26 h and 8 d do not
    expect(container.textContent).toContain("(2)");
  });

  it("7일 chip includes 26h ago but excludes 8d ago", () => {
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "7일" }));
    expect(container.textContent).toContain("(3)");
  });

  it("persists selection to localStorage and hydrates on remount", () => {
    const { getByRole, unmount } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1h");
    unmount();
    const { getByRole: g2 } = render(<EventTimelineView />);
    expect(g2("radio", { name: "1시간" }).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to 전 기간 when stored value is unknown", () => {
    localStorage.setItem(STORAGE_KEY, "garbage");
    const { getByRole } = render(<EventTimelineView />);
    expect(getByRole("radio", { name: "전 기간" }).getAttribute("aria-checked")).toBe("true");
  });

  it("bucket filter applies to stops too (universal time scope)", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: minutesAgo(30) })] },
      { items: [
          _STOP({ id: 1, created_at: minutesAgo(30) }),       // recent
          _STOP({ id: 2, created_at: minutesAgo(60 * 48) }),  // 2 d ago
      ]},
    );
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    // 1 order + 1 stop in window
    expect(container.textContent).toContain("(2)");
    fireEvent.click(getByRole("radio", { name: "24시간" }));
    // 24h includes the 2-day-old stop? No: 48h > 24h cutoff.
    expect(container.textContent).toContain("(2)");
    fireEvent.click(getByRole("radio", { name: "7일" }));
    // 7d covers 2-day stop
    expect(container.textContent).toContain("(3)");
  });

  it("composes with kind filter (1h × 주문)", () => {
    _resetHooks(
      { items: [_ORDER({ id: 10, created_at: minutesAgo(30) })] },
      { items: [_STOP({ id: 1, created_at: minutesAgo(30) })] },
    );
    const { container, getByRole } = render(<EventTimelineView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    expect(container.textContent).toContain("(2)");
    fireEvent.click(getByRole("radio", { name: "주문" }));
    expect(container.textContent).toContain("(1)");
  });
});


describe("setEventKindFilter helper (cross-tab navigation entry point)", () => {
  const STORAGE_KEY = "autotrade.eventKindFilter";

  beforeEach(() => { localStorage.clear(); });
  afterEach(() => { localStorage.clear(); });

  it("writes a valid kind to localStorage", () => {
    setEventKindFilter("order");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("order");
    setEventKindFilter("stop");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("stop");
    setEventKindFilter("all");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("all");
  });

  it("ignores invalid kinds (caller bug should not corrupt user setting)", () => {
    setEventKindFilter("order");
    setEventKindFilter("garbage");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("order");
  });
});
