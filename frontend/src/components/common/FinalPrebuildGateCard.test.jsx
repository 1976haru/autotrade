/**
 * 체크리스트 11-00 — FinalPrebuildGateCard 단위 테스트.
 *
 * lock: overall_status + exe_build_allowed + counts + 섹션 + blocked/warn + next +
 * error/empty fallback + 매수/매도/실전/승인/자동적용 버튼 0개 (새로고침/복사만) +
 * input 0개 + secret 미표시 + 필수 disclaimer.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { FinalPrebuildGateCard } from "./FinalPrebuildGateCard";

afterEach(cleanup);

function _report(over = {}) {
  return {
    overall_status: "BUILD_READY_WITH_WARNINGS",
    user_line: "EXE 빌드 가능하나 WARN 확인 필요",
    exe_build_allowed: true,
    ready_for_market_rehearsal: false,
    ready_for_paper_rehearsal: true,
    counts: { PASS: 13, WARN: 8, FAIL: 0, SKIP: 4 },
    sections: [
      { name: "CONFIG_ENV", status: "PASS", detail: "LIVE/AI/FUTURES=false" },
      { name: "SECURITY_SECRET_SCAN", status: "PASS", detail: "0 findings" },
      { name: "KIS_CREDENTIALS", status: "WARN", detail: "자격 미설정" },
      { name: "LIVE_SAFETY", status: "PASS", detail: "실전 OFF" },
    ],
    blocked_reasons: [],
    warn_reasons: ["KIS_CREDENTIALS: 자격 미설정"],
    next_actions: ["EXE 빌드 진행 가능 (위 WARN 검토 후)."],
    contains_secret: false,
    is_live_authorization: false,
    broker_order_sent: false,
    order_created: false,
    no_profit_guarantee: true,
    ...over,
  };
}

function _api(report = _report()) {
  return { finalPrebuildGate: vi.fn(async () => report) };
}

describe("<FinalPrebuildGateCard>", () => {
  it("overall_status + exe_build_allowed 표시", async () => {
    render(<FinalPrebuildGateCard apiClient={_api()} />);
    const st = await screen.findByTestId("final-prebuild-status");
    expect(st.textContent).toContain("BUILD_READY_WITH_WARNINGS");
    expect(screen.getByTestId("final-prebuild-exe-allowed").textContent).toContain("예");
  });

  it("counts 표시", async () => {
    render(<FinalPrebuildGateCard apiClient={_api()} />);
    const c = await screen.findByTestId("final-prebuild-counts");
    expect(c.textContent).toContain("PASS 13");
    expect(c.textContent).toContain("FAIL 0");
  });

  it("섹션별 상태 표시", async () => {
    render(<FinalPrebuildGateCard apiClient={_api()} />);
    await screen.findByTestId("final-prebuild-section-CONFIG_ENV");
    expect(screen.getByTestId("final-prebuild-section-LIVE_SAFETY").textContent).toContain("PASS");
  });

  it("WARN 목록 표시 (KIS 자격)", async () => {
    render(<FinalPrebuildGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("final-prebuild-warns")).textContent).toContain("KIS_CREDENTIALS");
  });

  it("BUILD_BLOCKED + blocked reasons 표시", async () => {
    render(<FinalPrebuildGateCard apiClient={_api(_report({
      overall_status: "BUILD_BLOCKED", exe_build_allowed: false,
      counts: { PASS: 5, WARN: 2, FAIL: 1, SKIP: 2 },
      blocked_reasons: ["CONFIG_ENV: 안전 플래그 위반"] }))} />);
    expect((await screen.findByTestId("final-prebuild-status")).textContent).toContain("BUILD_BLOCKED");
    expect(screen.getByTestId("final-prebuild-exe-allowed").textContent).toContain("아니오");
    expect(screen.getByTestId("final-prebuild-blocked").textContent).toContain("안전 플래그");
  });

  it("next actions 표시", async () => {
    render(<FinalPrebuildGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("final-prebuild-next")).textContent).toContain("EXE 빌드");
  });

  it("API 실패 fallback", async () => {
    render(<FinalPrebuildGateCard apiClient={{ finalPrebuildGate: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("final-prebuild-error")).toBeTruthy();
  });

  it("빈 응답 fallback", async () => {
    render(<FinalPrebuildGateCard apiClient={{ finalPrebuildGate: vi.fn(async () => null) }} />);
    expect(await screen.findByTestId("final-prebuild-empty")).toBeTruthy();
  });

  it("매수/매도/실전/승인/자동적용 버튼 0개 (점검·복사만)", async () => {
    const { container } = render(<FinalPrebuildGateCard apiClient={_api()} />);
    await screen.findByTestId("final-prebuild-status");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["점검 새로고침", "리포트 복사"]);
    const forbidden = /실전 전환|자동 적용|지금 매수|지금 매도|매수 실행|매도 실행|Place Order|승인 보내기/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea 0개", async () => {
    const { container } = render(<FinalPrebuildGateCard apiClient={_api()} />);
    await screen.findByTestId("final-prebuild-status");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("필수 disclaimer (실전 승인 아님 / 주문 실행 아님 / 수익 보장 아님)", async () => {
    const { container } = render(<FinalPrebuildGateCard apiClient={_api()} />);
    await screen.findByTestId("final-prebuild-status");
    const text = container.textContent;
    expect(text).toContain("실전 승인 아님");
    expect(text).toContain("주문 실행 아님");
    expect(text).toContain("수익 보장 아님");
  });

  it("secret/account-like 원문 미표시", async () => {
    const { container } = render(<FinalPrebuildGateCard apiClient={_api()} />);
    await screen.findByTestId("final-prebuild-status");
    expect(container.textContent).not.toMatch(/sk-[A-Za-z0-9]{20,}/);
    expect(container.textContent).not.toMatch(/\b\d{8}-\d{2}\b/);
  });
});
