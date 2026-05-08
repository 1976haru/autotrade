import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SignalExplainabilityPanel } from "./SignalExplainabilityPanel";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    explainSignal: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _payload(overrides = {}) {
  return {
    audit_trace_id: 42,
    symbol:         "005930",
    strategy:       "VolumeBreakout",
    action:         "BUY",
    final_status:   "APPROVED",
    summary:        "거래대금 증가 / VWAP 상단",
    reasons: [
      { category: "STRATEGY",         status: "PASS",    message: "거래대금이 평균 대비 증가함", severity: "MEDIUM" },
      { category: "RISK_MANAGER",     status: "PASS",    message: "audit decision = APPROVED", code: "DECISION_APPROVED" },
      { category: "MARKET_REGIME",    status: "WARN",    message: "REDUCE_SIZE 권고" },
      { category: "RISK_MANAGER",     status: "FAIL",    message: "max_order_notional 초과" },
      { category: "PERMISSION_GATE",  status: "BLOCKED", message: "운영자 승인 거부" },
      { category: "AGENT",            status: "INFO",    message: "AI confidence = 60", code: "AI_CONFIDENCE" },
    ],
    indicators:    null,
    risk_notes:    [],
    operator_note: null,
    grouped: {
      PASS: [
        { category: "STRATEGY",     status: "PASS",    message: "거래대금이 평균 대비 증가함" },
        { category: "RISK_MANAGER", status: "PASS",    message: "audit decision = APPROVED", code: "DECISION_APPROVED" },
      ],
      WARN:    [{ category: "MARKET_REGIME",   status: "WARN",    message: "REDUCE_SIZE 권고" }],
      FAIL:    [{ category: "RISK_MANAGER",    status: "FAIL",    message: "max_order_notional 초과" }],
      BLOCKED: [{ category: "PERMISSION_GATE", status: "BLOCKED", message: "운영자 승인 거부" }],
      INFO:    [{ category: "AGENT",           status: "INFO",    message: "AI confidence = 60", code: "AI_CONFIDENCE" }],
    },
    ...overrides,
  };
}


describe("<SignalExplainabilityPanel>", () => {
  beforeEach(() => {
    backendApi.explainSignal.mockReset();
  });
  afterEach(cleanup);

  it("renders empty placeholder when no auditId given", () => {
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={null} />);
    expect(getByTestId("signal-explainability-panel-no-audit")).toBeTruthy();
  });

  it("renders loading state while fetching", () => {
    backendApi.explainSignal.mockReturnValue(new Promise(() => {}));  // never resolves
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    expect(getByTestId("signal-explainability-panel-loading")).toBeTruthy();
    expect(backendApi.explainSignal).toHaveBeenCalledWith(42);
  });

  it("renders summary, badges, and grouped reason cards", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload());
    const { getByTestId, getAllByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-summary"));

    // Summary fields
    const summary = getByTestId("signal-explain-summary");
    expect(summary.textContent).toContain("005930");
    expect(summary.textContent).toContain("BUY");
    expect(summary.textContent).toContain("VolumeBreakout");
    expect(summary.textContent).toContain("audit_id #42");
    expect(getByTestId("signal-explain-final").textContent).toContain("APPROVED");

    // Each group rendered
    expect(getByTestId("signal-explain-group-PASS")).toBeTruthy();
    expect(getByTestId("signal-explain-group-WARN")).toBeTruthy();
    expect(getByTestId("signal-explain-group-FAIL")).toBeTruthy();
    expect(getByTestId("signal-explain-group-BLOCKED")).toBeTruthy();
    expect(getByTestId("signal-explain-group-INFO")).toBeTruthy();

    // Reason cards rendered with category attribute
    const cards = getAllByTestId("signal-explain-reason");
    expect(cards.length).toBe(6);
    const categories = cards.map((c) => c.getAttribute("data-category"));
    expect(categories).toContain("STRATEGY");
    expect(categories).toContain("MARKET_REGIME");
    expect(categories).toContain("PERMISSION_GATE");
    expect(categories).toContain("AGENT");
  });

  it("renders 'no reasons' empty state when reasons array is empty", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload({
      reasons: [],
      grouped: { PASS: [], WARN: [], FAIL: [], BLOCKED: [], INFO: [] },
      summary: "",
    }));
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explainability-panel-empty-reasons"));
    expect(getByTestId("signal-explainability-panel-empty-reasons").textContent)
      .toContain("판정 근거가 아직 없습니다");
  });

  it("hides empty groups when status has no reasons", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload({
      reasons: [{ category: "STRATEGY", status: "PASS", message: "ok" }],
      grouped: {
        PASS: [{ category: "STRATEGY", status: "PASS", message: "ok" }],
        WARN: [], FAIL: [], BLOCKED: [], INFO: [],
      },
    }));
    const { getByTestId, queryByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-group-PASS"));
    expect(queryByTestId("signal-explain-group-WARN")).toBeNull();
    expect(queryByTestId("signal-explain-group-FAIL")).toBeNull();
    expect(queryByTestId("signal-explain-group-BLOCKED")).toBeNull();
  });

  it("does NOT expose raw 'Failed to fetch' on network error", async () => {
    backendApi.explainSignal.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), { status: undefined }),
    );
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explainability-panel-error"));
    const errorPanel = getByTestId("signal-explainability-panel-error");
    expect(errorPanel.textContent).toContain("백엔드 서버에 연결할 수 없습니다");
    // raw 원문 노출 금지
    expect(errorPanel.textContent).not.toContain("Failed to fetch");
  });

  it("renders friendly 404 message", async () => {
    backendApi.explainSignal.mockRejectedValue(
      Object.assign(new Error("Not Found"), { status: 404 }),
    );
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={9999} />);
    await waitFor(() => getByTestId("signal-explainability-panel-error"));
    expect(getByTestId("signal-explainability-panel-error").textContent)
      .toContain("판정 기록을 찾을 수 없습니다");
  });

  it("renders risk_notes warning section when present", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload({
      risk_notes: ["VWAP 격차 과도", "intraday runup 큼"],
    }));
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-risk-notes"));
    const notes = getByTestId("signal-explain-risk-notes");
    expect(notes.textContent).toContain("VWAP 격차 과도");
    expect(notes.textContent).toContain("intraday runup 큼");
  });

  it("renders operator_note when present", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload({
      operator_note: "운영자 명시 보류 — 익일 재검토",
    }));
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-operator-note"));
    expect(getByTestId("signal-explain-operator-note").textContent)
      .toContain("운영자 명시 보류 — 익일 재검토");
  });

  it("renders REJECTED final_status with danger badge", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload({
      final_status: "REJECTED",
    }));
    const { getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-final"));
    expect(getByTestId("signal-explain-final").textContent).toContain("REJECTED");
  });

  it("does not render is_order_intent or order side fields directly (read-only)", async () => {
    // Component is a read-only audit panel — no order action buttons exposed.
    backendApi.explainSignal.mockResolvedValue(_payload());
    const { container } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() =>
      expect(container.querySelector('[data-testid="signal-explain-summary"]')).toBeTruthy(),
    );
    // No buy/sell/place/submit buttons inside the panel
    expect(container.querySelector('button[data-testid*="approve"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="reject"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="place"]')).toBeNull();
  });

  it("does not call API again when same auditId rerenders", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload());
    const { rerender, getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-summary"));
    expect(backendApi.explainSignal).toHaveBeenCalledTimes(1);
    rerender(<SignalExplainabilityPanel auditId={42} />);
    // Same id → no new fetch (effect's dep is auditId).
    expect(backendApi.explainSignal).toHaveBeenCalledTimes(1);
  });

  it("re-fetches when auditId changes", async () => {
    backendApi.explainSignal.mockResolvedValue(_payload());
    const { rerender, getByTestId } = render(<SignalExplainabilityPanel auditId={42} />);
    await waitFor(() => getByTestId("signal-explain-summary"));
    backendApi.explainSignal.mockResolvedValue(_payload({ audit_trace_id: 100 }));
    rerender(<SignalExplainabilityPanel auditId={100} />);
    await waitFor(() =>
      expect(backendApi.explainSignal).toHaveBeenCalledWith(100),
    );
    expect(backendApi.explainSignal).toHaveBeenCalledTimes(2);
  });
});
