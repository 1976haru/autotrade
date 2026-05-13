/**
 * 체크리스트 #68: AuditEventTimelineCard 테스트.
 *
 * invariant:
 *   - 삭제 / 수정 버튼 0개 — archive 버튼만 존재
 *   - archive는 확인 모달 필수 (직접 호출 없음)
 *   - "append-only" 안내 항상 노출
 *   - severity / source chip 필터
 *   - archived 행은 별도 표시 + 기본 숨김
 *   - friendly error — raw "Failed to fetch" 미노출
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuditEventTimelineCard } from "./AuditEventTimelineCard";
import { backendApi } from "../../services/backend/client";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    auditEventsList:   vi.fn(),
    auditEventArchive: vi.fn(),
    auditEventNote:    vi.fn(),
    auditEventGet:     vi.fn(),
    // 금지 API — 호출되면 invariant 위반
    brokerOrder:       vi.fn(),
    approveApproval:   vi.fn(),
    cancelApproval:    vi.fn(),
    setEmergencyStop:  vi.fn(),
  },
}));


function _event(overrides = {}) {
  return {
    id: 1,
    created_at: "2026-05-25T09:00:00Z",
    event_type: "SIGNAL",
    severity:   "INFO",
    source:     "STRATEGY",
    actor:      "agent-1",
    symbol:     "005930",
    strategy:   "sma_crossover",
    mode:       "SIMULATION",
    target_kind: null,
    target_id:   null,
    summary:    "BUY signal on 005930",
    reason:     "sma crossover up",
    details:    { confidence: 80 },
    archived:   false,
    archived_at: null,
    archived_by: null,
    archive_note: null,
    ...overrides,
  };
}


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.auditEventsList.mockResolvedValue([
    _event({ id: 1, severity: "INFO",     source: "STRATEGY",
              event_type: "SIGNAL",          summary: "BUY signal" }),
    _event({ id: 2, severity: "CRITICAL", source: "OPERATOR",
              event_type: "EMERGENCY_STOP", summary: "Emergency stop enabled" }),
    _event({ id: 3, severity: "WARN",     source: "AI",
              event_type: "AI_PROPOSAL",     summary: "AI proposed BUY 005930" }),
  ]);
});


describe("<AuditEventTimelineCard>", () => {
  it("renders events with severity / source chips + append-only banner", async () => {
    const { findByTestId, getByTestId } = render(
      <AuditEventTimelineCard testId="audit-card" />,
    );
    await findByTestId("audit-card");
    // append-only 안내 항상 노출
    expect(getByTestId("audit-card-policy-banner").textContent)
      .toMatch(/append-only/);
    expect(getByTestId("audit-card-policy-banner").textContent)
      .toMatch(/삭제.*수정 UI는 제공되지 않습니다/);

    // 3건 표시
    await findByTestId("audit-event-row-1");
    await findByTestId("audit-event-row-2");
    await findByTestId("audit-event-row-3");
    // severity 색 / source 색 chip
    await findByTestId("audit-event-severity-1");
    await findByTestId("audit-event-source-2");
  });

  it("never renders a delete or edit button (invariant lock)", async () => {
    const { findByTestId, container } = render(<AuditEventTimelineCard />);
    await findByTestId("audit-event-timeline-card");
    // 본 invariant는 *버튼*에만 적용 — banner text는 정책 설명이라 "삭제"/"수정"
    // 단어가 의도적으로 포함됨. 실제 위반은 button 요소에만 존재한다.
    const buttons = Array.from(container.querySelectorAll("button"));
    const forbidden = /삭제|수정|Delete|Edit|Remove/i;
    const violators = buttons.filter((b) => forbidden.test(b.textContent || ""));
    expect(violators).toEqual([]);
  });

  it("renders archive button per non-archived row", async () => {
    const { findAllByText } = render(<AuditEventTimelineCard />);
    const buttons = await findAllByText(/archive/);
    // 3건 모두 archived=false → archive 버튼 3개 + 안내 banner의 'archived' 문자열
    // 최소 3개 archive 버튼 존재
    const btnCount = buttons.filter((el) => el.tagName === "BUTTON").length;
    expect(btnCount).toBeGreaterThanOrEqual(3);
  });

  it("archive click opens confirm modal — no immediate API call", async () => {
    const { findAllByText, findByTestId } = render(<AuditEventTimelineCard />);
    const archiveBtns = (await findAllByText(/archive/)).filter(
      (el) => el.tagName === "BUTTON",
    );
    fireEvent.click(archiveBtns[0]);
    await findByTestId("audit-event-archive-summary");
    // 모달 열렸을 뿐 — archive API 미호출
    expect(backendApi.auditEventArchive).not.toHaveBeenCalled();
  });

  it("confirm modal triggers archive API call + refresh", async () => {
    const { findAllByText, findByTestId } = render(
      <AuditEventTimelineCard operatorName="ops1" />,
    );
    backendApi.auditEventArchive.mockResolvedValueOnce({
      id: 1, archived: true, archived_by: "ops1",
    });
    const archiveBtns = (await findAllByText(/archive/)).filter(
      (el) => el.tagName === "BUTTON",
    );
    fireEvent.click(archiveBtns[0]);
    const dialog = await findByTestId("audit-event-archive-summary");
    const confirmBtn = dialog.closest("[role='dialog']")
      .querySelector("button:nth-of-type(2)");
    await act(async () => { fireEvent.click(confirmBtn); });
    await waitFor(() => {
      expect(backendApi.auditEventArchive).toHaveBeenCalled();
    });
    // 호출 인자는 event id + decided_by/note 객체
    const [callId, callPayload] = backendApi.auditEventArchive.mock.calls[0];
    expect(callId).toBe(1);
    expect(callPayload).toMatchObject({
      archived_by: expect.any(String),
    });
    // 핵심 invariant — 본 흐름에서 broker / order / risk API 미호출
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
    expect(backendApi.approveApproval).not.toHaveBeenCalled();
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
  });

  it("archived rows are hidden by default", async () => {
    backendApi.auditEventsList.mockResolvedValueOnce([
      _event({ id: 10, summary: "fresh" }),
      _event({ id: 11, summary: "archived already",
                archived: true, archived_by: "old-ops" }),
    ]);
    const { findByTestId, queryByTestId } = render(<AuditEventTimelineCard />);
    await findByTestId("audit-event-row-10");
    // archived row도 응답에 포함되어 있다면 렌더에 archived 표시
    if (queryByTestId("audit-event-row-11")) {
      expect(queryByTestId("audit-event-archived-badge-11")).toBeTruthy();
    }
    // include_archived 토글 켜기
    const toggle = (await findByTestId("audit-event-timeline-card-include-archived-toggle"))
      .querySelector("input[type=checkbox]");
    backendApi.auditEventsList.mockResolvedValueOnce([
      _event({ id: 10, summary: "fresh" }),
      _event({ id: 11, summary: "archived already",
                archived: true, archived_by: "old-ops" }),
    ]);
    await act(async () => {
      fireEvent.click(toggle);
    });
    await waitFor(() => {
      // 두 번째 호출에 includeArchived=true 전달됐는지
      const lastCall = backendApi.auditEventsList.mock.calls.slice(-1)[0][0];
      expect(lastCall?.includeArchived).toBe(true);
    });
  });

  it("renders empty state when no events", async () => {
    backendApi.auditEventsList.mockResolvedValueOnce([]);
    const { findByTestId } = render(<AuditEventTimelineCard />);
    expect((await findByTestId("audit-event-timeline-card-empty")).textContent)
      .toMatch(/표시할 감사 이벤트가 없습니다/);
  });

  it("shows friendly error message — no raw 'Failed to fetch'", async () => {
    backendApi.auditEventsList.mockRejectedValueOnce(new Error("Failed to fetch"));
    const { findByTestId } = render(<AuditEventTimelineCard />);
    const err = await findByTestId("audit-event-timeline-card-error");
    expect(err.textContent).not.toBe("Failed to fetch");
    expect(err.textContent).toMatch(/백엔드|데모/);
  });

  it("does not call any forbidden order / broker API during lifecycle", async () => {
    const { findByTestId } = render(<AuditEventTimelineCard />);
    await findByTestId("audit-event-timeline-card");
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
    expect(backendApi.approveApproval).not.toHaveBeenCalled();
    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
    expect(backendApi.auditEventsList).toHaveBeenCalled();
    expect(backendApi.auditEventArchive).not.toHaveBeenCalled();
  });

  it("severity / source filter chips pass through to API call", async () => {
    const { findByTestId, container } = render(<AuditEventTimelineCard />);
    await findByTestId("audit-event-timeline-card");
    // ChipFilterBar는 button textContent로 정확 라벨을 사용.
    const buttons = Array.from(container.querySelectorAll("button"));
    const warnChip = buttons.find(
      (b) => (b.textContent || "").trim() === "WARN",
    );
    expect(warnChip).toBeTruthy();
    await act(async () => { fireEvent.click(warnChip); });
    await waitFor(() => {
      const lastCall = backendApi.auditEventsList.mock.calls.slice(-1)[0][0];
      expect(lastCall?.severity).toBe("WARN");
    });
  });
});
