import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AI_MODEL_PRICING,
  AiAuditView,
  AiModelBadge,
  AiTokenByModel,
  AiTokenSummary,
  ApprovalAttemptAuditRow,
  BACKTEST_OUTCOME_STORAGE_KEY,
  BACKTEST_SORT_STORAGE_KEY,
  BacktestOutcomeFilterBar,
  BacktestRunsView,
  BacktestSortBar,
  EmergencyStopAuditRow,
  EventTimelineView,
  KindFilterBar,
  ModeBadge,
  OrderAuditRow,
  TimeBucketBar,
  aiAuditEmptyMessage,
  backtestEmptyMessage,
  backtestWinRate,
  classifyBacktestOutcome,
  emptyEventTimelineMessage,
  estimateAiCost,
  flattenApprovalAttempts,
  formatAiTokenByModel,
  formatUsdCost,
  isValidBacktestOutcome,
  isValidBacktestSort,
  mergeEvents,
  modelAccent,
  modelFamily,
  setEventKindFilter,
  sortBacktestRuns,
  summarizeAiTokens,
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
const _aiHook       = { items: [], loading: false, error: "", refresh: vi.fn() };
const _backtestHook = { items: [], loading: false, error: "", refresh: vi.fn() };

vi.mock("../../store/useAuditLogs", () => ({
  useOrderAudits:          () => _orderHook,
  useAiAudits:             () => _aiHook,
  useBacktestRuns:         () => _backtestHook,
  useEmergencyStopAudits:  () => _stopHook,
}));

function _resetAiHook(overrides = {}) {
  Object.assign(_aiHook, {
    items: [], loading: false, error: "", ...overrides,
  });
  _aiHook.refresh = vi.fn();
}

function _resetBacktestHook(overrides = {}) {
  Object.assign(_backtestHook, {
    items: [], loading: false, error: "", ...overrides,
  });
  _backtestHook.refresh = vi.fn();
}


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
    const events = mergeEvents({ orders, stops });
    expect(events.map((e) => `${e.kind}-${e.row.id}`)).toEqual([
      "order-11", "stop-1", "order-10",
    ]);
  });

  it("respects limit (top-N most recent)", () => {
    const orders = Array.from({ length: 60 }, (_, i) =>
      _ORDER({ id: i, created_at: new Date(2026, 4, 5, 12, 0, i).toISOString() }),
    );
    const events = mergeEvents({ orders, limit: 50 });
    expect(events).toHaveLength(50);
    // Most recent = highest second value (id 59)
    expect(events[0].row.id).toBe(59);
  });

  it("with no limit (default), returns every row sorted desc", () => {
    const orders = Array.from({ length: 80 }, (_, i) =>
      _ORDER({ id: i, created_at: new Date(2026, 4, 5, 12, 0, i).toISOString() }),
    );
    const events = mergeEvents({ orders });
    expect(events).toHaveLength(80);
    expect(events[0].row.id).toBe(79);
  });

  it("returns an empty list when no sources are provided", () => {
    expect(mergeEvents()).toEqual([]);
    expect(mergeEvents({})).toEqual([]);
    expect(mergeEvents({ orders: [], stops: [] })).toEqual([]);
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
    const events = mergeEvents({ orders, stops, attempts });
    expect(events.map((e) => e.kind)).toEqual(["attempt", "stop", "order"]);
  });

  it("attempts default to empty list when key is omitted", () => {
    const orders = [{ id: 1, created_at: "2026-05-05T12:00:00+00:00" }];
    const events = mergeEvents({ orders });
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
    expect(getByText("해당 조건의 이벤트 없음")).toBeTruthy();
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


describe("aiAuditEmptyMessage", () => {
  it("returns plain '없음' message when items is empty", () => {
    expect(aiAuditEmptyMessage([], "", "all")).toBe("AI 호출 기록 없음");
    expect(aiAuditEmptyMessage(undefined, "", "all")).toBe("AI 호출 기록 없음");
  });

  it("returns the filter-narrowed variant when ticker filter narrows to zero", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "005930", "all"))
      .toBe("해당 조건의 AI 호출 없음");
  });

  it("returns the filter-narrowed variant when time bucket narrows to zero (091)", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "", "1h"))
      .toBe("해당 조건의 AI 호출 없음");
  });

  it("returns the filter-narrowed variant when both ticker + bucket are active", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "005930", "24h"))
      .toBe("해당 조건의 AI 호출 없음");
  });

  it("falls back to plain message when no filter active", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "", "all")).toBe("AI 호출 기록 없음");
  });

  it("undefined time bucket arg behaves as 'no time filter' (back-compat)", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "", undefined)).toBe("AI 호출 기록 없음");
  });

  it("returns the filter-narrowed variant when model filter narrows to zero (094)", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "", "all", "sonnet"))
      .toBe("해당 조건의 AI 호출 없음");
  });

  it("treats model + ticker + bucket all as multi-axis filters (094)", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "005930", "1h", "opus"))
      .toBe("해당 조건의 AI 호출 없음");
  });

  it("undefined model arg falls back to 'no model filter' (back-compat, 094)", () => {
    expect(aiAuditEmptyMessage([{ id: 1 }], "", "all", undefined))
      .toBe("AI 호출 기록 없음");
  });
});


describe("modelAccent (094)", () => {
  it("returns purple for opus models", () => {
    expect(modelAccent("claude-opus-4-7")).toBe("#c084fc");
    expect(modelAccent("claude-OPUS-4-6")).toBe("#c084fc"); // case-insensitive
  });

  it("returns cyan for sonnet models", () => {
    expect(modelAccent("claude-sonnet-4-6")).toBe("#67e8f9");
    expect(modelAccent("claude-3-7-sonnet")).toBe("#67e8f9");
  });

  it("returns yellow for haiku models", () => {
    expect(modelAccent("claude-haiku-4-5-20251001")).toBe("#fbbf24");
  });

  it("returns neutral gray for unknown / empty / null", () => {
    expect(modelAccent("gpt-4")).toBe("#475569");
    expect(modelAccent("")).toBe("#475569");
    expect(modelAccent(null)).toBe("#475569");
    expect(modelAccent(undefined)).toBe("#475569");
  });
});


describe("<AiModelBadge> (094)", () => {
  afterEach(cleanup);

  it("renders nothing when model is missing", () => {
    const { container } = render(<AiModelBadge model={null} />);
    expect(container.querySelector('[data-testid="ai-model-badge"]')).toBeNull();
    cleanup();
    const { container: c2 } = render(<AiModelBadge model="" />);
    expect(c2.querySelector('[data-testid="ai-model-badge"]')).toBeNull();
  });

  it("renders the model id with the family-mapped color", () => {
    const { getByTestId } = render(<AiModelBadge model="claude-sonnet-4-6" />);
    const badge = getByTestId("ai-model-badge");
    expect(badge.textContent).toBe("claude-sonnet-4-6");
    expect(badge.style.color).toBeTruthy();
  });
});


describe("<AiAuditView> model filter (094)", () => {
  beforeEach(() => { _resetAiHook(); });
  afterEach(cleanup);

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", extra: "", active_strats: [], risk_params: {},
      text: "...", model: "claude-sonnet-4-6", input_tokens: 100, output_tokens: 200,
      score: { total: 75 }, error: null,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the model search input with placeholder hint", () => {
    const { getByPlaceholderText } = render(<AiAuditView />);
    expect(getByPlaceholderText(/모델/)).toBeTruthy();
  });

  it("typing 'sonnet' narrows to sonnet rows only", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6" }),
        _ai({ id: 2, ticker: "BBB", model: "claude-haiku-4-5" }),
        _ai({ id: 3, ticker: "CCC", model: "claude-opus-4-7" }),
      ],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "sonnet" } });
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).not.toContain("BBB");
    expect(container.textContent).not.toContain("CCC");
    expect(container.textContent).toContain("(1)");
  });

  it("matches case-insensitively", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "AAA", model: "claude-OPUS-4-7" })],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "opus" } });
    expect(container.textContent).toContain("AAA");
  });

  it("composes with ticker filter (model × ticker)", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "005930", model: "claude-sonnet-4-6" }),
        _ai({ id: 2, ticker: "000660", model: "claude-sonnet-4-6" }),
        _ai({ id: 3, ticker: "005930", model: "claude-haiku-4-5" }),
      ],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "sonnet" } });
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "005930" } });
    expect(container.textContent).toContain("(1)");
  });

  it("rows missing a model field are filtered out gracefully", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6" }),
        _ai({ id: 2, ticker: "BBB", model: null }),
      ],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "sonnet" } });
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).not.toContain("BBB");
  });

  it("non-matching model filter shows the filter-narrowed empty message", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6" })],
    });
    const { getByText, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "gpt" } });
    expect(getByText("해당 조건의 AI 호출 없음")).toBeTruthy();
  });

  it("renders an AiModelBadge for each row when model is present", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6" }),
        _ai({ id: 2, ticker: "BBB", model: "claude-haiku-4-5" }),
      ],
    });
    const { container } = render(<AiAuditView />);
    const badges = container.querySelectorAll('[data-testid="ai-model-badge"]');
    expect(badges.length).toBe(2);
    const labels = Array.from(badges).map((b) => b.textContent);
    expect(labels).toContain("claude-sonnet-4-6");
    expect(labels).toContain("claude-haiku-4-5");
  });

  it("does not render a badge when row has no model", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "AAA", model: null })],
    });
    const { container } = render(<AiAuditView />);
    expect(container.querySelector('[data-testid="ai-model-badge"]')).toBeNull();
  });
});


describe("summarizeAiTokens (098)", () => {
  it("returns zero shape for empty / nullable items", () => {
    expect(summarizeAiTokens([])).toEqual({ count: 0, inputTotal: 0, outputTotal: 0 });
    expect(summarizeAiTokens(null)).toEqual({ count: 0, inputTotal: 0, outputTotal: 0 });
    expect(summarizeAiTokens(undefined)).toEqual({ count: 0, inputTotal: 0, outputTotal: 0 });
  });

  it("sums input_tokens and output_tokens across items", () => {
    expect(summarizeAiTokens([
      { input_tokens: 1000, output_tokens: 500 },
      { input_tokens: 2000, output_tokens: 800 },
    ])).toEqual({ count: 2, inputTotal: 3000, outputTotal: 1300 });
  });

  it("treats missing token fields as 0 (no NaN propagation)", () => {
    expect(summarizeAiTokens([
      { input_tokens: 100, output_tokens: 50 },
      { input_tokens: undefined, output_tokens: null },
      {},
    ])).toEqual({ count: 3, inputTotal: 100, outputTotal: 50 });
  });
});


describe("<AiTokenSummary> (098)", () => {
  afterEach(cleanup);

  it("renders nothing when items is empty", () => {
    const { container } = render(<AiTokenSummary items={[]} />);
    expect(container.querySelector('[data-testid="ai-token-summary"]')).toBeNull();
  });

  it("renders nothing when items is undefined", () => {
    const { container } = render(<AiTokenSummary items={undefined} />);
    expect(container.querySelector('[data-testid="ai-token-summary"]')).toBeNull();
  });

  it("renders count + in + out with locale-formatted numbers", () => {
    const { getByTestId } = render(<AiTokenSummary items={[
      { input_tokens: 14500, output_tokens: 8200 },
      { input_tokens: 200,   output_tokens: 100 },
    ]} />);
    const footer = getByTestId("ai-token-summary");
    expect(footer.textContent).toContain("총 2회");
    expect(footer.textContent).toContain("in 14,700");
    expect(footer.textContent).toContain("out 8,300");
  });
});


describe("<AiAuditView> token summary integration (098)", () => {
  beforeEach(() => { _resetAiHook(); });
  afterEach(cleanup);

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", model: "claude-sonnet-4-6",
      input_tokens: 1000, output_tokens: 500,
      score: { total: 75 }, error: null,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the footer with totals when rows are present", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, input_tokens: 14500, output_tokens: 8200 }),
        _ai({ id: 2, input_tokens: 1500,  output_tokens: 800 }),
      ],
    });
    const { getByTestId } = render(<AiAuditView />);
    const footer = getByTestId("ai-token-summary");
    expect(footer.textContent).toContain("총 2회");
    expect(footer.textContent).toContain("in 16,000");
    expect(footer.textContent).toContain("out 9,000");
  });

  it("hides the footer when items list is genuinely empty", () => {
    _resetAiHook({ items: [] });
    const { container } = render(<AiAuditView />);
    expect(container.querySelector('[data-testid="ai-token-summary"]')).toBeNull();
  });

  it("recomputes totals as filters narrow the visible items", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6",
              input_tokens: 1000, output_tokens: 200 }),
        _ai({ id: 2, ticker: "BBB", model: "claude-haiku-4-5",
              input_tokens: 5000, output_tokens: 800 }),
      ],
    });
    const { getByTestId, getByPlaceholderText } = render(<AiAuditView />);
    expect(getByTestId("ai-token-summary").textContent).toContain("총 2회");
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "sonnet" } });
    const footer = getByTestId("ai-token-summary");
    expect(footer.textContent).toContain("총 1회");
    expect(footer.textContent).toContain("in 1,000");
    expect(footer.textContent).toContain("out 200");
  });

  it("hides the footer when filters narrow visible items to zero", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "AAA", model: "claude-sonnet-4-6" })],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "gpt-99" } });
    expect(container.querySelector('[data-testid="ai-token-summary"]')).toBeNull();
  });
});


describe("modelFamily (101)", () => {
  it("returns the family id for known prefixes", () => {
    expect(modelFamily("claude-opus-4-7")).toBe("opus");
    expect(modelFamily("claude-sonnet-4-6")).toBe("sonnet");
    expect(modelFamily("claude-haiku-4-5-20251001")).toBe("haiku");
  });

  it("matches case-insensitively", () => {
    expect(modelFamily("Claude-OPUS-4-7")).toBe("opus");
  });

  it("returns null for unknown / empty / null", () => {
    expect(modelFamily("gpt-4")).toBeNull();
    expect(modelFamily("")).toBeNull();
    expect(modelFamily(null)).toBeNull();
    expect(modelFamily(undefined)).toBeNull();
  });
});


describe("AI_MODEL_PRICING (101)", () => {
  it("covers the three Anthropic families with per-1M-token rates", () => {
    expect(AI_MODEL_PRICING.opus.input).toBeGreaterThan(0);
    expect(AI_MODEL_PRICING.opus.output).toBeGreaterThan(AI_MODEL_PRICING.opus.input);
    expect(AI_MODEL_PRICING.sonnet.input).toBeLessThan(AI_MODEL_PRICING.opus.input);
    expect(AI_MODEL_PRICING.haiku.input).toBeLessThan(AI_MODEL_PRICING.sonnet.input);
  });
});


describe("estimateAiCost (101)", () => {
  it("returns zero shape for empty / nullable items", () => {
    expect(estimateAiCost([]))
      .toEqual({ totalUsd: 0, knownCount: 0, unknownCount: 0 });
    expect(estimateAiCost(null))
      .toEqual({ totalUsd: 0, knownCount: 0, unknownCount: 0 });
  });

  it("computes cost for a single sonnet call (1M in, 1M out)", () => {
    // sonnet: $3 in + $15 out = $18 per 1M+1M
    const c = estimateAiCost([
      { model: "claude-sonnet-4-6", input_tokens: 1_000_000, output_tokens: 1_000_000 },
    ]);
    expect(c.totalUsd).toBeCloseTo(18, 6);
    expect(c.knownCount).toBe(1);
    expect(c.unknownCount).toBe(0);
  });

  it("sums across families using each family's rate", () => {
    const c = estimateAiCost([
      // opus 100k in + 50k out = 0.1*15 + 0.05*75 = 1.5 + 3.75 = 5.25
      { model: "claude-opus-4-7", input_tokens: 100_000, output_tokens: 50_000 },
      // haiku 1M in + 1M out = 0.8 + 4 = 4.80
      { model: "claude-haiku-4-5", input_tokens: 1_000_000, output_tokens: 1_000_000 },
    ]);
    expect(c.totalUsd).toBeCloseTo(10.05, 2);
    expect(c.knownCount).toBe(2);
  });

  it("counts unknown-family rows separately, excluded from totalUsd", () => {
    const c = estimateAiCost([
      { model: "claude-sonnet-4-6", input_tokens: 1_000_000, output_tokens: 0 }, // $3
      { model: "gpt-4",             input_tokens: 1_000_000, output_tokens: 0 }, // unknown
      { model: null,                input_tokens: 1_000_000, output_tokens: 0 }, // unknown
    ]);
    expect(c.totalUsd).toBeCloseTo(3, 6);
    expect(c.knownCount).toBe(1);
    expect(c.unknownCount).toBe(2);
  });

  it("treats missing token fields as 0 (no NaN propagation)", () => {
    const c = estimateAiCost([
      { model: "claude-sonnet-4-6" },
      { model: "claude-sonnet-4-6", input_tokens: null, output_tokens: undefined },
    ]);
    expect(c.totalUsd).toBe(0);
    expect(c.knownCount).toBe(2);
  });
});


describe("formatUsdCost (101)", () => {
  it("returns $0.00 for zero or negative", () => {
    expect(formatUsdCost(0)).toBe("$0.00");
    expect(formatUsdCost(-1)).toBe("$0.00");
  });

  it("returns <$0.01 for tiny positive values", () => {
    expect(formatUsdCost(0.001)).toBe("<$0.01");
    expect(formatUsdCost(0.009999)).toBe("<$0.01");
  });

  it("returns 2-decimal $X.XX otherwise", () => {
    expect(formatUsdCost(0.01)).toBe("$0.01");
    expect(formatUsdCost(2.5)).toBe("$2.50");
    expect(formatUsdCost(123.456)).toBe("$123.46");
  });
});


describe("<AiAuditView> cost estimate integration (101)", () => {
  beforeEach(() => { _resetAiHook(); });
  afterEach(cleanup);

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", model: "claude-sonnet-4-6",
      input_tokens: 1000, output_tokens: 500,
      score: { total: 75 }, error: null,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the cost estimate alongside the token totals", () => {
    _resetAiHook({
      items: [_ai({ id: 1, model: "claude-sonnet-4-6",
                     input_tokens: 1_000_000, output_tokens: 1_000_000 })],
    });
    const { getByTestId } = render(<AiAuditView />);
    expect(getByTestId("ai-cost-estimate").textContent).toContain("$18.00");
  });

  it("shows the unknown badge when a row has an unrecognized model", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, model: "claude-sonnet-4-6" }),
        _ai({ id: 2, model: "gpt-4" }),
      ],
    });
    const { getByTestId } = render(<AiAuditView />);
    expect(getByTestId("ai-cost-unknown").textContent).toContain("미상 1건");
  });

  it("hides the unknown badge when all rows have known families", () => {
    _resetAiHook({
      items: [_ai({ id: 1, model: "claude-haiku-4-5" })],
    });
    const { container } = render(<AiAuditView />);
    expect(container.querySelector('[data-testid="ai-cost-unknown"]')).toBeNull();
  });

  it("displays <$0.01 when the running total is sub-cent", () => {
    _resetAiHook({
      items: [_ai({ id: 1, model: "claude-haiku-4-5",
                     input_tokens: 100, output_tokens: 100 })],
    });
    const { getByTestId } = render(<AiAuditView />);
    expect(getByTestId("ai-cost-estimate").textContent).toContain("<$0.01");
  });
});


describe("formatAiTokenByModel (112)", () => {
  it("returns empty array for empty / nullable input", () => {
    expect(formatAiTokenByModel([])).toEqual([]);
    expect(formatAiTokenByModel(null)).toEqual([]);
    expect(formatAiTokenByModel(undefined)).toEqual([]);
  });

  it("groups items by Anthropic family with input/output sums", () => {
    const cells = formatAiTokenByModel([
      { model: "claude-opus-4-7",   input_tokens: 1000, output_tokens: 500 },
      { model: "claude-opus-4-7",   input_tokens: 200,  output_tokens: 100 },
      { model: "claude-sonnet-4-6", input_tokens: 5000, output_tokens: 800 },
      { model: "claude-haiku-4-5",  input_tokens: 100,  output_tokens: 50  },
    ]);
    const opus = cells.find((c) => c.family === "opus");
    expect(opus.count).toBe(2);
    expect(opus.inputTotal).toBe(1200);
    expect(opus.outputTotal).toBe(600);
    expect(cells.find((c) => c.family === "sonnet").count).toBe(1);
    expect(cells.find((c) => c.family === "haiku").count).toBe(1);
  });

  it("orders cells opus → sonnet → haiku → unknown", () => {
    const cells = formatAiTokenByModel([
      { model: "gpt-4",             input_tokens: 1, output_tokens: 1 },
      { model: "claude-haiku-4-5",  input_tokens: 1, output_tokens: 1 },
      { model: "claude-opus-4-7",   input_tokens: 1, output_tokens: 1 },
      { model: "claude-sonnet-4-6", input_tokens: 1, output_tokens: 1 },
    ]);
    expect(cells.map((c) => c.family)).toEqual(["opus", "sonnet", "haiku", "unknown"]);
  });

  it("groups unrecognised models under 'unknown' family with neutral color", () => {
    const cells = formatAiTokenByModel([
      { model: "gpt-4",          input_tokens: 100, output_tokens: 50 },
      { model: null,             input_tokens: 200, output_tokens: 50 },
      { model: "claude-test-x",  input_tokens: 300, output_tokens: 50 },
    ]);
    const unknown = cells.find((c) => c.family === "unknown");
    expect(unknown.count).toBe(3);
    expect(unknown.inputTotal).toBe(600);
    expect(unknown.label).toBe("기타");
    expect(unknown.color).toBe("#475569");
  });

  it("attaches the same family color modelAccent uses", () => {
    const cells = formatAiTokenByModel([
      { model: "claude-opus-4-7", input_tokens: 1, output_tokens: 1 },
      { model: "claude-sonnet-4-6", input_tokens: 1, output_tokens: 1 },
    ]);
    expect(cells.find((c) => c.family === "opus").color).toBe("#c084fc");
    expect(cells.find((c) => c.family === "sonnet").color).toBe("#67e8f9");
  });

  it("treats missing token fields as 0 (no NaN propagation)", () => {
    const cells = formatAiTokenByModel([
      { model: "claude-sonnet-4-6", input_tokens: undefined, output_tokens: null },
      { model: "claude-sonnet-4-6" },
    ]);
    const sonnet = cells.find((c) => c.family === "sonnet");
    expect(sonnet.count).toBe(2);
    expect(sonnet.inputTotal).toBe(0);
    expect(sonnet.outputTotal).toBe(0);
  });
});


describe("<AiTokenByModel> (112)", () => {
  afterEach(cleanup);

  it("renders nothing for empty / nullable input", () => {
    const { container } = render(<AiTokenByModel items={[]} />);
    expect(container.querySelector('[data-testid="ai-token-by-model"]')).toBeNull();
    cleanup();
    const { container: c2 } = render(<AiTokenByModel items={null} />);
    expect(c2.querySelector('[data-testid="ai-token-by-model"]')).toBeNull();
  });

  it("renders one chip per family with count + token totals", () => {
    const items = [
      { model: "claude-sonnet-4-6", input_tokens: 5000, output_tokens: 800 },
      { model: "claude-haiku-4-5",  input_tokens: 200,  output_tokens: 50  },
    ];
    const { getByTestId } = render(<AiTokenByModel items={items} />);
    const sonnet = getByTestId("ai-token-by-model-cell-sonnet");
    expect(sonnet.textContent).toContain("sonnet");
    expect(sonnet.textContent).toContain("1건");
    expect(sonnet.textContent).toContain("in 5,000");
    expect(sonnet.textContent).toContain("out 800");
    const haiku = getByTestId("ai-token-by-model-cell-haiku");
    expect(haiku.textContent).toContain("haiku");
  });

  it("renders one stacked-bar segment per family with non-zero tokens", () => {
    const items = [
      { model: "claude-opus-4-7",   input_tokens: 1000, output_tokens: 500 }, // 1500
      { model: "claude-sonnet-4-6", input_tokens: 500,  output_tokens: 0   }, //  500
    ];
    const { getByTestId, container } = render(<AiTokenByModel items={items} />);
    expect(getByTestId("ai-token-by-model-bar-opus")).toBeTruthy();
    expect(getByTestId("ai-token-by-model-bar-sonnet")).toBeTruthy();
    // The bar is flex-proportional to total tokens
    const opusBar   = getByTestId("ai-token-by-model-bar-opus");
    const sonnetBar = getByTestId("ai-token-by-model-bar-sonnet");
    expect(Number(opusBar.style.flexGrow)).toBe(1500);
    expect(Number(sonnetBar.style.flexGrow)).toBe(500);
  });

  it("omits a stacked-bar segment for a family whose tokens are all 0", () => {
    const items = [
      { model: "claude-opus-4-7",   input_tokens: 1000, output_tokens: 0 },
      { model: "claude-sonnet-4-6", input_tokens: 0,    output_tokens: 0 },
    ];
    const { container, getByTestId } = render(<AiTokenByModel items={items} />);
    // Sonnet appears in the chip row (count=1) but not in the bar (zero tokens).
    expect(getByTestId("ai-token-by-model-cell-sonnet")).toBeTruthy();
    expect(container.querySelector('[data-testid="ai-token-by-model-bar-sonnet"]')).toBeNull();
    expect(getByTestId("ai-token-by-model-bar-opus")).toBeTruthy();
  });

  it("uses '기타' label for the unknown family", () => {
    const items = [{ model: "gpt-4", input_tokens: 100, output_tokens: 50 }];
    const { getByTestId } = render(<AiTokenByModel items={items} />);
    const cell = getByTestId("ai-token-by-model-cell-unknown");
    expect(cell.textContent).toContain("기타");
  });
});


describe("<AiAuditView> per-model token distribution integration (112)", () => {
  beforeEach(() => { _resetAiHook(); });
  afterEach(cleanup);

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", model: "claude-sonnet-4-6",
      input_tokens: 1000, output_tokens: 500,
      score: { total: 75 }, error: null,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the bar block when there are visible items", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, model: "claude-sonnet-4-6", input_tokens: 1000, output_tokens: 100 }),
        _ai({ id: 2, model: "claude-haiku-4-5",  input_tokens: 200,  output_tokens: 50 }),
      ],
    });
    const { getByTestId } = render(<AiAuditView />);
    expect(getByTestId("ai-token-by-model")).toBeTruthy();
    expect(getByTestId("ai-token-by-model-cell-sonnet")).toBeTruthy();
    expect(getByTestId("ai-token-by-model-cell-haiku")).toBeTruthy();
  });

  it("hides the bar block when there are no visible items", () => {
    _resetAiHook({ items: [] });
    const { container } = render(<AiAuditView />);
    expect(container.querySelector('[data-testid="ai-token-by-model"]')).toBeNull();
  });

  it("recomputes families as filters narrow the visible set", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, model: "claude-sonnet-4-6", input_tokens: 1000 }),
        _ai({ id: 2, model: "claude-haiku-4-5",  input_tokens: 200 }),
      ],
    });
    const { getByPlaceholderText, container } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/모델/), { target: { value: "sonnet" } });
    expect(container.querySelector('[data-testid="ai-token-by-model-cell-sonnet"]')).toBeTruthy();
    expect(container.querySelector('[data-testid="ai-token-by-model-cell-haiku"]')).toBeNull();
  });
});


describe("<AiAuditView> ticker filter", () => {
  beforeEach(() => { _resetAiHook(); });
  afterEach(cleanup);

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", extra: "", active_strats: [], risk_params: {},
      text: "...", model: "claude-test", input_tokens: 100, output_tokens: 200,
      score: { total: 75 }, error: null,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the ticker search input with placeholder hint", () => {
    const { getByPlaceholderText } = render(<AiAuditView />);
    expect(getByPlaceholderText(/종목/)).toBeTruthy();
  });

  it("default empty filter shows all rows", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "005930" }), _ai({ id: 2, ticker: "000660" })],
    });
    const { container } = render(<AiAuditView />);
    expect(container.textContent).toContain("005930");
    expect(container.textContent).toContain("000660");
    expect(container.textContent).toContain("(2)");
  });

  it("typing a matching ticker narrows the list (case-insensitive substring)", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "005930" }),
        _ai({ id: 2, ticker: "000660" }),
        _ai({ id: 3, ticker: "AAPL" }),
      ],
    });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "0066" } });
    expect(container.textContent).toContain("000660");
    expect(container.textContent).not.toContain("005930");
    expect(container.textContent).not.toContain("AAPL");
    expect(container.textContent).toContain("(1)");
  });

  it("trims whitespace before matching", () => {
    _resetAiHook({ items: [_ai({ id: 1, ticker: "005930" })] });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "  005930  " } });
    expect(container.textContent).toContain("005930");
    expect(container.textContent).toContain("(1)");
  });

  it("non-matching filter shows the filter-narrowed empty message", () => {
    _resetAiHook({ items: [_ai({ id: 1, ticker: "005930" })] });
    const { getByText, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "999999" } });
    expect(getByText("해당 조건의 AI 호출 없음")).toBeTruthy();
  });

  it("plain '없음' message when items list itself is empty", () => {
    _resetAiHook({ items: [] });
    const { getByText } = render(<AiAuditView />);
    expect(getByText("AI 호출 기록 없음")).toBeTruthy();
  });

  it("rows missing a ticker field are filtered out gracefully", () => {
    _resetAiHook({ items: [_ai({ id: 1, ticker: null }), _ai({ id: 2, ticker: "AAA" })] });
    const { container, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "AAA" } });
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).toContain("(1)");
  });
});


describe("<AiAuditView> time bucket (091)", () => {
  const STORAGE_KEY = "autotrade.aiAuditTimeBucket";
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const minutesAgo = (m) => new Date(NOW - m * 60_000).toISOString();
  const hoursAgo = (h) => new Date(NOW - h * 3600_000).toISOString();

  function _ai(overrides = {}) {
    return {
      id: 1, ticker: "005930", extra: "", active_strats: [], risk_params: {},
      text: "...", model: "claude-test", input_tokens: 100, output_tokens: 200,
      score: { total: 75 }, error: null, created_at: minutesAgo(10),
      ...overrides,
    };
  }

  beforeEach(() => {
    localStorage.clear();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    _resetAiHook();
  });
  afterEach(() => {
    cleanup();
    localStorage.clear();
    vi.useRealTimers();
  });

  it("default '전 기간' shows all rows regardless of created_at", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", created_at: minutesAgo(10) }),
        _ai({ id: 2, ticker: "BBB", created_at: hoursAgo(48) }),
      ],
    });
    const { container } = render(<AiAuditView />);
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).toContain("BBB");
    expect(container.textContent).toContain("(2)");
  });

  it("1시간 chip narrows to recent calls", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "AAA", created_at: minutesAgo(15) }),  // included
        _ai({ id: 2, ticker: "BBB", created_at: hoursAgo(2) }),     // excluded
      ],
    });
    const { container, getByRole } = render(<AiAuditView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).not.toContain("BBB");
    expect(container.textContent).toContain("(1)");
  });

  it("composes with ticker filter (24시간 × ticker substring)", () => {
    _resetAiHook({
      items: [
        _ai({ id: 1, ticker: "005930", created_at: minutesAgo(30) }),
        _ai({ id: 2, ticker: "000660", created_at: minutesAgo(30) }),
        _ai({ id: 3, ticker: "005930", created_at: hoursAgo(48) }),
      ],
    });
    const { container, getByRole, getByPlaceholderText } = render(<AiAuditView />);
    fireEvent.click(getByRole("radio", { name: "24시간" }));
    fireEvent.change(getByPlaceholderText(/종목/), { target: { value: "005930" } });
    expect(container.textContent).toContain("(1)"); // recent + matching ticker
  });

  it("persists selection to localStorage", () => {
    _resetAiHook({ items: [_ai()] });
    const { getByRole, unmount } = render(<AiAuditView />);
    fireEvent.click(getByRole("radio", { name: "7일" }));
    expect(localStorage.getItem(STORAGE_KEY)).toBe("7d");
    unmount();
    _resetAiHook({ items: [_ai()] });
    const { getByRole: g2 } = render(<AiAuditView />);
    expect(g2("radio", { name: "7일" }).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to 전 기간 when stored value is unknown", () => {
    localStorage.setItem(STORAGE_KEY, "garbage");
    _resetAiHook({ items: [_ai()] });
    const { getByRole } = render(<AiAuditView />);
    expect(getByRole("radio", { name: "전 기간" }).getAttribute("aria-checked")).toBe("true");
  });

  it("shows the filter-narrowed empty message when bucket eliminates everything", () => {
    _resetAiHook({
      items: [_ai({ id: 1, ticker: "AAA", created_at: hoursAgo(48) })],
    });
    const { getByText, getByRole } = render(<AiAuditView />);
    fireEvent.click(getByRole("radio", { name: "1시간" }));
    expect(getByText("해당 조건의 AI 호출 없음")).toBeTruthy();
  });
});


describe("backtestEmptyMessage", () => {
  it("returns plain '없음' message when items is empty", () => {
    expect(backtestEmptyMessage([], "")).toBe("백테스트 실행 기록 없음");
    expect(backtestEmptyMessage(undefined, "")).toBe("백테스트 실행 기록 없음");
  });

  it("returns the filter-narrowed variant when strategy filter narrows to zero", () => {
    expect(backtestEmptyMessage([{ id: 1 }], "sma_crossover"))
      .toBe("해당 조건의 백테스트 없음");
  });

  it("falls back to plain message when no filter active", () => {
    expect(backtestEmptyMessage([{ id: 1 }], "")).toBe("백테스트 실행 기록 없음");
  });

  it("treats outcome axis like other filters (099)", () => {
    expect(backtestEmptyMessage([{ id: 1 }], "", "profit"))
      .toBe("해당 조건의 백테스트 없음");
    expect(backtestEmptyMessage([{ id: 1 }], "", "all"))
      .toBe("백테스트 실행 기록 없음");
  });

  it("undefined outcome arg falls back to 'no outcome filter' (back-compat, 099)", () => {
    expect(backtestEmptyMessage([{ id: 1 }], "", undefined))
      .toBe("백테스트 실행 기록 없음");
  });
});


describe("<BacktestRunsView> strategy filter", () => {
  // 099 added a persisted outcome filter; clear localStorage so prior tests
  // can't leak a non-"all" choice into this suite's strategy assertions.
  beforeEach(() => { _resetBacktestHook(); localStorage.clear(); });
  afterEach(() => { cleanup(); localStorage.clear(); });

  function _bt(overrides = {}) {
    return {
      id: 1, strategy: "sma_crossover",
      params: {}, initial_cash: 10_000_000, quantity: 10, bars_processed: 100,
      final_cash: 10_500_000, total_pnl: 500_000,
      win_count: 5, loss_count: 3, max_drawdown: 100_000,
      data_source: "bars", data_symbol: "005930",
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders the strategy search input with placeholder hint", () => {
    const { getByPlaceholderText } = render(<BacktestRunsView />);
    expect(getByPlaceholderText(/전략/)).toBeTruthy();
  });

  it("default empty filter shows all rows", () => {
    _resetBacktestHook({
      items: [_bt({ id: 1, strategy: "sma_crossover" }),
              _bt({ id: 2, strategy: "rsi_revert" })],
    });
    const { container } = render(<BacktestRunsView />);
    expect(container.textContent).toContain("sma_crossover");
    expect(container.textContent).toContain("rsi_revert");
    expect(container.textContent).toContain("(2)");
  });

  it("typing a matching strategy narrows the list", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "sma_crossover" }),
        _bt({ id: 2, strategy: "rsi_revert" }),
        _bt({ id: 3, strategy: "sma_breakout" }),
      ],
    });
    const { container, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "sma" } });
    expect(container.textContent).toContain("sma_crossover");
    expect(container.textContent).toContain("sma_breakout");
    expect(container.textContent).not.toContain("rsi_revert");
    expect(container.textContent).toContain("(2)");
  });

  it("substring match works case-insensitively", () => {
    _resetBacktestHook({
      items: [_bt({ id: 1, strategy: "sma_crossover" })],
    });
    const { container, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "SMA" } });
    expect(container.textContent).toContain("sma_crossover");
  });

  it("trims whitespace before matching", () => {
    _resetBacktestHook({ items: [_bt({ id: 1, strategy: "sma_crossover" })] });
    const { container, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "  sma  " } });
    expect(container.textContent).toContain("(1)");
  });

  it("non-matching filter shows the filter-narrowed empty message", () => {
    _resetBacktestHook({ items: [_bt({ id: 1, strategy: "sma_crossover" })] });
    const { getByText, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "nonexistent" } });
    expect(getByText("해당 조건의 백테스트 없음")).toBeTruthy();
  });

  it("plain '없음' message when items list itself is empty", () => {
    _resetBacktestHook({ items: [] });
    const { getByText } = render(<BacktestRunsView />);
    expect(getByText("백테스트 실행 기록 없음")).toBeTruthy();
  });

  it("rows missing a strategy field are filtered out gracefully", () => {
    _resetBacktestHook({
      items: [_bt({ id: 1, strategy: null }), _bt({ id: 2, strategy: "sma_crossover" })],
    });
    const { container, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "sma" } });
    expect(container.textContent).toContain("sma_crossover");
    expect(container.textContent).toContain("(1)");
  });
});


describe("classifyBacktestOutcome (099)", () => {
  it("returns 'profit' when total_pnl is positive", () => {
    expect(classifyBacktestOutcome({ total_pnl: 100 })).toBe("profit");
    expect(classifyBacktestOutcome({ total_pnl: 1 })).toBe("profit");
  });

  it("returns 'loss' when total_pnl is negative", () => {
    expect(classifyBacktestOutcome({ total_pnl: -100 })).toBe("loss");
    expect(classifyBacktestOutcome({ total_pnl: -1 })).toBe("loss");
  });

  it("returns 'breakeven' when total_pnl is zero", () => {
    expect(classifyBacktestOutcome({ total_pnl: 0 })).toBe("breakeven");
  });

  it("returns 'breakeven' for null/undefined run or missing total_pnl", () => {
    expect(classifyBacktestOutcome(null)).toBe("breakeven");
    expect(classifyBacktestOutcome(undefined)).toBe("breakeven");
    expect(classifyBacktestOutcome({})).toBe("breakeven");
    expect(classifyBacktestOutcome({ total_pnl: null })).toBe("breakeven");
  });
});


describe("isValidBacktestOutcome (099)", () => {
  it("accepts the four canonical ids", () => {
    expect(isValidBacktestOutcome("all")).toBe(true);
    expect(isValidBacktestOutcome("profit")).toBe(true);
    expect(isValidBacktestOutcome("loss")).toBe(true);
    expect(isValidBacktestOutcome("breakeven")).toBe(true);
  });

  it("rejects unknown values", () => {
    expect(isValidBacktestOutcome("garbage")).toBe(false);
    expect(isValidBacktestOutcome("")).toBe(false);
    expect(isValidBacktestOutcome(null)).toBe(false);
  });
});


describe("<BacktestOutcomeFilterBar> (099)", () => {
  afterEach(cleanup);

  it("renders four chips with the expected labels", () => {
    const { getByRole } = render(<BacktestOutcomeFilterBar active="all" onChange={() => {}} />);
    expect(getByRole("radiogroup", { name: "백테스트 결과 필터" })).toBeTruthy();
    expect(getByRole("radio", { name: "전체" })).toBeTruthy();
    expect(getByRole("radio", { name: "수익" })).toBeTruthy();
    expect(getByRole("radio", { name: "손실" })).toBeTruthy();
    expect(getByRole("radio", { name: "브레이크" })).toBeTruthy();
  });

  it("calls onChange with the chip id", () => {
    const onChange = vi.fn();
    const { getByRole } = render(<BacktestOutcomeFilterBar active="all" onChange={onChange} />);
    fireEvent.click(getByRole("radio", { name: "수익" }));
    expect(onChange).toHaveBeenCalledWith("profit");
    fireEvent.click(getByRole("radio", { name: "손실" }));
    expect(onChange).toHaveBeenLastCalledWith("loss");
    fireEvent.click(getByRole("radio", { name: "브레이크" }));
    expect(onChange).toHaveBeenLastCalledWith("breakeven");
  });
});


describe("<BacktestRunsView> outcome filter (099)", () => {
  beforeEach(() => { _resetBacktestHook(); localStorage.clear(); });
  afterEach(() => { cleanup(); localStorage.clear(); });

  function _bt(overrides = {}) {
    return {
      id: 1, strategy: "sma_crossover",
      params: {}, initial_cash: 10_000_000, quantity: 10, bars_processed: 100,
      final_cash: 10_500_000, total_pnl: 500_000,
      win_count: 5, loss_count: 3, max_drawdown: 100_000,
      data_source: "bars", data_symbol: "005930",
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("default '전체' shows profit, loss, breakeven rows together", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "win",   total_pnl:  200_000 }),
        _bt({ id: 2, strategy: "lose",  total_pnl: -300_000 }),
        _bt({ id: 3, strategy: "flat",  total_pnl: 0 }),
      ],
    });
    const { container } = render(<BacktestRunsView />);
    expect(container.textContent).toContain("win");
    expect(container.textContent).toContain("lose");
    expect(container.textContent).toContain("flat");
  });

  it("clicking 수익 narrows to profit-only rows", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "win",  total_pnl:  200_000 }),
        _bt({ id: 2, strategy: "lose", total_pnl: -300_000 }),
      ],
    });
    const { container, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "수익" }));
    expect(container.textContent).toContain("win");
    expect(container.textContent).not.toContain("lose");
  });

  it("clicking 손실 narrows to negative-pnl rows", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "win",  total_pnl:  200_000 }),
        _bt({ id: 2, strategy: "lose", total_pnl: -300_000 }),
        _bt({ id: 3, strategy: "flat", total_pnl: 0 }),
      ],
    });
    const { container, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "손실" }));
    expect(container.textContent).not.toContain("win");
    expect(container.textContent).toContain("lose");
    expect(container.textContent).not.toContain("flat");
  });

  it("clicking 브레이크 narrows to zero-pnl rows", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "win",  total_pnl:  200_000 }),
        _bt({ id: 2, strategy: "flat", total_pnl: 0 }),
      ],
    });
    const { container, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "브레이크" }));
    expect(container.textContent).not.toContain("win");
    expect(container.textContent).toContain("flat");
  });

  it("composes with strategy filter (수익 × strategy substring)", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "sma_crossover", total_pnl:  200_000 }),
        _bt({ id: 2, strategy: "rsi_revert",    total_pnl:  100_000 }),
        _bt({ id: 3, strategy: "sma_breakout",  total_pnl: -50_000 }),
      ],
    });
    const { container, getByRole, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "수익" }));
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "sma" } });
    expect(container.textContent).toContain("sma_crossover");    // sma + 수익
    expect(container.textContent).not.toContain("rsi_revert");   // 수익이지만 sma 아님
    expect(container.textContent).not.toContain("sma_breakout"); // sma지만 손실
  });

  it("shows the filter-narrowed empty message when outcome eliminates everything", () => {
    _resetBacktestHook({
      items: [_bt({ id: 1, strategy: "win", total_pnl: 200_000 })],
    });
    const { getByText, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "손실" }));
    expect(getByText("해당 조건의 백테스트 없음")).toBeTruthy();
  });

  it("persists selection to localStorage", () => {
    _resetBacktestHook({ items: [_bt()] });
    const { getByRole, unmount } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "손실" }));
    expect(localStorage.getItem(BACKTEST_OUTCOME_STORAGE_KEY)).toBe("loss");
    unmount();
    const { getByRole: g2 } = render(<BacktestRunsView />);
    expect(g2("radio", { name: "손실" }).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to 전체 when stored value is unknown", () => {
    localStorage.setItem(BACKTEST_OUTCOME_STORAGE_KEY, "garbage");
    _resetBacktestHook({ items: [_bt()] });
    const { getByRole } = render(<BacktestRunsView />);
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
  });
});


describe("backtestWinRate (104)", () => {
  it("returns wins / (wins + losses)", () => {
    expect(backtestWinRate({ win_count: 6, loss_count: 4 })).toBeCloseTo(0.6, 6);
    expect(backtestWinRate({ win_count: 0, loss_count: 4 })).toBe(0);
    expect(backtestWinRate({ win_count: 5, loss_count: 0 })).toBe(1);
  });

  it("returns 0 when there were no trades (avoid divide-by-zero)", () => {
    expect(backtestWinRate({ win_count: 0, loss_count: 0 })).toBe(0);
    expect(backtestWinRate({})).toBe(0);
  });

  it("treats null/undefined input safely", () => {
    expect(backtestWinRate(null)).toBe(0);
    expect(backtestWinRate(undefined)).toBe(0);
  });
});


describe("sortBacktestRuns (104)", () => {
  function _r(overrides) {
    return {
      id: 1, total_pnl: 0, win_count: 0, loss_count: 0,
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("returns a new array (does not mutate input)", () => {
    const items = [_r({ id: 1 }), _r({ id: 2 })];
    const sorted = sortBacktestRuns(items, "pnl");
    expect(sorted).not.toBe(items);
  });

  it("'recent' sorts by created_at desc", () => {
    const items = [
      _r({ id: 1, created_at: "2026-05-04T00:00:00Z" }),
      _r({ id: 2, created_at: "2026-05-06T00:00:00Z" }),
      _r({ id: 3, created_at: "2026-05-05T00:00:00Z" }),
    ];
    expect(sortBacktestRuns(items, "recent").map((r) => r.id)).toEqual([2, 3, 1]);
  });

  it("'pnl' sorts by total_pnl desc (winners first)", () => {
    const items = [
      _r({ id: 1, total_pnl: -200 }),
      _r({ id: 2, total_pnl: 1000 }),
      _r({ id: 3, total_pnl: 0 }),
      _r({ id: 4, total_pnl: 500 }),
    ];
    expect(sortBacktestRuns(items, "pnl").map((r) => r.id)).toEqual([2, 4, 3, 1]);
  });

  it("'win_rate' sorts by win/(win+loss) desc, ties stable enough", () => {
    const items = [
      _r({ id: 1, win_count: 1, loss_count: 9 }),  // 0.10
      _r({ id: 2, win_count: 7, loss_count: 3 }),  // 0.70
      _r({ id: 3, win_count: 5, loss_count: 5 }),  // 0.50
      _r({ id: 4, win_count: 0, loss_count: 0 }),  // 0    (no trades)
    ];
    expect(sortBacktestRuns(items, "win_rate").map((r) => r.id)).toEqual([2, 3, 1, 4]);
  });

  it("unknown sortKey falls back to recent", () => {
    const items = [
      _r({ id: 1, created_at: "2026-05-04T00:00:00Z" }),
      _r({ id: 2, created_at: "2026-05-06T00:00:00Z" }),
    ];
    expect(sortBacktestRuns(items, "garbage").map((r) => r.id)).toEqual([2, 1]);
  });

  it("returns empty array for nullable input", () => {
    expect(sortBacktestRuns(null, "pnl")).toEqual([]);
    expect(sortBacktestRuns(undefined, "recent")).toEqual([]);
  });
});


describe("isValidBacktestSort (104)", () => {
  it("accepts the three canonical ids", () => {
    expect(isValidBacktestSort("recent")).toBe(true);
    expect(isValidBacktestSort("pnl")).toBe(true);
    expect(isValidBacktestSort("win_rate")).toBe(true);
  });

  it("rejects unknown values", () => {
    expect(isValidBacktestSort("garbage")).toBe(false);
    expect(isValidBacktestSort("")).toBe(false);
    expect(isValidBacktestSort(null)).toBe(false);
  });
});


describe("<BacktestSortBar> (104)", () => {
  afterEach(cleanup);

  it("renders three chips with the expected labels", () => {
    const { getByRole } = render(<BacktestSortBar active="recent" onChange={() => {}} />);
    expect(getByRole("radiogroup", { name: "백테스트 정렬" })).toBeTruthy();
    expect(getByRole("radio", { name: "최근순" })).toBeTruthy();
    expect(getByRole("radio", { name: "수익순" })).toBeTruthy();
    expect(getByRole("radio", { name: "승률순" })).toBeTruthy();
  });

  it("calls onChange with the chip id", () => {
    const onChange = vi.fn();
    const { getByRole } = render(<BacktestSortBar active="recent" onChange={onChange} />);
    fireEvent.click(getByRole("radio", { name: "수익순" }));
    expect(onChange).toHaveBeenCalledWith("pnl");
    fireEvent.click(getByRole("radio", { name: "승률순" }));
    expect(onChange).toHaveBeenLastCalledWith("win_rate");
  });
});


describe("<BacktestRunsView> sort toggle (104)", () => {
  beforeEach(() => { _resetBacktestHook(); localStorage.clear(); });
  afterEach(() => { cleanup(); localStorage.clear(); });

  function _bt(overrides = {}) {
    return {
      id: 1, strategy: "sma_crossover",
      params: {}, initial_cash: 10_000_000, quantity: 10, bars_processed: 100,
      final_cash: 10_500_000, total_pnl: 0,
      win_count: 0, loss_count: 0, max_drawdown: 0,
      data_source: "bars", data_symbol: "005930",
      created_at: "2026-05-06T12:00:00+00:00",
      ...overrides,
    };
  }

  it("default '최근순' renders newest first", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "first",  created_at: "2026-05-04T00:00:00Z" }),
        _bt({ id: 2, strategy: "newest", created_at: "2026-05-06T00:00:00Z" }),
      ],
    });
    const { container } = render(<BacktestRunsView />);
    const text = container.textContent;
    expect(text.indexOf("newest")).toBeLessThan(text.indexOf("first"));
  });

  it("clicking 수익순 reorders by total_pnl desc", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "loser",  total_pnl: -200,
               created_at: "2026-05-06T11:00:00Z" }),
        _bt({ id: 2, strategy: "winner", total_pnl:  500,
               created_at: "2026-05-06T10:00:00Z" }),
      ],
    });
    const { container, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "수익순" }));
    const text = container.textContent;
    expect(text.indexOf("winner")).toBeLessThan(text.indexOf("loser"));
  });

  it("clicking 승률순 reorders by wins/(wins+losses) desc", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "low_wr",  win_count: 1, loss_count: 9,
               created_at: "2026-05-06T11:00:00Z" }),
        _bt({ id: 2, strategy: "high_wr", win_count: 7, loss_count: 3,
               created_at: "2026-05-06T10:00:00Z" }),
      ],
    });
    const { container, getByRole } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "승률순" }));
    const text = container.textContent;
    expect(text.indexOf("high_wr")).toBeLessThan(text.indexOf("low_wr"));
  });

  it("composes with strategy + outcome filters", () => {
    _resetBacktestHook({
      items: [
        _bt({ id: 1, strategy: "sma_a", total_pnl: 100,
               win_count: 1, loss_count: 9 }),  // wr 0.10, profit
        _bt({ id: 2, strategy: "sma_b", total_pnl: 200,
               win_count: 8, loss_count: 2 }),  // wr 0.80, profit
        _bt({ id: 3, strategy: "rsi",   total_pnl: 300,
               win_count: 9, loss_count: 1 }),  // wr 0.90, profit but filtered out
      ],
    });
    const { container, getByRole, getByPlaceholderText } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "수익" }));
    fireEvent.change(getByPlaceholderText(/전략/), { target: { value: "sma" } });
    fireEvent.click(getByRole("radio", { name: "승률순" }));
    const text = container.textContent;
    expect(text).not.toContain("rsi");
    expect(text.indexOf("sma_b")).toBeLessThan(text.indexOf("sma_a"));
  });

  it("persists selection to localStorage", () => {
    _resetBacktestHook({ items: [_bt()] });
    const { getByRole, unmount } = render(<BacktestRunsView />);
    fireEvent.click(getByRole("radio", { name: "수익순" }));
    expect(localStorage.getItem(BACKTEST_SORT_STORAGE_KEY)).toBe("pnl");
    unmount();
    const { getByRole: g2 } = render(<BacktestRunsView />);
    expect(g2("radio", { name: "수익순" }).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to 최근순 when stored value is unknown", () => {
    localStorage.setItem(BACKTEST_SORT_STORAGE_KEY, "garbage");
    _resetBacktestHook({ items: [_bt()] });
    const { getByRole } = render(<BacktestRunsView />);
    expect(getByRole("radio", { name: "최근순" }).getAttribute("aria-checked")).toBe("true");
  });
});


describe("emptyEventTimelineMessage", () => {
  it("returns plain '이벤트 없음' when no filter is active", () => {
    expect(emptyEventTimelineMessage("all", "", "all")).toBe("이벤트 없음");
  });

  it("returns the filtered variant when only kind is active", () => {
    expect(emptyEventTimelineMessage("order", "", "all"))
      .toBe("해당 조건의 이벤트 없음");
  });

  it("returns the filtered variant when only symbol is active", () => {
    expect(emptyEventTimelineMessage("all", "005930", "all"))
      .toBe("해당 조건의 이벤트 없음");
  });

  it("treats whitespace-only symbol as no symbol filter", () => {
    expect(emptyEventTimelineMessage("all", "   ", "all"))
      .toBe("이벤트 없음");
  });

  it("returns the filtered variant when only time bucket is active", () => {
    expect(emptyEventTimelineMessage("all", "", "1h"))
      .toBe("해당 조건의 이벤트 없음");
  });

  it("returns the filtered variant when multiple filters are active", () => {
    expect(emptyEventTimelineMessage("attempt", "005930", "24h"))
      .toBe("해당 조건의 이벤트 없음");
  });

  it("handles undefined inputs (defensive against partial-state callers)", () => {
    expect(emptyEventTimelineMessage()).toBe("이벤트 없음");
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


describe("<ModeBadge> (108)", () => {
  afterEach(cleanup);

  it("renders nothing when mode is missing", () => {
    const { container } = render(<ModeBadge mode={null} />);
    expect(container.querySelector('[data-testid="mode-badge"]')).toBeNull();
    cleanup();
    const { container: c2 } = render(<ModeBadge mode="" />);
    expect(c2.querySelector('[data-testid="mode-badge"]')).toBeNull();
  });

  it("renders the canonical short label for known modes", () => {
    const { getByTestId, rerender } = render(<ModeBadge mode="SIMULATION" />);
    expect(getByTestId("mode-badge").textContent).toBe("SIM");
    expect(getByTestId("mode-badge").dataset.mode).toBe("SIMULATION");
    rerender(<ModeBadge mode="LIVE_AI_ASSIST" />);
    expect(getByTestId("mode-badge").textContent).toBe("AI 보조");
  });

  it("falls back to raw id + neutral color for unknown modes", () => {
    const { getByTestId } = render(<ModeBadge mode="FUTURES_SIMULATION" />);
    const badge = getByTestId("mode-badge");
    expect(badge.textContent).toBe("FUTURES_SIMULATION");
    expect(badge.dataset.mode).toBe("FUTURES_SIMULATION");
  });
});


describe("<OrderAuditRow> mode badge integration (108)", () => {
  afterEach(cleanup);

  function _row(overrides = {}) {
    return {
      id: 1, mode: "SIMULATION", requested_by_ai: false,
      symbol: "005930", side: "BUY", quantity: 1, order_type: "MARKET",
      limit_price: null, latest_price: 75_000,
      decision: "APPROVED", reasons: [],
      executed: true, broker_order_id: "MOCK-1", broker_status: "FILLED",
      filled_quantity: 1, avg_fill_price: 75_000, message: "",
      created_at: "2026-05-05T12:00:00+00:00",
      ...overrides,
    };
  }

  it("renders a ModeBadge in the metadata line", () => {
    const { getByTestId } = render(<OrderAuditRow r={_row({ mode: "PAPER" })} />);
    const badge = getByTestId("mode-badge");
    expect(badge.textContent).toBe("PAPER");
  });

  it("the badge replaces the bare 'mode' text from the previous version", () => {
    // 108 이전의 plain "{r.mode} ·" 출력은 사라지고 badge로만 나타나야 한다.
    const { container } = render(
      <OrderAuditRow r={_row({ mode: "LIVE_MANUAL_APPROVAL" })} />,
    );
    // Badge는 short label "MANUAL"로 — full enum string은 metadata 라인 어디에도
    // 직접 노출되지 않는다.
    expect(container.textContent).toContain("MANUAL");
    expect(container.textContent).not.toContain("LIVE_MANUAL_APPROVAL");
  });
});


describe("<ApprovalAttemptAuditRow> mode badge (108)", () => {
  afterEach(cleanup);

  it("renders a ModeBadge when the attempt carries a mode (after 108 hoist)", () => {
    const r = {
      approval_id: 7, symbol: "005930", side: "BUY", quantity: 1,
      mode: "LIVE_AI_ASSIST",
      at: "2026-05-06T11:50:00+00:00",
      decided_by: "ops1",
      reasons: ["max_order_notional_exceeded"],
    };
    const { getByTestId } = render(<ApprovalAttemptAuditRow r={r} />);
    expect(getByTestId("mode-badge").textContent).toBe("AI 보조");
  });

  it("does not render a badge when mode is missing", () => {
    const r = {
      approval_id: 7, symbol: "005930", side: "BUY", quantity: 1,
      at: "2026-05-06T11:50:00+00:00",
      reasons: [],
    };
    const { container } = render(<ApprovalAttemptAuditRow r={r} />);
    expect(container.querySelector('[data-testid="mode-badge"]')).toBeNull();
  });
});


describe("flattenApprovalAttempts mode hoist (108)", () => {
  it("hoists mode from the parent approval into each attempt entry", () => {
    const pending = [{
      id: 1, symbol: "005930", side: "BUY", quantity: 1,
      mode: "LIVE_MANUAL_APPROVAL",
      attempts: [
        { at: "2026-05-06T11:50:00Z", decided_by: "ops1", reasons: [] },
        { at: "2026-05-06T11:55:00Z", decided_by: "ops1", reasons: ["foo"] },
      ],
    }];
    const flat = flattenApprovalAttempts(pending, []);
    expect(flat).toHaveLength(2);
    expect(flat[0].mode).toBe("LIVE_MANUAL_APPROVAL");
    expect(flat[1].mode).toBe("LIVE_MANUAL_APPROVAL");
  });

  it("history attempts also get their parent's mode", () => {
    const history = [{
      id: 9, symbol: "AAA", side: "BUY", quantity: 1,
      mode: "LIVE_AI_ASSIST",
      attempts: [{ at: "2026-05-06T11:00:00Z", decided_by: "ops1", reasons: [] }],
    }];
    const flat = flattenApprovalAttempts([], history);
    expect(flat).toHaveLength(1);
    expect(flat[0].mode).toBe("LIVE_AI_ASSIST");
  });
});
