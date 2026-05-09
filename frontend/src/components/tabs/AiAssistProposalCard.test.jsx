import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AiAssistProposalCard,
  AiAssistSummaryTile,
  useAiAssistSummary,
} from "./AiAssistProposalCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    aiAssistSubmit:  vi.fn(),
    aiAssistSummary: vi.fn(),
  },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.aiAssistSummary.mockResolvedValue({
    pending_count: 0, approved_count_24h: 0, rejected_count_24h: 0,
    total_24h: 0, last_submitted_at: null,
    notice: "AI는 매수/매도 후보 제안만 합니다. 모든 주문은 사람 승인 후에만 broker로 진행됩니다.",
  });
});


describe("<AiAssistProposalCard>", () => {
  it("renders '실제 주문 아님' badge prominently", () => {
    const { getByTestId } = render(<AiAssistProposalCard />);
    expect(getByTestId("ai-assist-not-real-badge").textContent)
      .toContain("실제 주문 아님");
  });

  it("disables submit when symbol empty", () => {
    const { getByTestId } = render(<AiAssistProposalCard />);
    const btn = getByTestId("ai-assist-submit-btn");
    expect(btn.disabled).toBe(true);
  });

  it("submits NEEDS_APPROVAL response and shows approval id", async () => {
    backendApi.aiAssistSubmit.mockResolvedValueOnce({
      decision: "NEEDS_APPROVAL",
      reasons: ["manual approval required by operation mode"],
      audit_id: 42, approval_id: 7,
      permission_note: "AI permission OK",
      candidate_meta: { source: "AI_ASSIST" },
      submitted_at: "2026-05-09T12:00:00+00:00",
    });

    const { getByTestId, getByPlaceholderText } = render(<AiAssistProposalCard />);
    fireEvent.change(getByPlaceholderText("005930"), { target: { value: "005930" } });
    fireEvent.click(getByTestId("ai-assist-submit-btn"));

    await waitFor(() =>
      expect(backendApi.aiAssistSubmit).toHaveBeenCalled(),
    );
    const banner = await waitFor(() => getByTestId("ai-assist-decision-banner"));
    expect(banner.textContent).toMatch(/결재 큐 등록|승인 대기/);
    expect(getByTestId("ai-assist-approval-id").textContent).toContain("#7");
  });

  it("shows REJECTED banner with risk reasons when RiskManager rejects", async () => {
    backendApi.aiAssistSubmit.mockResolvedValueOnce({
      decision: "REJECTED",
      reasons: ["per_order_notional_max exceeded", "emergency_stop=true"],
      audit_id: 99, approval_id: null,
      permission_note: "AI permission OK",
      candidate_meta: { source: "AI_ASSIST" },
      submitted_at: "2026-05-09T12:00:00+00:00",
    });

    const { getByTestId, queryByTestId, getByPlaceholderText } = render(
      <AiAssistProposalCard defaultSymbol="005930" />,
    );
    fireEvent.change(getByPlaceholderText("005930"), { target: { value: "005930" } });
    fireEvent.click(getByTestId("ai-assist-submit-btn"));
    await waitFor(() =>
      expect(backendApi.aiAssistSubmit).toHaveBeenCalled(),
    );
    const banner = await waitFor(() => getByTestId("ai-assist-decision-banner"));
    expect(banner.textContent).toContain("RiskManager 거부");
    expect(banner.textContent).toContain("per_order_notional_max");
    // No approval id on reject.
    expect(queryByTestId("ai-assist-approval-id")).toBeNull();
  });

  it("surfaces submit error from backendApi", async () => {
    backendApi.aiAssistSubmit.mockRejectedValueOnce(new Error("network down"));
    const { getByTestId, getByPlaceholderText } = render(
      <AiAssistProposalCard defaultSymbol="005930" />,
    );
    fireEvent.change(getByPlaceholderText("005930"), { target: { value: "005930" } });
    fireEvent.click(getByTestId("ai-assist-submit-btn"));
    await waitFor(() => expect(backendApi.aiAssistSubmit).toHaveBeenCalled());
    const err = await waitFor(() => getByTestId("ai-assist-error"));
    expect(err.textContent).toContain("network down");
  });

  it("includes supporting/opposing reasons + risk_note in submit body", async () => {
    backendApi.aiAssistSubmit.mockResolvedValueOnce({
      decision: "NEEDS_APPROVAL", reasons: [], audit_id: 1, approval_id: 1,
      permission_note: "ok", candidate_meta: {}, submitted_at: "2026-05-09T00:00:00Z",
    });
    const { getByTestId, getByPlaceholderText } = render(
      <AiAssistProposalCard defaultSymbol="005930" />,
    );
    fireEvent.change(getByPlaceholderText("005930"), { target: { value: "005930" } });
    fireEvent.change(getByTestId("ai-assist-supporting-reasons"),
      { target: { value: "bull cross\nvol spike" } });
    fireEvent.change(getByTestId("ai-assist-opposing-reasons"),
      { target: { value: "rsi 78" } });
    fireEvent.click(getByTestId("ai-assist-submit-btn"));

    await waitFor(() => expect(backendApi.aiAssistSubmit).toHaveBeenCalled());
    const body = backendApi.aiAssistSubmit.mock.calls[0][0];
    expect(body.symbol).toBe("005930");
    expect(body.supporting_reasons).toEqual(["bull cross", "vol spike"]);
    expect(body.opposing_reasons).toEqual(["rsi 78"]);
  });
});


describe("<AiAssistSummaryTile>", () => {
  const _summary = {
    pending_count: 3, approved_count_24h: 5, rejected_count_24h: 2,
    total_24h: 10, last_submitted_at: "2026-05-09T12:00:00+00:00",
    notice: "AI 제안 안내",
  };

  it("renders four count tiles", () => {
    const { getByTestId } = render(
      <AiAssistSummaryTile summary={_summary} loading={false} error="" />,
    );
    expect(getByTestId("ai-assist-tile-pending").textContent).toContain("3");
    expect(getByTestId("ai-assist-tile-approved-24h").textContent).toContain("5");
    expect(getByTestId("ai-assist-tile-rejected-24h").textContent).toContain("2");
    expect(getByTestId("ai-assist-tile-total-24h").textContent).toContain("10");
  });

  it("pending tile jumps to approve tab on click", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = render(
      <AiAssistSummaryTile summary={_summary} loading={false} error=""
                            onJumpTab={onJumpTab} />,
    );
    fireEvent.click(getByTestId("ai-assist-tile-pending"));
    expect(onJumpTab).toHaveBeenCalledWith("approve");
  });

  it("shows loading state without summary", () => {
    const { getByText } = render(
      <AiAssistSummaryTile summary={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows error state when error string provided", () => {
    const { getByTestId } = render(
      <AiAssistSummaryTile summary={null} loading={false} error="boom" />,
    );
    expect(getByTestId("ai-assist-summary-error").textContent).toContain("boom");
  });

  it("renders nothing when summary is null and no loading/error", () => {
    const { container } = render(
      <AiAssistSummaryTile summary={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });
});


describe("useAiAssistSummary integration", () => {
  it("fetches summary on mount and passes to tile", async () => {
    backendApi.aiAssistSummary.mockResolvedValueOnce({
      pending_count: 4, approved_count_24h: 0, rejected_count_24h: 0,
      total_24h: 4, last_submitted_at: null, notice: "x",
    });

    function Probe() {
      const r = useAiAssistSummary();
      return (
        <AiAssistSummaryTile
          summary={r.summary} loading={r.loading} error={r.error}
        />
      );
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.aiAssistSummary).toHaveBeenCalled());
    const tile = await findByTestId("ai-assist-tile-pending");
    expect(tile.textContent).toContain("4");
  });
});
