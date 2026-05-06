import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApprovalDecisionModal,
  ApproveAttemptFailureBadge,
  Approvals,
  BulkCancelStaleModal,
  HISTORY_STATUS_STORAGE_KEY,
  HistoryRow,
  HistoryStatusFilterBar,
  PendingAgeBadge,
  ReasonsLine,
  formatPendingAge,
  historyEmptyMessage,
  isPendingStale,
  isValidHistoryStatus,
} from "./Approvals";


// useApprovals는 App에서 lift돼 prop으로 들어오므로 모듈 모킹 없이 prop으로
// 직접 주입한다 — 컴포넌트가 어떤 인자로 hook 함수를 호출하는지만 검증.
function _makeApprovals(overrides = {}) {
  return {
    pending: [],
    history: [],
    loading: false,
    error: "",
    busy: false,
    approve: vi.fn(),
    reject:  vi.fn(),
    cancel:  vi.fn(),
    cancelMany: vi.fn(),
    refresh: vi.fn(),
    refreshHistory: vi.fn(),
    ...overrides,
  };
}


const _PENDING = {
  id: 17, symbol: "005930", side: "BUY", quantity: 5,
  order_type: "MARKET", limit_price: null,
  mode: "LIVE_MANUAL_APPROVAL",
  created_at: "2026-05-05T11:55:00+00:00",
};


function _row(overrides = {}) {
  return {
    id: 42,
    symbol: "005930",
    side: "BUY",
    quantity: 10,
    order_type: "MARKET",
    limit_price: null,
    status: "APPROVED",
    mode: "LIVE_MANUAL_APPROVAL",
    decided_at: "2026-05-05T12:00:00+00:00",
    decided_by: "user",
    note: "ok",
    created_at: "2026-05-05T11:55:00+00:00",
    audit_id: 1,
    ...overrides,
  };
}


describe("<HistoryRow>", () => {
  afterEach(cleanup);

  it("renders the status badge with green color for APPROVED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "APPROVED" })} />);
    const badge = getByText("APPROVED");
    expect(badge.style.color).toBe("rgb(34, 197, 94)"); // #22c55e
  });

  it("renders the status badge with red color for REJECTED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "REJECTED" })} />);
    expect(getByText("REJECTED").style.color).toBe("rgb(239, 68, 68)"); // #ef4444
  });

  it("renders the status badge with gray color for CANCELLED", () => {
    const { getByText } = render(<HistoryRow a={_row({ status: "CANCELLED" })} />);
    expect(getByText("CANCELLED").style.color).toBe("rgb(148, 163, 184)"); // #94a3b8
  });

  it("includes decided_by, mode, and note in the secondary line", () => {
    const { container } = render(
      <HistoryRow a={_row({ note: "stale signal", decided_by: "trader1" })} />,
    );
    expect(container.textContent).toContain("LIVE_MANUAL_APPROVAL");
    expect(container.textContent).toContain("by trader1");
    expect(container.textContent).toContain("stale signal");
  });

  it("handles missing note and decided_by gracefully", () => {
    const { container } = render(
      <HistoryRow a={_row({ note: null, decided_by: null })} />,
    );
    // Just verify it renders without crashing
    expect(container.textContent).toContain("#42");
  });

  it("renders limit_price when present", () => {
    const { container } = render(
      <HistoryRow a={_row({ order_type: "LIMIT", limit_price: 75_000 })} />,
    );
    expect(container.textContent).toContain("LIMIT");
    expect(container.textContent).toContain("75,000원");
  });

  it("appends a relative-time hint next to decided_at", () => {
    // Pin Date.now so the offset (created relative to current real time) is
    // predictable inside the component's formatPendingAge call.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-05-06T12:05:00Z"));
    const { container } = render(
      <HistoryRow a={_row({ decided_at: "2026-05-06T12:00:00+00:00" })} />,
    );
    expect(container.textContent).toContain("(5분 전)");
    vi.useRealTimers();
  });

  it("does not crash and shows '—' when decided_at is null (no relative hint)", () => {
    const { container } = render(
      <HistoryRow a={_row({ decided_at: null, decided_by: null, note: null })} />,
    );
    expect(container.textContent).toContain("—");
    expect(container.textContent).not.toContain("분 전");
  });

  it("renders reasons line when row has reasons", () => {
    const { container } = render(
      <HistoryRow a={_row({ reasons: ["max_order_notional 초과", "manual approval required"] })} />,
    );
    expect(container.textContent).toContain("사유:");
    expect(container.textContent).toContain("max_order_notional 초과");
    expect(container.textContent).toContain("manual approval required");
  });

  it("omits reasons line when reasons is empty or missing", () => {
    const { container: c1 } = render(<HistoryRow a={_row({ reasons: [] })} />);
    expect(c1.textContent).not.toContain("사유:");
    cleanup();
    const { container: c2 } = render(<HistoryRow a={_row()} />);
    expect(c2.textContent).not.toContain("사유:");
  });

  it("shows '⚠ N회 시도' when attempts has entries (post-076 history rows)", () => {
    const { getByTestId } = render(
      <HistoryRow a={_row({ attempts: [
        { at: "2026-05-06T11:00:00+00:00", reasons: ["x"] },
        { at: "2026-05-06T11:30:00+00:00", reasons: ["y"] },
        { at: "2026-05-06T11:55:00+00:00", reasons: ["z"] },
      ]})} />,
    );
    const summary = getByTestId("history-attempts-summary");
    expect(summary.textContent).toContain("3회 시도");
    expect(summary.style.color).toBe("rgb(251, 191, 36)"); // #fbbf24
  });

  it("omits the attempts summary when attempts is empty or missing", () => {
    const { queryByTestId, rerender } = render(
      <HistoryRow a={_row({ attempts: [] })} />,
    );
    expect(queryByTestId("history-attempts-summary")).toBeNull();
    rerender(<HistoryRow a={_row()} />);
    expect(queryByTestId("history-attempts-summary")).toBeNull();
  });
});


describe("<ReasonsLine>", () => {
  afterEach(cleanup);

  it("renders nothing when reasons is undefined or empty", () => {
    const { container: c1 } = render(<ReasonsLine />);
    expect(c1.textContent).toBe("");
    cleanup();
    const { container: c2 } = render(<ReasonsLine reasons={[]} />);
    expect(c2.textContent).toBe("");
  });

  it("joins multiple reasons with ' / ' for compact display", () => {
    const { container } = render(
      <ReasonsLine reasons={["a", "b", "c"]} />,
    );
    expect(container.textContent).toContain("사유:");
    expect(container.textContent).toContain("a / b / c");
  });
});


describe("formatPendingAge", () => {
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const ago = (ms) => new Date(NOW - ms).toISOString();

  it("returns '방금' for ages under 30 seconds", () => {
    expect(formatPendingAge(ago(0), NOW)).toBe("방금");
    expect(formatPendingAge(ago(15_000), NOW)).toBe("방금");
  });

  it("returns minutes when between 30s and 1h", () => {
    expect(formatPendingAge(ago(60_000), NOW)).toBe("1분 전");
    expect(formatPendingAge(ago(5 * 60_000), NOW)).toBe("5분 전");
    expect(formatPendingAge(ago(59 * 60_000), NOW)).toBe("59분 전");
  });

  it("returns hours when between 1h and 24h", () => {
    expect(formatPendingAge(ago(60 * 60_000), NOW)).toBe("1시간 전");
    expect(formatPendingAge(ago(5 * 60 * 60_000), NOW)).toBe("5시간 전");
  });

  it("returns days when 24h or more", () => {
    expect(formatPendingAge(ago(24 * 60 * 60_000), NOW)).toBe("1일 전");
    expect(formatPendingAge(ago(3 * 24 * 60 * 60_000), NOW)).toBe("3일 전");
  });

  it("clamps negative deltas (clock skew) to '방금' rather than producing negative ages", () => {
    const future = new Date(NOW + 60_000).toISOString();
    expect(formatPendingAge(future, NOW)).toBe("방금");
  });
});


describe("isPendingStale", () => {
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const ago = (ms) => new Date(NOW - ms).toISOString();

  it("returns false under 10 minutes", () => {
    expect(isPendingStale(ago(0), NOW)).toBe(false);
    expect(isPendingStale(ago(9 * 60_000), NOW)).toBe(false);
  });

  it("returns true at or over 10 minutes", () => {
    expect(isPendingStale(ago(10 * 60_000), NOW)).toBe(true);
    expect(isPendingStale(ago(60 * 60_000), NOW)).toBe(true);
  });
});


describe("<PendingAgeBadge>", () => {
  afterEach(cleanup);
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const ago = (ms) => new Date(NOW - ms).toISOString();

  it("renders fresh badge in neutral color when age < 10min", () => {
    const { getByTestId } = render(
      <PendingAgeBadge createdAt={ago(2 * 60_000)} now={NOW} />,
    );
    const badge = getByTestId("pending-age-badge");
    expect(badge.getAttribute("data-stale")).toBe("false");
    expect(badge.textContent).toBe("2분 전");
    expect(badge.style.color).toBe("rgb(100, 116, 139)"); // #64748b
  });

  it("renders stale badge in amber with warning icon when age >= 10min", () => {
    const { getByTestId } = render(
      <PendingAgeBadge createdAt={ago(15 * 60_000)} now={NOW} />,
    );
    const badge = getByTestId("pending-age-badge");
    expect(badge.getAttribute("data-stale")).toBe("true");
    expect(badge.textContent).toContain("⚠");
    expect(badge.textContent).toContain("15분 전");
    expect(badge.style.color).toBe("rgb(245, 158, 11)"); // #f59e0b
  });
});


describe("<Approvals> PENDING age badge", () => {
  afterEach(cleanup);

  it("shows the PendingAgeBadge alongside #ID on each PENDING row", () => {
    const approvals = _makeApprovals({ pending: [_PENDING] });
    const { getByTestId } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(getByTestId("pending-age-badge")).toBeTruthy();
  });
});


describe("<BulkCancelStaleModal>", () => {
  afterEach(cleanup);

  // 11분 전 — 058 stale 임계값(10분) 초과
  const STALE_NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const STALE_CREATED = new Date(STALE_NOW - 11 * 60_000).toISOString();

  function _staleApproval(overrides = {}) {
    return {
      id: 1, symbol: "005930", side: "BUY", quantity: 5,
      order_type: "MARKET", limit_price: null,
      mode: "LIVE_MANUAL_APPROVAL",
      created_at: STALE_CREATED,
      ...overrides,
    };
  }

  it("titles the dialog with the count of stale approvals", () => {
    const { getByRole, container } = render(
      <BulkCancelStaleModal
        approvals={[_staleApproval(), _staleApproval({ id: 2 }), _staleApproval({ id: 3 })]}
        busy={false} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("stale 일괄 취소");
    expect(container.textContent).toContain("stale 일괄 취소 (3건)");
    expect(container.textContent).toContain("3건 취소");
  });

  it("lists up to 5 rows and collapses the rest into 외 N건", () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      _staleApproval({ id: i + 1, symbol: `STK${i}` }),
    );
    const { container } = render(
      <BulkCancelStaleModal approvals={many} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container.textContent).toContain("STK0");
    expect(container.textContent).toContain("STK4");
    expect(container.textContent).not.toContain("STK5");
    expect(container.textContent).toContain("외 3건");
  });

  it("pre-fills decided_by from defaultDecidedBy and forwards trimmed values on confirm", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <BulkCancelStaleModal
        approvals={[_staleApproval()]} busy={false}
        defaultDecidedBy="ops-default"
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    expect(getByPlaceholderText(/ops1/).value).toBe("ops-default");
    fireEvent.change(getByPlaceholderText(/stale 신호 일괄/), { target: { value: " stale " } });
    fireEvent.click(getByText(/1건 취소/));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-default", note: "stale" });
  });

  it("Esc dispatches onCancel; Enter dispatches onConfirm", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <BulkCancelStaleModal
        approvals={[_staleApproval()]} busy={false}
        onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "", note: "" });
  });

  it("disables both buttons while busy", () => {
    const { getByText } = render(
      <BulkCancelStaleModal
        approvals={[_staleApproval()]} busy={true}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByText("닫기").disabled).toBe(true);
    expect(getByText(/처리 중/).disabled).toBe(true);
  });
});


describe("<Approvals> stale bulk-cancel button", () => {
  afterEach(cleanup);

  const NOW = Date.now();
  const FRESH = new Date(NOW - 2 * 60_000).toISOString();   // 2분 전 (fresh)
  const STALE = new Date(NOW - 11 * 60_000).toISOString();  // 11분 전 (stale)

  function _row(overrides = {}) {
    return { ..._PENDING, created_at: FRESH, ...overrides };
  }

  it("hides the bulk-cancel button when there are no stale rows", () => {
    const approvals = _makeApprovals({
      pending: [_row({ id: 1, created_at: FRESH }), _row({ id: 2, created_at: FRESH })],
    });
    const { queryByText } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(queryByText(/stale 일괄 취소/)).toBeNull();
  });

  it("shows the bulk-cancel button with stale count when at least one row is stale", () => {
    const approvals = _makeApprovals({
      pending: [_row({ id: 1, created_at: STALE }), _row({ id: 2, created_at: FRESH }),
                _row({ id: 3, created_at: STALE })],
    });
    const { getByText } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(getByText(/stale 일괄 취소 \(2\)/)).toBeTruthy();
  });

  it("clicking the bulk button opens the modal scoped to stale rows only", () => {
    const approvals = _makeApprovals({
      pending: [_row({ id: 1, created_at: STALE, symbol: "OLDSTK" }),
                _row({ id: 2, created_at: FRESH, symbol: "NEWSTK" })],
    });
    const { getByText, getByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByText(/stale 일괄 취소/));
    const dialog = within(getByRole("dialog"));
    // Modal scope contains the stale symbol and the "1건 취소" confirm — fresh
    // symbol is not in the modal list (verified by the count, since the modal
    // would say 2건 if it included the fresh row).
    expect(dialog.getByText("OLDSTK")).toBeTruthy();
    expect(dialog.getByText(/1건 취소/)).toBeTruthy();
  });

  it("modal confirm dispatches cancelMany with stale ids and decision", async () => {
    const approvals = _makeApprovals({
      pending: [_row({ id: 11, created_at: STALE }),
                _row({ id: 12, created_at: STALE }),
                _row({ id: 13, created_at: FRESH })],
    });
    approvals.cancelMany.mockResolvedValue();
    const { getByText, getByPlaceholderText, getByRole, queryByRole } = render(
      <Approvals approvals={approvals} operatorName="ops-prefill" />,
    );
    fireEvent.click(getByText(/stale 일괄 취소/));
    const dialog = within(getByRole("dialog"));
    fireEvent.change(dialog.getByPlaceholderText(/stale 신호 일괄/), {
      target: { value: "스테일 정리" },
    });
    await act(async () => {
      fireEvent.click(dialog.getByText(/2건 취소/));
    });
    expect(approvals.cancelMany).toHaveBeenCalledWith([11, 12], {
      decided_by: "ops-prefill", note: "스테일 정리",
    });
    await waitFor(() => expect(queryByRole("dialog")).toBeNull());
  });
});


describe("<ApproveAttemptFailureBadge>", () => {
  afterEach(cleanup);
  const NOW = new Date("2026-05-06T12:00:00Z").getTime();
  const minutesAgo = (m) => new Date(NOW - m * 60_000).toISOString();

  it("renders nothing when attempts is empty or undefined", () => {
    const { queryByTestId, rerender } = render(<ApproveAttemptFailureBadge />);
    expect(queryByTestId("approve-attempt-failure-badge")).toBeNull();
    rerender(<ApproveAttemptFailureBadge attempts={[]} />);
    expect(queryByTestId("approve-attempt-failure-badge")).toBeNull();
  });

  it("renders count + last attempt's relative time and reasons", () => {
    const attempts = [
      { at: minutesAgo(20), decided_by: "ops1",
        reasons: ["emergency stop is enabled"] },
      { at: minutesAgo(5),  decided_by: "ops2",
        reasons: ["max_order_notional 초과", "manual approval required by operation mode"] },
    ];
    const { getByTestId } = render(
      <ApproveAttemptFailureBadge attempts={attempts} now={NOW} />,
    );
    const badge = getByTestId("approve-attempt-failure-badge");
    expect(badge.textContent).toContain("2번째 시도");
    expect(badge.textContent).toContain("5분 전");
    expect(badge.textContent).toContain("max_order_notional 초과");
  });

  it("handles missing reasons array gracefully", () => {
    const attempts = [{ at: minutesAgo(3), decided_by: null }];
    const { getByTestId } = render(
      <ApproveAttemptFailureBadge attempts={attempts} now={NOW} />,
    );
    expect(getByTestId("approve-attempt-failure-badge").textContent).toContain("1번째 시도");
  });
});


describe("<Approvals> approve-attempt failure badge on PENDING row", () => {
  afterEach(cleanup);

  it("renders the badge when the row has attempts", () => {
    const approvals = _makeApprovals({
      pending: [{
        ..._PENDING,
        attempts: [{
          at: new Date().toISOString(),
          decided_by: "ops1",
          reasons: ["승인 시점 재평가에서 거부됨"],
        }],
      }],
    });
    const { getByTestId } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(getByTestId("approve-attempt-failure-badge").textContent)
      .toContain("승인 시점 재평가에서 거부됨");
  });

  it("does not render the badge when attempts is empty", () => {
    const approvals = _makeApprovals({
      pending: [{ ..._PENDING, attempts: [] }],
    });
    const { queryByTestId } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(queryByTestId("approve-attempt-failure-badge")).toBeNull();
  });
});


describe("historyEmptyMessage", () => {
  it("returns plain '결정된 항목이 없습니다' when history is empty", () => {
    expect(historyEmptyMessage([], "", "all")).toBe("결정된 항목이 없습니다");
    expect(historyEmptyMessage(undefined, "", "all")).toBe("결정된 항목이 없습니다");
  });

  it("returns the filtered variant when symbol filter narrows non-empty history to zero", () => {
    expect(historyEmptyMessage([{ id: 1 }], "005930", "all"))
      .toBe("해당 조건의 항목이 없습니다");
  });

  it("returns the filtered variant when status filter narrows non-empty history", () => {
    expect(historyEmptyMessage([{ id: 1 }], "", "REJECTED"))
      .toBe("해당 조건의 항목이 없습니다");
  });

  it("returns the filtered variant when both symbol + status are active", () => {
    expect(historyEmptyMessage([{ id: 1 }], "005930", "APPROVED"))
      .toBe("해당 조건의 항목이 없습니다");
  });

  it("returns plain message when no filter active", () => {
    expect(historyEmptyMessage([{ id: 1 }], "", "all")).toBe("결정된 항목이 없습니다");
  });
});


describe("<HistoryStatusFilterBar>", () => {
  afterEach(cleanup);

  it("renders four chips and highlights the active one", () => {
    const { getByRole } = render(<HistoryStatusFilterBar active="all" onChange={() => {}} />);
    expect(getByRole("radiogroup", { name: "처리 내역 상태 필터" })).toBeTruthy();
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
    expect(getByRole("radio", { name: "승인" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "거부" }).getAttribute("aria-checked")).toBe("false");
    expect(getByRole("radio", { name: "취소" }).getAttribute("aria-checked")).toBe("false");
  });

  it("calls onChange with the chip's status id", () => {
    const onChange = vi.fn();
    const { getByRole } = render(<HistoryStatusFilterBar active="all" onChange={onChange} />);
    fireEvent.click(getByRole("radio", { name: "승인" }));
    expect(onChange).toHaveBeenCalledWith("APPROVED");
    fireEvent.click(getByRole("radio", { name: "거부" }));
    expect(onChange).toHaveBeenLastCalledWith("REJECTED");
    fireEvent.click(getByRole("radio", { name: "취소" }));
    expect(onChange).toHaveBeenLastCalledWith("CANCELLED");
  });
});


describe("isValidHistoryStatus", () => {
  it("accepts the four canonical ids", () => {
    expect(isValidHistoryStatus("all")).toBe(true);
    expect(isValidHistoryStatus("APPROVED")).toBe(true);
    expect(isValidHistoryStatus("REJECTED")).toBe(true);
    expect(isValidHistoryStatus("CANCELLED")).toBe(true);
  });

  it("rejects unknown values (forward-compat against future builds)", () => {
    expect(isValidHistoryStatus("PENDING")).toBe(false);
    expect(isValidHistoryStatus("garbage")).toBe(false);
    expect(isValidHistoryStatus("")).toBe(false);
  });
});


describe("<Approvals> 처리 내역 status filter", () => {
  afterEach(() => { cleanup(); localStorage.clear(); });

  function _h(overrides = {}) {
    return {
      id: 1, symbol: "005930", side: "BUY", quantity: 1, order_type: "MARKET",
      limit_price: null, status: "APPROVED", mode: "LIVE_MANUAL_APPROVAL",
      decided_at: "2026-05-06T12:00:00+00:00", decided_by: "user", note: "",
      created_at: "2026-05-06T11:55:00+00:00", audit_id: 1,
      ...overrides,
    };
  }

  it("default '전체' shows all status rows", () => {
    const approvals = _makeApprovals({
      history: [
        _h({ id: 1, symbol: "AAA", status: "APPROVED" }),
        _h({ id: 2, symbol: "BBB", status: "REJECTED" }),
        _h({ id: 3, symbol: "CCC", status: "CANCELLED" }),
      ],
    });
    const { container } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).toContain("BBB");
    expect(container.textContent).toContain("CCC");
  });

  it("clicking 거부 narrows to REJECTED rows only", () => {
    const approvals = _makeApprovals({
      history: [
        _h({ id: 1, symbol: "AAA", status: "APPROVED" }),
        _h({ id: 2, symbol: "BBB", status: "REJECTED" }),
        _h({ id: 3, symbol: "CCC", status: "CANCELLED" }),
      ],
    });
    const { container, getByRole } = render(<Approvals approvals={approvals} operatorName="" />);
    fireEvent.click(getByRole("radio", { name: "거부" }));
    expect(container.textContent).not.toContain("AAA");
    expect(container.textContent).toContain("BBB");
    expect(container.textContent).not.toContain("CCC");
  });

  it("composes with symbol filter (status × symbol)", () => {
    const approvals = _makeApprovals({
      history: [
        _h({ id: 1, symbol: "AAA", status: "REJECTED" }),
        _h({ id: 2, symbol: "BBB", status: "REJECTED" }),
        _h({ id: 3, symbol: "AAA", status: "APPROVED" }),
      ],
    });
    const { container, getByRole, getAllByPlaceholderText } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByRole("radio", { name: "거부" }));
    const inputs = getAllByPlaceholderText(/종목/);
    fireEvent.change(inputs[0], { target: { value: "AAA" } });
    // Only id 1 (AAA + REJECTED) should remain
    expect(container.textContent).toContain("#1");
    expect(container.textContent).not.toContain("#2"); // BBB rejected
    expect(container.textContent).not.toContain("#3"); // AAA approved
  });

  it("persists selection to localStorage", () => {
    const approvals = _makeApprovals({ history: [_h()] });
    const { getByRole, unmount } = render(<Approvals approvals={approvals} operatorName="" />);
    fireEvent.click(getByRole("radio", { name: "취소" }));
    expect(localStorage.getItem(HISTORY_STATUS_STORAGE_KEY)).toBe("CANCELLED");
    unmount();
    const { getByRole: g2 } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(g2("radio", { name: "취소" }).getAttribute("aria-checked")).toBe("true");
  });

  it("falls back to 전체 when stored value is unknown", () => {
    localStorage.setItem(HISTORY_STATUS_STORAGE_KEY, "garbage");
    const approvals = _makeApprovals({ history: [_h()] });
    const { getByRole } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(getByRole("radio", { name: "전체" }).getAttribute("aria-checked")).toBe("true");
  });
});


describe("<Approvals> 처리 내역 symbol filter", () => {
  afterEach(cleanup);

  function _h(overrides = {}) {
    return {
      id: 1, symbol: "005930", side: "BUY", quantity: 1, order_type: "MARKET",
      limit_price: null, status: "APPROVED", mode: "LIVE_MANUAL_APPROVAL",
      decided_at: "2026-05-06T12:00:00+00:00", decided_by: "user", note: "",
      created_at: "2026-05-06T11:55:00+00:00", audit_id: 1,
      ...overrides,
    };
  }

  it("renders all history rows when filter is empty", () => {
    const approvals = _makeApprovals({
      history: [_h({ id: 1, symbol: "AAA" }), _h({ id: 2, symbol: "BBB" })],
    });
    const { container } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(container.textContent).toContain("AAA");
    expect(container.textContent).toContain("BBB");
  });

  it("narrows history rows by case-insensitive substring on symbol", () => {
    const approvals = _makeApprovals({
      history: [_h({ id: 1, symbol: "005930" }), _h({ id: 2, symbol: "000660" })],
    });
    const { container, getAllByPlaceholderText } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    // There are multiple symbol inputs (modal might render too); pick the one
    // for the history filter by placeholder text + scope. Placeholder is
    // unique so getAllByPlaceholderText returns the historical filter input
    // (modal isn't open).
    const inputs = getAllByPlaceholderText(/종목/);
    fireEvent.change(inputs[0], { target: { value: "0066" } });
    expect(container.textContent).toContain("000660");
    expect(container.textContent).not.toContain("005930");
  });

  it("shows the filtered empty message when filter narrows to zero", () => {
    const approvals = _makeApprovals({
      history: [_h({ id: 1, symbol: "005930" })],
    });
    const { getByText, getAllByPlaceholderText } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    const inputs = getAllByPlaceholderText(/종목/);
    fireEvent.change(inputs[0], { target: { value: "999999" } });
    expect(getByText("해당 조건의 항목이 없습니다")).toBeTruthy();
  });

  it("shows the plain empty message when history is empty (no filter applied)", () => {
    const approvals = _makeApprovals({ history: [] });
    const { getByText } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(getByText("결정된 항목이 없습니다")).toBeTruthy();
  });
});


describe("<Approvals> reasons on PENDING rows", () => {
  afterEach(cleanup);

  it("renders the reasons line on a PENDING row when present", () => {
    const approvals = _makeApprovals({
      pending: [{ ..._PENDING, reasons: ["manual approval required"] }],
    });
    const { container } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(container.textContent).toContain("사유:");
    expect(container.textContent).toContain("manual approval required");
  });

  it("omits the reasons line on a PENDING row with no reasons", () => {
    const approvals = _makeApprovals({
      pending: [{ ..._PENDING, reasons: [] }],
    });
    const { container } = render(<Approvals approvals={approvals} operatorName="" />);
    expect(container.textContent).not.toContain("사유:");
  });
});


describe("<ApprovalDecisionModal>", () => {
  afterEach(cleanup);

  it("titles the dialog by action variant", () => {
    const { rerender, getByRole } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("주문 승인");
    rerender(<ApprovalDecisionModal
      action="reject" approval={_PENDING} busy={false}
      onConfirm={() => {}} onCancel={() => {}} />);
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("주문 거부");
    rerender(<ApprovalDecisionModal
      action="cancel" approval={_PENDING} busy={false}
      onConfirm={() => {}} onCancel={() => {}} />);
    expect(getByRole("dialog").getAttribute("aria-label")).toBe("주문 취소");
  });

  it("renders order summary so operator can verify before confirming", () => {
    const { container } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(container.textContent).toContain("005930");
    expect(container.textContent).toContain("BUY");
    expect(container.textContent).toContain("5주");
    expect(container.textContent).toContain("#17");
  });

  it("pre-fills decided_by from defaultDecidedBy prop", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        defaultDecidedBy="ops-default"
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    expect(getByPlaceholderText(/ops1/).value).toBe("ops-default");
    fireEvent.click(getByText("✓ 승인"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops-default", note: "" });
  });

  it("trims surrounding whitespace before forwarding values", () => {
    const onConfirm = vi.fn();
    const { getByText, getByPlaceholderText } = render(
      <ApprovalDecisionModal
        action="reject" approval={_PENDING} busy={false}
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: " ops1 " } });
    fireEvent.change(getByPlaceholderText(/신호 노후/), { target: { value: " stale " } });
    fireEvent.click(getByText("✗ 거부"));
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "stale" });
  });

  it("disables confirm + close buttons while busy", () => {
    const { getByText } = render(
      <ApprovalDecisionModal
        action="cancel" approval={_PENDING} busy={true}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(getByText("닫기").disabled).toBe(true);
    expect(getByText(/처리 중/).disabled).toBe(true);
  });

  it("auto-focuses decided_by input when defaultDecidedBy is empty", () => {
    const { getByPlaceholderText } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(document.activeElement).toBe(getByPlaceholderText(/ops1/));
  });

  it("auto-focuses note input when defaultDecidedBy is pre-filled", () => {
    const { getByPlaceholderText } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        defaultDecidedBy="ops-default"
        onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(document.activeElement).toBe(getByPlaceholderText(/신호 노후/));
  });

  it("Esc dispatches onCancel", () => {
    const onCancel = vi.fn();
    render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        onConfirm={() => {}} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });

  it("Enter dispatches onConfirm with trimmed values", () => {
    const onConfirm = vi.fn();
    const { getByPlaceholderText } = render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={false}
        onConfirm={onConfirm} onCancel={() => {}} />,
    );
    fireEvent.change(getByPlaceholderText(/ops1/), { target: { value: " ops1 " } });
    fireEvent.change(getByPlaceholderText(/신호 노후/), { target: { value: " stale " } });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onConfirm).toHaveBeenCalledWith({ decided_by: "ops1", note: "stale" });
  });

  it("ignores Esc and Enter while busy", () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ApprovalDecisionModal
        action="approve" approval={_PENDING} busy={true}
        onConfirm={onConfirm} onCancel={onCancel} />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.keyDown(window, { key: "Enter" });
    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });
});


describe("<Approvals> button → modal flow", () => {
  let approvals;
  beforeEach(() => { approvals = _makeApprovals({ pending: [_PENDING] }); });
  afterEach(cleanup);

  it("clicking 승인 opens the modal without invoking approve yet", () => {
    const { getByText, queryByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByText(/✓ 승인/));
    expect(queryByRole("dialog")).not.toBeNull();
    expect(approvals.approve).not.toHaveBeenCalled();
  });

  it("modal confirm forwards decision to the matching hook function", async () => {
    approvals.approve.mockResolvedValue();
    const { getByText, getByRole, queryByRole } = render(
      <Approvals approvals={approvals} operatorName="ops-prefill" />,
    );
    fireEvent.click(getByText(/✓ 승인/));
    const dialog = within(getByRole("dialog"));
    fireEvent.change(dialog.getByPlaceholderText(/신호 노후/), {
      target: { value: "looks good" },
    });
    await act(async () => {
      fireEvent.click(dialog.getByText(/✓ 승인/));
    });
    expect(approvals.approve).toHaveBeenCalledWith(17, {
      decided_by: "ops-prefill", note: "looks good",
    });
    await waitFor(() => expect(queryByRole("dialog")).toBeNull());
  });

  it("reject button routes through reject() hook", async () => {
    approvals.reject.mockResolvedValue();
    const { getByText, getByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByText(/✗ 거부/));
    const dialog = within(getByRole("dialog"));
    await act(async () => {
      fireEvent.click(dialog.getByText(/✗ 거부/));
    });
    expect(approvals.reject).toHaveBeenCalled();
    expect(approvals.approve).not.toHaveBeenCalled();
    expect(approvals.cancel).not.toHaveBeenCalled();
  });

  it("cancel button routes through cancel() hook", async () => {
    approvals.cancel.mockResolvedValue();
    const { getByText, getByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByText(/⊘ 취소/));
    const dialog = within(getByRole("dialog"));
    await act(async () => {
      fireEvent.click(dialog.getByText(/⊘ 취소/));
    });
    expect(approvals.cancel).toHaveBeenCalled();
    expect(approvals.approve).not.toHaveBeenCalled();
    expect(approvals.reject).not.toHaveBeenCalled();
  });

  it("modal stays open and renders error inline when approve returns {ok:false}", async () => {
    const approvals = _makeApprovals({ pending: [_PENDING] });
    approvals.approve.mockResolvedValue({ ok: false, message: "재평가 거부됨: emergency stop" });
    const { getByText, getByRole, getByTestId, queryByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByText(/✓ 승인/));
    const dialog = within(getByRole("dialog"));
    await act(async () => {
      fireEvent.click(dialog.getByText(/✓ 승인/));
    });
    // Dialog stays open
    expect(queryByRole("dialog")).not.toBeNull();
    // Error block is rendered with the friendly message
    expect(getByTestId("decision-dialog-error").textContent).toContain("재평가 거부됨");
  });

  it("modal close button dismisses without calling any hook function", () => {
    const { getByText, getByRole, queryByRole } = render(
      <Approvals approvals={approvals} operatorName="" />,
    );
    fireEvent.click(getByText(/✓ 승인/));
    const dialog = within(getByRole("dialog"));
    fireEvent.click(dialog.getByText("닫기"));
    expect(queryByRole("dialog")).toBeNull();
    expect(approvals.approve).not.toHaveBeenCalled();
  });
});
