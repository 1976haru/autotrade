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

import PaperCandidateApprovalCard, {
  normalizeCandidatesResponse,
} from "./PaperCandidateApprovalCard";


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
    expect(await screen.findByTestId("candidate-approval-empty")).toBeTruthy();
    expect(screen.getByTestId("candidate-approval-readiness").textContent)
      .toContain("Paper 후보 없음");
  });

  it("shows permanent badges", async () => {
    const api = _mockApi();
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect((await screen.findByTestId("candidate-approval-paper-only-badge")).textContent)
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
    expect(await screen.findByTestId("candidate-approval-waiting-banner")).toBeTruthy();
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
    const banner = await screen.findByTestId("candidate-approval-active-banner");
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
    const row = await screen.findByTestId("candidate-row-MOMENTUM::005930::rank1");
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
    fireEvent.click(await screen.findByTestId(
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
    fireEvent.click(await screen.findByTestId(
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
    fireEvent.click(await screen.findByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    ));
    await waitFor(() => expect(
      screen.getByTestId("candidate-approval-error"),
    ).toBeTruthy());
    expect(screen.getByTestId("candidate-approval-error").textContent)
      .toContain("approval_blocked_risk");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// fix/ci-paper-candidate-and-lint: normalizer 가 다양한 응답 모양을 모두
// 받아들이고 후보 row 가 정상 렌더링되는지 검증.
// ────────────────────────────────────────────────────────────────────────────
describe("normalizeCandidatesResponse — response shape coverage", () => {
  it("handles null", () => {
    const n = normalizeCandidatesResponse(null);
    expect(n.candidates).toEqual([]);
    expect(n.readiness_state).toBe("NO_CANDIDATE");
  });

  it("handles undefined", () => {
    const n = normalizeCandidatesResponse(undefined);
    expect(n.candidates).toEqual([]);
    expect(n.readiness_state).toBe("NO_CANDIDATE");
  });

  it("handles empty array", () => {
    const n = normalizeCandidatesResponse([]);
    expect(n.candidates).toEqual([]);
    expect(n.readiness_state).toBe("NO_CANDIDATE");
  });

  it("handles array directly returned", () => {
    const n = normalizeCandidatesResponse([_candidate()]);
    expect(n.candidates.length).toBe(1);
    expect(n.candidates[0].candidate_id).toBe("MOMENTUM::005930::rank1");
    // status=PENDING_APPROVAL → readiness 추론.
    expect(n.readiness_state).toBe("WAITING_APPROVAL");
  });

  it("handles { candidates: [...] }", () => {
    const n = normalizeCandidatesResponse({ candidates: [_candidate()] });
    expect(n.candidates.length).toBe(1);
    expect(n.readiness_state).toBe("WAITING_APPROVAL");
  });

  it("handles { items: [...] }", () => {
    const n = normalizeCandidatesResponse({ items: [_candidate()] });
    expect(n.candidates.length).toBe(1);
    expect(n.readiness_state).toBe("WAITING_APPROVAL");
  });

  it("handles { entries: [...] }", () => {
    const n = normalizeCandidatesResponse({ entries: [_candidate()] });
    expect(n.candidates.length).toBe(1);
    expect(n.readiness_state).toBe("WAITING_APPROVAL");
  });

  it("handles { data: { candidates: [...] } }", () => {
    const n = normalizeCandidatesResponse({
      data: { candidates: [_candidate()] },
    });
    expect(n.candidates.length).toBe(1);
    expect(n.candidates[0].candidate_id).toBe("MOMENTUM::005930::rank1");
  });

  it("handles { data: { items: [...] } }", () => {
    const n = normalizeCandidatesResponse({
      data: { items: [_candidate()] },
    });
    expect(n.candidates.length).toBe(1);
  });

  it("handles { result: { candidates: [...] } }", () => {
    const n = normalizeCandidatesResponse({
      result: { candidates: [_candidate()] },
    });
    expect(n.candidates.length).toBe(1);
  });

  it("preserves explicit readiness_state when provided", () => {
    const n = normalizeCandidatesResponse({
      readiness_state: "CANDIDATE_READY",
      candidates: [_candidate({ status: "APPROVED" })],
    });
    expect(n.readiness_state).toBe("CANDIDATE_READY");
  });

  it("infers CANDIDATE_READY when all candidates APPROVED + no explicit state", () => {
    const n = normalizeCandidatesResponse([
      _candidate({ status: "APPROVED" }),
    ]);
    expect(n.readiness_state).toBe("CANDIDATE_READY");
  });

  it("filters entries without candidate_id (defensive)", () => {
    const n = normalizeCandidatesResponse({
      candidates: [
        _candidate(),
        { candidate: { name: "X" } },  // candidate_id missing → drop
        null,
        "not an object",
      ],
    });
    expect(n.candidates.length).toBe(1);
  });

  it("rendering: candidate row appears with { items: [...] } shape", async () => {
    // 사용자 요청서 실패 케이스 재현 — backend 가 `items` 키로 응답해도
    // 후보 row 가 반드시 표시되어야 한다.
    const api = {
      autoPaperCandidates: vi.fn(async () => ({ items: [_candidate()] })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "WAITING_APPROVAL", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(
      screen.getByTestId("candidate-row-MOMENTUM::005930::rank1"),
    ).toBeTruthy();
    expect(screen.queryByTestId("candidate-approval-empty")).toBeNull();
  });

  it("rendering: candidate row appears with bare array response", async () => {
    const api = {
      autoPaperCandidates: vi.fn(async () => [_candidate()]),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "WAITING_APPROVAL", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(
      screen.getByTestId("candidate-row-MOMENTUM::005930::rank1"),
    ).toBeTruthy();
  });

  it("rendering: candidate row appears with { data: { candidates: [...] } }", async () => {
    const api = {
      autoPaperCandidates: vi.fn(async () => ({
        data: { candidates: [_candidate()] },
      })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "WAITING_APPROVAL", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(
      screen.getByTestId("candidate-row-MOMENTUM::005930::rank1"),
    ).toBeTruthy();
  });

  it("rendering: empty state only when truly empty (no candidate rows)", async () => {
    const api = {
      autoPaperCandidates: vi.fn(async () => ({ candidates: [] })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "NO_CANDIDATE", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(await screen.findByTestId("candidate-approval-empty")).toBeTruthy();
    // 어떤 candidate-row 도 없어야 함.
    expect(
      document.querySelector('[data-testid^="candidate-row-"]'),
    ).toBeNull();
  });

  it("does not render both empty + list at the same time", async () => {
    // 후보가 있는 응답에서 empty state 와 list 가 *동시* 표시되는 회귀 차단.
    const api = {
      autoPaperCandidates: vi.fn(async () => ({
        // readiness_state 가 누락된 응답에서도 동작해야 함.
        candidates: [_candidate()],
      })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "WAITING_APPROVAL", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(),
      autoPaperRejectCandidate: vi.fn(),
    };
    render(<PaperCandidateApprovalCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    expect(
      screen.getByTestId("candidate-row-MOMENTUM::005930::rank1"),
    ).toBeTruthy();
    expect(screen.queryByTestId("candidate-approval-empty")).toBeNull();
  });

  it("approve / reject buttons still work with { items: [...] } shape", async () => {
    const api = {
      autoPaperCandidates: vi.fn(async () => ({ items: [_candidate()] })),
      autoPaperActiveCandidate: vi.fn(async () => ({
        has_active: false, readiness_state: "WAITING_APPROVAL", active: null,
      })),
      autoPaperApproveCandidate: vi.fn(async () => ({})),
      autoPaperRejectCandidate: vi.fn(async () => ({})),
    };
    render(<PaperCandidateApprovalCard
      apiClient={api} pollIntervalMs={0} defaultOperatorId="op-B"
    />);
    await waitFor(() => expect(api.autoPaperCandidates).toHaveBeenCalled());
    fireEvent.click(await screen.findByTestId(
      "candidate-approve-btn-MOMENTUM::005930::rank1",
    ));
    await waitFor(() =>
      expect(api.autoPaperApproveCandidate).toHaveBeenCalled(),
    );
    expect(api.autoPaperApproveCandidate.mock.calls[0][0])
      .toBe("MOMENTUM::005930::rank1");
    expect(api.autoPaperApproveCandidate.mock.calls[0][1])
      .toEqual({ approved_by: "op-B" });
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
    const approveBtn = await screen.findByTestId(
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
    const note = await screen.findByTestId("candidate-approval-footer-note");
    expect(note.textContent).toContain("is_order_signal=false");
    expect(note.textContent).toContain("is_live_authorization=false");
    expect(note.textContent).toContain("실거래는 어떤 경로로도 진행되지 않습니다");
  });
});
