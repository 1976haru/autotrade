/**
 * BUILD-01 — ProgramIntegrityGateCard 단위 테스트.
 *
 * lock: 전체 PASS/WARN/FAIL + build_ready + 섹션별 상태 + fail/warn reason +
 * live safety 표시 + 주문/실전/승인 버튼 없음 (새로고침/복사만) + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ProgramIntegrityGateCard } from "./ProgramIntegrityGateCard";

afterEach(cleanup);

function _report({ overall = "WARN", build_ready = true, fail = false } = {}) {
  const sections = [
    { section: "UNIVERSE", verdict: "PASS", reason_code: "NO_USER_WATCHLIST", detail: "기본 50", evidence: {} },
    { section: "KIS_PAPER_READINESS", verdict: "WARN", reason_code: "KIS_CREDENTIALS_MISSING", detail: "자격 미설정", evidence: {} },
    { section: "AGENT_COUNCIL", verdict: "PASS", reason_code: "COUNCIL_OK", detail: "BUY", evidence: {} },
    { section: "QUALITY_GATE", verdict: "PASS", reason_code: "QUALITY_GATE_OK", detail: "ok", evidence: {} },
    { section: "KIS_PAPER_DECISION", verdict: "PASS", reason_code: "KIS_PAPER_DECISION_OK", detail: "KIS_PAPER", evidence: {} },
    { section: "LIVE_SAFETY", verdict: fail ? "FAIL" : "PASS",
      reason_code: fail ? "LIVE_SAFETY_VIOLATION" : "LIVE_SAFETY_OK", detail: "기본 OFF", evidence: {} },
  ];
  return {
    generated_at: "2026-05-24T01:00:00+00:00", sections,
    counts: { PASS: fail ? 4 : 5, WARN: 1, FAIL: fail ? 1 : 0, SKIP: 0 },
    overall_verdict: fail ? "FAIL" : overall, build_ready: fail ? false : build_ready,
    reason_code: fail ? "BUILD_BLOCKED_BY_FAIL" : "BUILD_READY_WITH_WARNINGS",
    is_live_authorization: false, broker_order_sent: false, order_created_live: false,
    contains_secret: false,
  };
}

function _api(report = _report()) {
  return { programIntegrity: vi.fn(async () => report) };
}

describe("<ProgramIntegrityGateCard>", () => {
  it("전체 판정 + build_ready 표시", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("integrity-overall")).textContent).toContain("WARN");
    expect(screen.getByTestId("integrity-build-ready").textContent).toContain("예");
  });

  it("섹션별 상태 표시", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api()} />);
    await screen.findByTestId("integrity-section-UNIVERSE");
    expect(screen.getByTestId("integrity-section-AGENT_COUNCIL").textContent).toContain("PASS");
    expect(screen.getByTestId("integrity-section-LIVE_SAFETY").textContent).toContain("PASS");
  });

  it("WARN reason 표시", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("integrity-warn-reasons")).textContent).toContain("KIS_PAPER_READINESS");
  });

  it("FAIL 시 build_ready 아니오 + fail reason", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api(_report({ fail: true }))} />);
    expect((await screen.findByTestId("integrity-build-ready")).textContent).toContain("아니오");
    expect(screen.getByTestId("integrity-fail-reasons").textContent).toContain("LIVE_SAFETY");
  });

  it("live safety 섹션 표시", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("integrity-section-LIVE_SAFETY"))).toBeTruthy();
  });

  it("표시 전용/실전 승인 아님 문구", async () => {
    render(<ProgramIntegrityGateCard apiClient={_api()} />);
    const intro = (await screen.findByTestId("integrity-intro")).textContent;
    expect(intro).toContain("최종 빌드 전 점검용");
    expect(intro).toContain("실전 승인이 아니며");
    expect(intro).toContain("주문 버튼이 아닙니다");
  });

  it("주문/실전/승인 버튼 없음 (새로고침/복사만 허용)", async () => {
    const { container } = render(<ProgramIntegrityGateCard apiClient={_api()} />);
    await screen.findByTestId("integrity-overall");
    // 버튼은 새로고침/복사 2개만.
    const btns = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(btns).toEqual(["점검 새로고침", "리포트 복사"]);
    for (const f of ["실전 켜기", "주문 실행", "지금 매수", "지금 매도", "승인하기", "LIVE ON", "Place Order"]) {
      expect(container.textContent).not.toContain(f);
    }
    // 입력 form 없음.
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<ProgramIntegrityGateCard apiClient={_api()} />);
    await screen.findByTestId("integrity-overall");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });
});
