import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { PreflightPanel } from "./PreflightPanel";

afterEach(() => cleanup());

const okData = {
  all_ok: true, summary: "출발 준비 완료",
  items: [
    { key: "env", label: "키/환경(.env)", status: "ok", detail: "키지문 63b3744d04eba500" },
    { key: "kis", label: "증권사(KIS) 연결", status: "ok", detail: "예수금 100,000,000원" },
    { key: "emergency_stop", label: "긴급정지", status: "ok", detail: "꺼짐(정상)" },
  ],
};
const issueData = {
  all_ok: false, summary: "확인이 필요한 항목이 있어요",
  items: [
    { key: "kis", label: "증권사(KIS) 연결", status: "fail", detail: "잔고 조회 실패" },
    { key: "emergency_stop", label: "긴급정지", status: "warn", detail: "켜짐 — 주문이 차단돼 있어요" },
  ],
};

describe("PreflightPanel (V8)", () => {
  it("전부 OK → '출발 준비 완료 ✓'", async () => {
    const { getByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => okData) }} />);
    await waitFor(() => expect(getByTestId("preflight-ready")).toBeTruthy());
  });

  it("FAIL/WARN → 항목별 한국어 안내 자동 표시", async () => {
    const { getByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => issueData) }} />);
    await waitFor(() => expect(getByTestId("preflight-issues").textContent).toContain("확인이 필요한 항목 2개"));
    expect(getByTestId("preflight-item-kis").textContent).toContain("잔고 조회 실패");
    expect(getByTestId("preflight-item-emergency_stop").textContent).toContain("주문이 차단");
  });

  it("조회 실패 → 정직 표시(빈 상태 아님)", async () => {
    const { getByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => { throw new Error("x"); }) }} />);
    await waitFor(() => expect(getByTestId("preflight-fail")).toBeTruthy());
  });
});
