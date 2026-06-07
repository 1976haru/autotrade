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
  it("P1: all_ok=true → '✅ 출발 준비 완료' (정확 문구), 항목 안내 없음", async () => {
    const { getByTestId, queryByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => okData) }} />);
    await waitFor(() => expect(getByTestId("preflight-ready").textContent).toContain("출발 준비 완료"));
    expect(queryByTestId("preflight-issues")).toBeNull();   // OK면 "확인 필요" 문구 없음
  });

  it("P1: all_ok=false → '⚠ 확인이 필요한 항목 N개' (N=FAIL/WARN 수)", async () => {
    const { getByTestId, queryByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => issueData) }} />);
    await waitFor(() => expect(getByTestId("preflight-issues").textContent).toContain("확인이 필요한 항목 2개"));
    expect(queryByTestId("preflight-ready")).toBeNull();    // FAIL이면 "준비 완료" 문구 없음
  });

  it("FAIL/WARN → 항목별 한국어 안내 자동 표시", async () => {
    const { getByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => issueData) }} />);
    await waitFor(() => expect(getByTestId("preflight-issues").textContent).toContain("확인이 필요한 항목 2개"));
    expect(getByTestId("preflight-item-kis").textContent).toContain("잔고 조회 실패");
    expect(getByTestId("preflight-item-emergency_stop").textContent).toContain("주문이 차단");
  });

  it("②-fetch실패: throw → '불러오지 못했어요' + 재시도(빈 상태/0개 아님)", async () => {
    const { getByTestId, queryByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => { throw new Error("x"); }) }} />);
    await waitFor(() => expect(getByTestId("preflight-fail").textContent).toContain("불러오지 못했어요"));
    expect(getByTestId("preflight-fail").textContent).toContain("재시도");
    expect(queryByTestId("preflight-issues")).toBeNull();   // "0개" 둔갑 금지
  });

  it.each([
    ["빈 객체", {}],
    ["items 없음", { all_ok: true }],
    ["all_ok 없음(프록시 누락 SPA류)", { items: [{ key: "x", status: "ok" }] }],
    ["null", null],
  ])("②-비정상응답(%s) → '불러오지 못했어요'(⚠ 0개 금지)", async (_n, bad) => {
    const { getByTestId, queryByTestId } = render(<PreflightPanel api={{ preflight: vi.fn(async () => bad) }} />);
    await waitFor(() => expect(getByTestId("preflight-fail")).toBeTruthy());
    expect(queryByTestId("preflight-issues")).toBeNull();   // ★"확인이 필요한 항목 0개" 안 뜸
    expect(queryByTestId("preflight-ready")).toBeNull();
  });

  it("①-재시도: 실패 후 ↻ 클릭 → 재fetch → 성공 시 ✅, 호출 2회", async () => {
    const preflight = vi.fn().mockRejectedValueOnce(new Error("transient")).mockResolvedValue(okData);
    const { getByTestId } = render(<PreflightPanel api={{ preflight }} />);
    await waitFor(() => expect(getByTestId("preflight-fail")).toBeTruthy());
    fireEvent.click(getByTestId("preflight-refresh"));
    await waitFor(() => expect(getByTestId("preflight-ready")).toBeTruthy());
    expect(preflight).toHaveBeenCalledTimes(2);   // ↻ 가 실제 재fetch
  });

  it("①-재시도: 반복 실패해도 누를 때마다 '시도했음'(호출 증가)", async () => {
    const preflight = vi.fn().mockRejectedValue(new Error("down"));
    const { getByTestId } = render(<PreflightPanel api={{ preflight }} />);
    await waitFor(() => expect(getByTestId("preflight-fail")).toBeTruthy());
    fireEvent.click(getByTestId("preflight-refresh"));
    await waitFor(() => expect(preflight).toHaveBeenCalledTimes(2));
    fireEvent.click(getByTestId("preflight-refresh"));
    await waitFor(() => expect(preflight).toHaveBeenCalledTimes(3));
    expect(getByTestId("preflight-fail")).toBeTruthy();   // 여전히 실패 정직 표시
  });

  it("②-로딩: 응답 전 '확인 중…'", async () => {
    let resolve;
    const { getByTestId } = render(<PreflightPanel api={{ preflight: () => new Promise((r) => { resolve = r; }) }} />);
    expect(getByTestId("preflight-loading")).toBeTruthy();
    resolve(okData);
    await waitFor(() => expect(getByTestId("preflight-ready")).toBeTruthy());
  });
});
