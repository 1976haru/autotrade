import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  MismatchRow,
  ReconciliationStatusCard,
} from "./ReconciliationStatusCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { reconciliationStatus: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.reconciliationStatus.mockResolvedValue({
    in_sync: true,
    broker_symbol_count: 0,
    audit_symbol_count: 0,
    matched_count: 0,
    mismatches: [],
  });
});


describe("<MismatchRow>", () => {
  it("renders quantity_mismatch with korean label and broker/audit qty", () => {
    const { getByText, getByTestId } = render(
      <MismatchRow mismatch={{
        symbol: "005930", broker_quantity: 15, audit_quantity: 10,
        quantity_diff: 5, kind: "quantity_mismatch",
      }} />,
    );
    expect(getByTestId("reconciliation-mismatch-005930")).toBeTruthy();
    expect(getByText("005930")).toBeTruthy();
    expect(getByText("broker 15 / audit 10")).toBeTruthy();
    expect(getByText("수량 불일치")).toBeTruthy();
  });

  it("renders broker_only kind", () => {
    const { getByText } = render(
      <MismatchRow mismatch={{
        symbol: "000660", broker_quantity: 5, audit_quantity: 0,
        quantity_diff: 5, kind: "broker_only",
      }} />,
    );
    expect(getByText("broker만 있음")).toBeTruthy();
  });

  it("renders audit_only kind", () => {
    const { getByText } = render(
      <MismatchRow mismatch={{
        symbol: "035420", broker_quantity: 0, audit_quantity: 3,
        quantity_diff: -3, kind: "audit_only",
      }} />,
    );
    expect(getByText("audit만 있음")).toBeTruthy();
  });

  it("falls back to raw kind for unknown values", () => {
    const { getByText } = render(
      <MismatchRow mismatch={{
        symbol: "005380", broker_quantity: 1, audit_quantity: 2,
        quantity_diff: -1, kind: "future_unknown_kind",
      }} />,
    );
    expect(getByText("future_unknown_kind")).toBeTruthy();
  });
});


describe("<ReconciliationStatusCard>", () => {
  it("shows IN SYNC when no mismatches", async () => {
    backendApi.reconciliationStatus.mockResolvedValueOnce({
      in_sync: true,
      broker_symbol_count: 2,
      audit_symbol_count: 2,
      matched_count: 2,
      mismatches: [],
    });
    const { findByText, getByTestId } = render(<ReconciliationStatusCard
      status={null} loading={false} error="" onRefresh={null} />);
    // Direct prop mode — use the controlled-prop variant instead.
    cleanup();
    const { getByTestId: getById, getByText } = render(<ReconciliationStatusCard
      status={{
        in_sync: true,
        broker_symbol_count: 2,
        audit_symbol_count: 2,
        matched_count: 2,
        mismatches: [],
      }}
      loading={false} error="" />);
    expect(getById("reconciliation-tile-state").textContent).toContain("IN SYNC");
    expect(getByText(/모든 종목 일치/)).toBeTruthy();
  });

  it("shows DRIFT and lists mismatches", () => {
    const { getByTestId, getByText } = render(<ReconciliationStatusCard
      status={{
        in_sync: false,
        broker_symbol_count: 1,
        audit_symbol_count: 1,
        matched_count: 0,
        mismatches: [{
          symbol: "005930", broker_quantity: 15, audit_quantity: 10,
          quantity_diff: 5, kind: "quantity_mismatch",
        }],
      }}
      loading={false} error="" />);
    expect(getByTestId("reconciliation-tile-state").textContent).toContain("DRIFT");
    expect(getByText(/불일치 \(1건\)/)).toBeTruthy();
    expect(getByTestId("reconciliation-mismatch-005930")).toBeTruthy();
  });

  it("renders multiple mismatches in order", () => {
    const { getByTestId } = render(<ReconciliationStatusCard
      status={{
        in_sync: false,
        broker_symbol_count: 2,
        audit_symbol_count: 1,
        matched_count: 0,
        mismatches: [
          { symbol: "005930", broker_quantity: 10, audit_quantity: 0,
            quantity_diff: 10, kind: "broker_only" },
          { symbol: "035420", broker_quantity: 0, audit_quantity: 3,
            quantity_diff: -3, kind: "audit_only" },
        ],
      }}
      loading={false} error="" />);
    expect(getByTestId("reconciliation-mismatch-005930")).toBeTruthy();
    expect(getByTestId("reconciliation-mismatch-035420")).toBeTruthy();
  });

  it("shows loading state without status", () => {
    const { getByText } = render(<ReconciliationStatusCard
      status={null} loading={true} error="" />);
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows error state with retry button", () => {
    const onRefresh = vi.fn();
    const { getByText, getByTestId } = render(<ReconciliationStatusCard
      status={null} loading={false} error="boom" onRefresh={onRefresh} />);
    // 233 (UI-005): ErrorState primitive 사용 — 'reconciliation 조회 실패' title
    // + raw error는 hint로. 'boom' 같은 의미 없는 raw는 friendlyErrorMessage가
    // 그대로 통과.
    expect(getByTestId("reconciliation-error").textContent).toContain("reconciliation 조회 실패");
    fireEvent.click(getByText(/다시 시도/));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getByText } = render(<ReconciliationStatusCard
      status={{
        in_sync: true, broker_symbol_count: 0, audit_symbol_count: 0,
        matched_count: 0, mismatches: [],
      }}
      loading={false} error="" onRefresh={onRefresh} />);
    fireEvent.click(getByText(/새로고침/));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("renders nothing when status is null and not loading/error", () => {
    const { container } = render(<ReconciliationStatusCard
      status={null} loading={false} error="" />);
    expect(container.firstChild).toBeNull();
  });
});


describe("useReconciliationStatus integration via card", () => {
  it("loads status on mount and shows IN SYNC", async () => {
    const { useReconciliationStatus } = await import("./ReconciliationStatusCard");
    backendApi.reconciliationStatus.mockResolvedValueOnce({
      in_sync: true,
      broker_symbol_count: 1,
      audit_symbol_count: 1,
      matched_count: 1,
      mismatches: [],
    });

    function Probe() {
      const r = useReconciliationStatus();
      return (
        <ReconciliationStatusCard
          status={r.status} loading={r.loading} error={r.error}
          onRefresh={r.refresh}
        />
      );
    }

    const { findByText } = render(<Probe />);
    await waitFor(() => expect(backendApi.reconciliationStatus).toHaveBeenCalled());
    await findByText(/모든 종목 일치/);
  });

  it("surfaces backend error and refresh recovers", async () => {
    const { useReconciliationStatus } = await import("./ReconciliationStatusCard");
    // React 18 strict-mode double-invoke makes ...Once unreliable here. Set the
    // default permanently so both effect runs reject identically, then swap to
    // a resolved default before clicking refresh.
    backendApi.reconciliationStatus.mockReset();
    backendApi.reconciliationStatus.mockRejectedValue(new Error("offline"));

    function Probe() {
      const r = useReconciliationStatus();
      return (
        <ReconciliationStatusCard
          status={r.status} loading={r.loading} error={r.error}
          onRefresh={r.refresh}
        />
      );
    }

    const { findByTestId, findByText, getByText } = render(<Probe />);
    // 233: ErrorState로 surface — raw 'offline' 문구 노출 X.
    await findByTestId("reconciliation-error");

    backendApi.reconciliationStatus.mockReset();
    backendApi.reconciliationStatus.mockResolvedValue({
      in_sync: false,
      broker_symbol_count: 1, audit_symbol_count: 0, matched_count: 0,
      mismatches: [{
        symbol: "005930", broker_quantity: 5, audit_quantity: 0,
        quantity_diff: 5, kind: "broker_only",
      }],
    });
    fireEvent.click(getByText(/다시 시도/));
    await findByText(/불일치 \(1건\)/);
  });
});
