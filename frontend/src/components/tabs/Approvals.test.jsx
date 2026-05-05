import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApprovalDecisionModal, Approvals, HistoryRow } from "./Approvals";


// useApprovals 자체를 모킹해 컴포넌트가 어떤 인자로 호출하는지만 검증한다
// (네트워크/실 상태 흐름은 useApprovals.test.js에서 별도로 다룸).
const _hookMock = {
  pending: [],
  history: [],
  loading: false,
  error: "",
  busy: false,
  approve: vi.fn(),
  reject:  vi.fn(),
  cancel:  vi.fn(),
  refresh: vi.fn(),
  refreshHistory: vi.fn(),
};

vi.mock("../../store/useApprovals", () => ({
  useApprovals: () => _hookMock,
}));


function _resetHook(overrides = {}) {
  Object.assign(_hookMock, {
    pending: [], history: [], loading: false, error: "", busy: false,
    ...overrides,
  });
  _hookMock.approve.mockReset();
  _hookMock.reject.mockReset();
  _hookMock.cancel.mockReset();
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
});


describe("<Approvals> button → modal flow", () => {
  beforeEach(() => { _resetHook({ pending: [_PENDING] }); });
  afterEach(cleanup);

  it("clicking 승인 opens the modal without invoking approve yet", () => {
    const { getByText, queryByRole } = render(<Approvals operatorName="" />);
    expect(queryByRole("dialog")).toBeNull();
    fireEvent.click(getByText(/✓ 승인/));
    expect(queryByRole("dialog")).not.toBeNull();
    expect(_hookMock.approve).not.toHaveBeenCalled();
  });

  it("modal confirm forwards decision to the matching hook function", async () => {
    _hookMock.approve.mockResolvedValue();
    const { getByText, getByRole, queryByRole } = render(
      <Approvals operatorName="ops-prefill" />,
    );
    fireEvent.click(getByText(/✓ 승인/));
    const dialog = within(getByRole("dialog"));
    fireEvent.change(dialog.getByPlaceholderText(/신호 노후/), {
      target: { value: "looks good" },
    });
    await act(async () => {
      fireEvent.click(dialog.getByText(/✓ 승인/));
    });
    expect(_hookMock.approve).toHaveBeenCalledWith(17, {
      decided_by: "ops-prefill", note: "looks good",
    });
    await waitFor(() => expect(queryByRole("dialog")).toBeNull());
  });

  it("reject button routes through reject() hook", async () => {
    _hookMock.reject.mockResolvedValue();
    const { getByText, getByRole } = render(<Approvals operatorName="" />);
    fireEvent.click(getByText(/✗ 거부/));
    const dialog = within(getByRole("dialog"));
    await act(async () => {
      fireEvent.click(dialog.getByText(/✗ 거부/));
    });
    expect(_hookMock.reject).toHaveBeenCalled();
    expect(_hookMock.approve).not.toHaveBeenCalled();
    expect(_hookMock.cancel).not.toHaveBeenCalled();
  });

  it("cancel button routes through cancel() hook", async () => {
    _hookMock.cancel.mockResolvedValue();
    const { getByText, getByRole } = render(<Approvals operatorName="" />);
    fireEvent.click(getByText(/⊘ 취소/));
    const dialog = within(getByRole("dialog"));
    await act(async () => {
      fireEvent.click(dialog.getByText(/⊘ 취소/));
    });
    expect(_hookMock.cancel).toHaveBeenCalled();
    expect(_hookMock.approve).not.toHaveBeenCalled();
    expect(_hookMock.reject).not.toHaveBeenCalled();
  });

  it("modal close button dismisses without calling any hook function", () => {
    const { getByText, getByRole, queryByRole } = render(<Approvals operatorName="" />);
    fireEvent.click(getByText(/✓ 승인/));
    const dialog = within(getByRole("dialog"));
    fireEvent.click(dialog.getByText("닫기"));
    expect(queryByRole("dialog")).toBeNull();
    expect(_hookMock.approve).not.toHaveBeenCalled();
  });
});
