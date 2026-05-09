import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AgentMemoryCard } from "./AgentMemoryCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    memorySearch:  vi.fn(),
    memoryGet:     vi.fn(),
    memoryCreate:  vi.fn(),
    memoryArchive: vi.fn(),
  },
}));


const _ITEM_BASE = {
  created_at: "2026-05-09T12:00:00+00:00",
  updated_at: "2026-05-09T12:00:00+00:00",
  source_kind: "operator",
  source_id: null,
  mode: "SIMULATION",
  meta: {},
  author: null,
  archived: false,
  is_order_signal: false,
};

const _ITEMS = [
  {
    ..._ITEM_BASE, id: 1, memory_type: "operator_note",
    severity: "INFO",
    title: "삼성전자 변동성 메모",
    summary: "장초반 30분 변동성이 큰 날 진입을 주의.",
    lessons: "VWAP 진입 자제",
    next_action: null,
    strategy: "vwap", symbol: "005930",
    tags: ["operator", "open-30m"],
  },
  {
    ..._ITEM_BASE, id: 2, memory_type: "risk_incident",
    severity: "CRITICAL",
    title: "Risk Audit — RED",
    summary: "긴급정지 권고",
    lessons: null,
    next_action: "EMERGENCY_STOP_RECOMMENDED — 운영자 수동 토글 검토",
    strategy: null, symbol: null,
    tags: ["risk_audit", "red"],
  },
];


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.memorySearch.mockResolvedValue({
    items: _ITEMS,
    notice: "본 결과는 주문 신호가 아닙니다.",
  });
  backendApi.memoryArchive.mockResolvedValue({
    ..._ITEMS[0], archived: true,
  });
  backendApi.memoryCreate.mockResolvedValue({
    ..._ITEM_BASE, id: 99, memory_type: "operator_note",
    severity: "INFO", title: "신규 메모", summary: "본문",
    lessons: null, next_action: null,
    strategy: null, symbol: null, tags: [],
  });
});


describe("<AgentMemoryCard>", () => {
  it("renders '주문 신호 아님 · 과거 학습 기록' badge", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    const badge = await findByTestId("agent-memory-not-order-badge");
    expect(badge.textContent).toMatch(/주문 신호 아님|과거 학습/);
  });

  it("renders disclaimer notice", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    const notice = await findByTestId("agent-memory-notice");
    expect(notice.textContent).toMatch(/주문 신호가 아닙니다/);
    expect(notice.textContent).toMatch(/BUY\/SELL\/HOLD/);
    expect(notice.textContent).toMatch(/자동 주문|승인 큐 등록.*사용되지 않/);
  });

  it("calls memorySearch on mount and renders items", async () => {
    const { findByTestId, getAllByTestId } = render(<AgentMemoryCard />);
    await waitFor(() => expect(backendApi.memorySearch).toHaveBeenCalled());
    const list = await findByTestId("agent-memory-list");
    expect(list).toBeTruthy();
    const rows = getAllByTestId(/^agent-memory-row-/);
    expect(rows.length).toBe(2);
  });

  it("renders item title, type, strategy, symbol, summary, tags", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    const row = await findByTestId("agent-memory-row-1");
    expect(row.textContent).toContain("삼성전자 변동성 메모");
    expect(row.textContent).toContain("operator_note");
    expect(row.textContent).toContain("vwap");
    expect(row.textContent).toContain("005930");
    expect(row.textContent).toContain("장초반 30분");
    const tags = await findByTestId("agent-memory-tags-1");
    expect(tags.textContent).toContain("operator");
    expect(tags.textContent).toContain("open-30m");
  });

  it("renders severity color (CRITICAL = red)", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    const row = await findByTestId("agent-memory-row-2");
    expect(row.textContent).toContain("CRITICAL");
  });

  it("does NOT render BUY/SELL/즉시 주문 buttons", async () => {
    const { container, findByTestId } = render(<AgentMemoryCard />);
    await findByTestId("agent-memory-list");
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      expect(text).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행|즉시 주문/);
      expect(text).not.toMatch(/Place Order|Submit Order/i);
      // approval queue로 보내는 버튼도 본 카드에서는 만들지 않음.
      expect(text).not.toMatch(/승인 큐.*보내|승인 대기.*보내/);
    }
  });

  it("opens detail view on 상세 click", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    fireEvent.click(await findByTestId("agent-memory-open-1"));
    const detail = await findByTestId("agent-memory-detail");
    expect(detail.textContent).toContain("삼성전자 변동성 메모");
    expect(detail.textContent).toContain("VWAP 진입 자제");  // lessons
  });

  it("archive button calls memoryArchive", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    fireEvent.click(await findByTestId("agent-memory-archive-1"));
    await waitFor(() =>
      expect(backendApi.memoryArchive).toHaveBeenCalledWith(1, true),
    );
  });

  it("search input filters via memorySearch", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    await waitFor(() => expect(backendApi.memorySearch).toHaveBeenCalled());
    backendApi.memorySearch.mockClear();
    const input = await findByTestId("agent-memory-search-input");
    fireEvent.change(input, { target: { value: "VWAP" } });
    await waitFor(() => expect(backendApi.memorySearch).toHaveBeenCalled());
    const last = backendApi.memorySearch.mock.calls.at(-1)[0];
    expect(last.keyword).toBe("VWAP");
  });

  it("type filter narrows search", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    await waitFor(() => expect(backendApi.memorySearch).toHaveBeenCalled());
    backendApi.memorySearch.mockClear();
    const sel = await findByTestId("agent-memory-type-filter");
    fireEvent.change(sel, { target: { value: "risk_incident" } });
    await waitFor(() => expect(backendApi.memorySearch).toHaveBeenCalled());
    const last = backendApi.memorySearch.mock.calls.at(-1)[0];
    expect(last.memory_type).toBe("risk_incident");
  });

  it("operator note add form submits and refreshes", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    await findByTestId("agent-memory-list");
    fireEvent.change(
      await findByTestId("agent-memory-add-title"),
      { target: { value: "신규 메모" } },
    );
    fireEvent.change(
      await findByTestId("agent-memory-add-summary"),
      { target: { value: "본문" } },
    );
    fireEvent.click(await findByTestId("agent-memory-add-submit"));
    await waitFor(() => expect(backendApi.memoryCreate).toHaveBeenCalled());
    const lastCall = backendApi.memoryCreate.mock.calls.at(-1)[0];
    expect(lastCall.title).toBe("신규 메모");
    expect(lastCall.summary).toBe("본문");
  });

  it("renders Secret 입력 금지 안내 in add form", async () => {
    const { findByTestId } = render(<AgentMemoryCard />);
    const form = await findByTestId("agent-memory-add-form");
    expect(form.textContent).toMatch(/API key.*Secret.*계좌번호.*개인정보.*입력 금지/);
  });

  it("shows error when create returns secret_leak (string error)", async () => {
    backendApi.memoryCreate.mockRejectedValue(new Error(
      "AgentMemory summary contains forbidden pattern 'email'.",
    ));
    const { findByTestId } = render(<AgentMemoryCard />);
    await findByTestId("agent-memory-list");
    fireEvent.change(await findByTestId("agent-memory-add-title"),
                     { target: { value: "x" } });
    fireEvent.change(await findByTestId("agent-memory-add-summary"),
                     { target: { value: "email user@example.com" } });
    fireEvent.click(await findByTestId("agent-memory-add-submit"));
    const err = await findByTestId("agent-memory-add-error");
    expect(err.textContent).toMatch(/forbidden pattern/);
  });

  it("renders empty state when no items", async () => {
    backendApi.memorySearch.mockResolvedValue({ items: [], notice: "" });
    const { findByTestId } = render(<AgentMemoryCard />);
    const empty = await findByTestId("agent-memory-empty");
    expect(empty.textContent).toMatch(/저장된 메모리가 없습니다/);
  });

  it("compact mode hides type filter and add form", async () => {
    const { findByTestId, queryByTestId } = render(<AgentMemoryCard compact />);
    await findByTestId("agent-memory-not-order-badge");
    expect(queryByTestId("agent-memory-type-filter")).toBeNull();
    expect(queryByTestId("agent-memory-add-form")).toBeNull();
  });
});
