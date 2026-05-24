/**
 * BUILD-02B-0 — KisPaperAiAutotradeAuditCard 단위 테스트.
 *
 * lock: paper_autotrade_ready / rehearsal + 섹션 PASS/WARN/FAIL + fake BUY/SELL/
 * HOLD + 실제 KIS API 없음 + 실전 승인 아님 + 매수/매도/주문/승인 버튼 없음 +
 * secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { KisPaperAiAutotradeAuditCard } from "./KisPaperAiAutotradeAuditCard";

afterEach(cleanup);

function _report({ ready = true, rehearsal = false, fail = false } = {}) {
  const sections = [
    { section: "ENV_READINESS", verdict: fail ? "FAIL" : "PASS", note: "안전 flag" },
    { section: "AGENT_COUNCIL", verdict: "PASS", note: "council" },
    { section: "PAPER_DECISION_BRIDGE", verdict: "PASS", note: "KIS_PAPER" },
    { section: "PAPER_ORDER_EXECUTOR", verdict: "PASS", note: "executor 계약" },
    { section: "BUY_SELL_LIMITS", verdict: "PASS", note: "게이트" },
    { section: "SELL_TRIGGER", verdict: "PASS", note: "보유 청산만" },
    { section: "FAKE_FLOWS", verdict: "PASS", note: "fake BUY/SELL/HOLD" },
    { section: "LIVE_SAFETY", verdict: "PASS", note: "실전 OFF" },
  ];
  return {
    generated_at: "2026-05-24T01:00:00+00:00", sections,
    counts: { PASS: fail ? 7 : 8, WARN: 0, FAIL: fail ? 1 : 0, SKIP: 0 },
    overall_verdict: fail ? "FAIL" : "PASS",
    paper_autotrade_ready: fail ? false : ready,
    ready_for_market_open_rehearsal: fail ? false : rehearsal,
    fail_items: fail ? ["ENV_READINESS/KIS_IS_PAPER"] : [],
    warn_items: [], reason_codes: [],
    is_live_authorization: false, broker_order_sent: false, order_created: false,
    contains_secret: false,
  };
}

function _api(report = _report()) {
  return { kisPaperAutotradeAudit: vi.fn(async () => report) };
}

describe("<KisPaperAiAutotradeAuditCard>", () => {
  it("paper_autotrade_ready 표시", async () => {
    render(<KisPaperAiAutotradeAuditCard apiClient={_api()} />);
    expect((await screen.findByTestId("kis-audit-ready")).textContent).toContain("예");
  });

  it("ready_for_market_open_rehearsal 표시", async () => {
    render(<KisPaperAiAutotradeAuditCard apiClient={_api(_report({ rehearsal: true }))} />);
    expect((await screen.findByTestId("kis-audit-rehearsal")).textContent).toContain("예");
  });

  it("섹션별 PASS 표시 + fake BUY/SELL/HOLD 섹션", async () => {
    render(<KisPaperAiAutotradeAuditCard apiClient={_api()} />);
    await screen.findByTestId("kis-audit-section-AGENT_COUNCIL");
    expect(screen.getByTestId("kis-audit-section-FAKE_FLOWS").textContent).toContain("PASS");
    expect(screen.getByTestId("kis-audit-section-SELL_TRIGGER").textContent).toContain("보유 청산");
    expect(screen.getByTestId("kis-audit-section-PAPER_DECISION_BRIDGE").textContent).toContain("KIS_PAPER");
  });

  it("FAIL 시 paper_autotrade_ready 아니오 + fail reason", async () => {
    render(<KisPaperAiAutotradeAuditCard apiClient={_api(_report({ fail: true }))} />);
    expect((await screen.findByTestId("kis-audit-ready")).textContent).toContain("아니오");
    expect(screen.getByTestId("kis-audit-fail-reasons").textContent).toContain("ENV_READINESS");
  });

  it("실제 KIS API 없음 / 실전 승인 아님 문구", async () => {
    render(<KisPaperAiAutotradeAuditCard apiClient={_api()} />);
    const intro = (await screen.findByTestId("kis-audit-intro")).textContent;
    expect(intro).toContain("실제 KIS API 호출 없음");
    expect(intro).toContain("실전 승인 아님");
    expect(intro).toContain("주문 버튼이 아닙니다");
  });

  it("매수/매도/주문/실전/승인 버튼 없음 (새로고침/복사만)", async () => {
    const { container } = render(<KisPaperAiAutotradeAuditCard apiClient={_api()} />);
    await screen.findByTestId("kis-audit-overall");
    const btns = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(btns).toEqual(["점검 새로고침", "결과 복사"]);
    for (const f of ["지금 매수", "지금 매도", "주문 실행", "실전 켜기", "승인하기", "Place Order"]) {
      expect(container.textContent).not.toContain(f);
    }
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<KisPaperAiAutotradeAuditCard apiClient={_api()} />);
    await screen.findByTestId("kis-audit-overall");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });
});
