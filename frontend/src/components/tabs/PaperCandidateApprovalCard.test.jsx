/**
 * #PaperCandidateWire: PaperCandidateApprovalCard tests.
 *
 * Invariants:
 * - "승인 후 Paper에서만 사용" + "실거래 활성화 아님" 배지 영구.
 * - 실거래 시작 / 지금 매수 / 지금 매도 / Place Order / Live 활성화 /
 *   ENABLE_LIVE_TRADING 라벨 button 0개.
 * - 후보 없음 / 승인 대기 / 승인된 후보 상태 표시.
 * - 승인 / 거절 버튼 click 시 apiClient 호출.
 * - secret 노출 없음 (입력 form 0개).
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import PaperCandidateApprovalCard from "./PaperCandidateApprovalCard";


afterEach(cleanup);


function _mockApi(initial = { total: 0, readiness_state: "NO_CANDIDATE",
                              candidates: [] }) {
  return {
    autoPaperCandidates: vi.fn(async () => initial),
    autoPaperActiveCandidate: vi.fn(async () => ({
      has_active: initial.readiness_state === "CANDIDATE_READY",
      readiness_state: initial.readiness_state,
      active: null,
    })),
    autoPaperApproveCandidate: vi.fn(async () => ({
      candidate: { status: "APPROVED" },
    })),
    autoPaperRejectCandidate: vi.fn(async () => ({
      candidate: { status: "REJECTED" },
    })),
  };
}


function _candidate(over = {}) {
  return {
    candidate_id: "MOMENTUM::005930::rank1",
    status: "PENDING_APPROVAL",
    approved_by: null,
    approved_at: null,
    rejected_by: null,
    rejected_at: null,
    decision_notes: [],
    loaded_at: "2026-05-19T01:00:00+00:00",
    candidate: {
      rank: 1,
      name: "MOMENTUM",
      included_tactics: ["MOMENTUM"],
      included_strategies: ["sma_crossover", "volume_breakout"],
      symbol: "005930",
      primary_regime: "TREND_UP",
      composite_score: 0.6234,
      recommended_reasons: ["expectancy=200 / pf=1.5", "regime PASS"],
      risk_flags: [],
      requires_operator_approval: true,
    },
    is_order_signal: false,
    auto_apply_allowed: false,
    is_live_authorization: false,
    ...over,
  };
}


describe("PaperCandidateApprovalCard — empty / readiness", () => {
  it("shows empty state when no candidates", async () => {
    const api = _mockApi();
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(screen.getByTestId("candidate-approval-empty")).toBeTruthy();
    expect(screen.getByTestId("candidate-approval-readiness").textContent)
      .toContain("Paper 후보 없음");
  });

  it("shows permanent badges", async () => {
    const api = _mockApi();
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(screen.getByTestId("candidate-approval-paper-only-badge").textContent)
      .toContain("승인 후 Paper에서만 사용");
    expect(screen.getByTestId("candidate-approval-no-live-badge").textContent)
      .toContain("실거래 활성화 아님");
  });

  it("shows waiting banner when pending", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(screen.getByTestId("candidate-approval-waiting-banner")).toBeTruthy();
    expect(screen.getByTestId("candidate-approval-readiness").textContent)
      .toContain("승인 대기");
  });

  it("shows active banner when ready", async () => {
    const candidate = _candidate({
      status: "APPROVED",
      approved_by: "op-1",
      approved_at: "2026-05-19T01:01:00+00:00",
    });
    const api = {
      autoPaperCandidates: vi.fn(async () => ({
        total: 1, readiness_state: "CANDIDATE_READY",
        candidates: [candidate],
      })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: true,
        readiness_state: "CANDIDATE_READY",
        active: candidate,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperActiveCandidate).toHaveBeenCalled());
    const banner = screen.getByTestId("candidate-approval-active-banner");
    expect(banner.textContent).toContain("MOMENTUM::005930::rank1");
  });
});


describe("PaperCandidateApprovalCard — candidate row + actions", () => {
  it("renders candidate row with name/symbol/tactics/score", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    const row = screen.getByTestId("candidate-row-MOMENTUM::005930::rank1");
    expect(row.textContent).toContain("MOMENTUM");
    expect(row.textContent).toContain("005930");
    expect(row.textContent).toContain("TREND_UP");
    expect(row.textContent).toContain("0.623");
    expect(screen.getByTestId(
      "candidate-tactics-MOMENTUM::005930::rank1",
    ).textContent).toContain("MOMENTUM");
  });

  it("approve button calls autoPaperApproveCandidate", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard
      apiClient={api} pollIntervalMs={0} defaultOperatorId="op-A"
    />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    ));
    await waitFor(() =>
      expect(api.autoPaperApproveCandidate).toHaveBeenCalled(),
    );
    expect(api.autoPaperApproveCandidate.mock.calls[0][0])
      .toBe("MOMENTUM::005930::rank1");
    expect(api.autoPaperApproveCandidate.mock.calls[0][1])
      .toEqual({ approved_by: "op-A" });
  });

  it("reject button calls autoPaperRejectCandidate", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId(
      "candidate-reject-btn-MOMENTUM::005930::rank1",
    ));
    await waitFor(() =>
      expect(api.autoPaperRejectCandidate).toHaveBeenCalled(),
    );
  });

  it("approve/reject buttons missing on APPROVED candidate", async () => {
    const c = _candidate({
      status: "APPROVED",
      approved_by: "op-1",
      approved_at: "2026-05-19T01:00:00+00:00",
    });
    const api = _mockApi({
      total: 1, readiness_state: "CANDIDATE_READY",
      candidates: [c],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(screen.queryByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    )).toBeNull();
    expect(screen.queryByTestId(
      "candidate-reject-btn-MOMENTUM::005930::rank1",
    )).toBeNull();
  });

  it("error message shown when API rejects approval", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    api.autoPaperApproveCandidate = vi.fn(async () => {
      throw new Error("approval_blocked_risk");
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    ));
    await waitFor(() => expect(
      screen.getByTestId("candidate-approval-error"),
    ).toBeTruthy());
    expect(screen.getByTestId("candidate-approval-error").textContent)
      .toContain("approval_blocked_risk");
  });
});


describe("PaperCandidateApprovalCard — invariants", () => {
  it("no order labels anywhere in DOM", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    const { container } = render(
      <PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />,
    );
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    const text = container.textContent || "";
    const forbidden = [
      "지금 매수", "지금 매도", "Place Order",
      "실거래 시작", "실거래 활성화 시작", "Live 활성화",
      "ENABLE_LIVE_TRADING=true", "ENABLE_AI_EXECUTION=true",
    ];
    for (const f of forbidden) {
      expect(text).not.toContain(f);
    }
  });

  it("zero text inputs / textareas (no secret entry surface)", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(screen.queryAllByRole("textbox").length).toBe(0);
  });

  it("approve/reject buttons say 'Paper 승인' / '거절' — never 'Live'", async () => {
    const api = _mockApi({
      total: 1, readiness_state: "WAITING_APPROVAL",
      candidates: [_candidate()],
    });
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    const approveBtn = screen.getByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    );
    expect(approveBtn.textContent).toBe("Paper 승인");
    expect(approveBtn.textContent).not.toContain("Live");
    expect(approveBtn.textContent).not.toContain("실거래");
  });

  it("footer note locks Paper-only invariants", async () => {
    const api = _mockApi();
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    const note = screen.getByTestId("candidate-approval-footer-note");
    expect(note.textContent).toContain("is_order_signal=false");
    expect(note.textContent).toContain("is_live_authorization=false");
    expect(note.textContent).toContain("실거래는 어떤 경로로도 진행되지 않습니다");
  });
});
