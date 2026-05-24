/**
 * #63 / 8-01 — PreflightSmokeCard 단위 테스트.
 *
 * lock 하는 invariant:
 *  - PASS / WARN / FAIL 요약 count 표시
 *  - 주요 FAIL 항목 표시
 *  - "주문을 발생시키지 않습니다" 문구
 *  - secret / 계좌번호 원문 표시 0건
 *  - 매수/매도/실거래/Place Order 버튼 0개, 입력 form 0개
 *  - 주문을 발생시키는 API 호출 0건 (preflight GET 만)
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PreflightSmokeCard } from "./PreflightSmokeCard";


afterEach(cleanup);


function _report(over = {}) {
  return {
    summary: { status: "PASS", pass_count: 10, warn_count: 0, fail_count: 0, total: 10 },
    checks: [
      { name: "backend_health", status: "PASS", message: "ok" },
      { name: "db_status", status: "PASS", message: "ok" },
      { name: "safety_flags", status: "PASS", message: "ok" },
    ],
    generated_at: "2026-05-24T00:00:00+00:00",
    is_live_authorization: false,
    contains_secret: false,
    ...over,
  };
}

function _api(report = _report()) {
  return { preflight: vi.fn(async () => report) };
}


describe("<PreflightSmokeCard>", () => {
  it("PASS 요약 표시", async () => {
    render(<PreflightSmokeCard apiClient={_api()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("preflight-overall")).getAttribute("data-status"))
      .toBe("PASS");
  });

  it("summary count (PASS/WARN/FAIL) 표시", async () => {
    const api = _api(_report({
      summary: { status: "WARN", pass_count: 8, warn_count: 2, fail_count: 0, total: 10 },
    }));
    render(<PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    expect((await screen.findByTestId("preflight-pass-count")).textContent).toContain("8");
    expect(screen.getByTestId("preflight-warn-count").textContent).toContain("2");
    expect(screen.getByTestId("preflight-fail-count").textContent).toContain("0");
  });

  it("WARN 표시", async () => {
    const api = _api(_report({
      summary: { status: "WARN", pass_count: 9, warn_count: 1, fail_count: 0, total: 10 },
    }));
    render(<PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    expect((await screen.findByTestId("preflight-overall")).getAttribute("data-status"))
      .toBe("WARN");
  });

  it("FAIL 표시 + 주요 실패 항목", async () => {
    const api = _api(_report({
      summary: { status: "FAIL", pass_count: 8, warn_count: 0, fail_count: 2, total: 10 },
      checks: [
        { name: "safety_flags", status: "FAIL", message: "live on" },
        { name: "db_status", status: "FAIL", message: "db down" },
      ],
    }));
    render(<PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    expect((await screen.findByTestId("preflight-overall")).getAttribute("data-status"))
      .toBe("FAIL");
    const fails = screen.getByTestId("preflight-fails").textContent;
    expect(fails).toContain("safety_flags");
    expect(fails).toContain("db_status");
  });

  it("개별 check 상태 표시", async () => {
    render(<PreflightSmokeCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    expect(screen.getByTestId("preflight-check-db_status").getAttribute("data-status"))
      .toBe("PASS");
  });

  it("'주문을 발생시키지 않습니다' 문구 표시", async () => {
    render(<PreflightSmokeCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    expect(screen.getByTestId("preflight-note").textContent)
      .toContain("주문을 발생시키지 않습니다");
  });

  it("backend 실패 시 안전 안내 (crash 없음)", async () => {
    const api = { preflight: vi.fn(async () => { throw new Error("offline"); }) };
    render(<PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    expect(await screen.findByTestId("preflight-error")).toBeTruthy();
  });
});


describe("<PreflightSmokeCard> — safety invariants", () => {
  it("매수/매도/실거래/Place Order 버튼 0개", async () => {
    const { container } = render(
      <PreflightSmokeCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    expect(container.querySelectorAll("button").length).toBe(0);
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING",
    ]) {
      expect(text).not.toContain(banned);
    }
  });

  it("입력 form(input/textarea/select) 0개", async () => {
    const { container } = render(
      <PreflightSmokeCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("주문 발생 API 호출 0건 — preflight 만 호출", async () => {
    const api = _api();
    render(<PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    expect(api.preflight).toHaveBeenCalled();
    // api 객체에 주문 메서드를 넣지 않았고, 카드는 preflight 만 호출.
    expect(Object.keys(api)).toEqual(["preflight"]);
  });

  it("secret / 계좌번호 원문이 응답에 있어도 표시되지 않음", async () => {
    const api = _api(_report({
      checks: [{ name: "db_status", status: "PASS", message: "ok" }],
      leaked_secret: "sk-ant-SHOULDNOTSHOW123456",
      leaked_account: "98765432-11",
    }));
    const { container } = render(
      <PreflightSmokeCard apiClient={api} pollIntervalMs={0} />);
    await screen.findByTestId("preflight-overall");
    const text = container.textContent || "";
    expect(text).not.toContain("sk-ant-SHOULDNOTSHOW123456");
    expect(text).not.toContain("98765432");
  });
});
