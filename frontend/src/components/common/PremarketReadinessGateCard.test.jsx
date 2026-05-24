/**
 * BUILD-02A — PremarketReadinessGateCard 단위 테스트.
 *
 * lock: overall_status / premarket_ready / rehearsal + 섹션 PASS/WARN/FAIL +
 * KIS creds WARN + BUILD-02B 안내 + 실제 KIS API 없음 + 실전 승인 아님 +
 * 주문/매수/매도/승인 버튼 없음 + secret 미표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { PremarketReadinessGateCard } from "./PremarketReadinessGateCard";

afterEach(cleanup);

function _report({ overall = "WARN", premarket_ready = true, rehearsal = false,
                  kisWarn = true, fail = false } = {}) {
  const sections = [
    { section: "ENV_READINESS", verdict: fail ? "FAIL" : "PASS", note: "안전 플래그" },
    { section: "KIS_CREDENTIALS", verdict: kisWarn ? "WARN" : "PASS", note: "present 여부" },
    { section: "PAPER_LIVE_SEPARATION", verdict: "PASS", note: "분리" },
    { section: "PROGRAM_INTEGRITY", verdict: "PASS", note: "BUILD-01" },
    { section: "PREFLIGHT_SMOKE", verdict: "PASS", note: "preflight" },
    { section: "DOCS_RUNBOOK", verdict: "PASS", note: "문서" },
  ];
  return {
    generated_at: "2026-05-24T01:00:00+00:00", mode: "fast", sections,
    counts: { PASS: 5, WARN: kisWarn ? 1 : 0, FAIL: fail ? 1 : 0, SKIP: 0 },
    overall_status: fail ? "FAIL" : overall,
    premarket_ready: fail ? false : premarket_ready,
    build_ready_for_offline: fail ? false : true,
    ready_for_market_open_rehearsal: fail ? false : rehearsal,
    missing_items: [], warn_items: kisWarn ? ["KIS_CREDENTIALS/kis_credentials_present"] : [],
    fail_items: fail ? ["ENV_READINESS/ENABLE_LIVE_TRADING"] : [],
    full_mode_command_plan: [],
    is_live_authorization: false, broker_order_sent: false, order_created: false,
    contains_secret: false,
  };
}

function _api(report = _report()) {
  return { premarketReadiness: vi.fn(async () => report) };
}

describe("<PremarketReadinessGateCard>", () => {
  it("overall_status + premarket_ready 표시", async () => {
    render(<PremarketReadinessGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("premarket-overall")).textContent).toContain("WARN");
    expect(screen.getByTestId("premarket-ready").textContent).toContain("예");
  });

  it("ready_for_market_open_rehearsal 표시", async () => {
    render(<PremarketReadinessGateCard apiClient={_api(_report({ rehearsal: true, kisWarn: false }))} />);
    expect((await screen.findByTestId("premarket-rehearsal")).textContent).toContain("예");
  });

  it("섹션별 PASS/WARN 표시", async () => {
    render(<PremarketReadinessGateCard apiClient={_api()} />);
    await screen.findByTestId("premarket-section-ENV_READINESS");
    expect(screen.getByTestId("premarket-section-ENV_READINESS").textContent).toContain("PASS");
    expect(screen.getByTestId("premarket-section-KIS_CREDENTIALS").textContent).toContain("WARN");
  });

  it("FAIL 시 premarket_ready 아니오", async () => {
    render(<PremarketReadinessGateCard apiClient={_api(_report({ fail: true }))} />);
    expect((await screen.findByTestId("premarket-ready")).textContent).toContain("아니오");
    expect(screen.getByTestId("premarket-section-ENV_READINESS").textContent).toContain("FAIL");
  });

  it("KIS credentials missing WARN 안내", async () => {
    render(<PremarketReadinessGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("premarket-kis-warn")).textContent).toContain("KIS 자격");
  });

  it("장중 BUILD-02B 안내 표시", async () => {
    render(<PremarketReadinessGateCard apiClient={_api()} />);
    expect((await screen.findByTestId("premarket-next")).textContent).toContain("BUILD-02B");
  });

  it("실제 KIS API 없음 / 실전 승인 아님 문구", async () => {
    render(<PremarketReadinessGateCard apiClient={_api()} />);
    const intro = (await screen.findByTestId("premarket-intro")).textContent;
    expect(intro).toContain("실제 KIS API 호출 없음");
    expect(intro).toContain("실전 승인 아님");
    expect(intro).toContain("주문 버튼이 아닙니다");
  });

  it("주문/매수/매도/승인 버튼 없음 (새로고침/복사만)", async () => {
    const { container } = render(<PremarketReadinessGateCard apiClient={_api()} />);
    await screen.findByTestId("premarket-overall");
    const btns = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(btns).toEqual(["점검 새로고침", "결과 복사"]);
    for (const f of ["실전 켜기", "모의 주문 실행", "지금 매수", "지금 매도", "승인하기", "LIVE ON"]) {
      expect(container.textContent).not.toContain(f);
    }
    expect(container.querySelectorAll("input, textarea, select").length).toBe(0);
  });

  it("secret/account 미표시", async () => {
    const { container } = render(<PremarketReadinessGateCard apiClient={_api()} />);
    await screen.findByTestId("premarket-overall");
    for (const f of ["sk-ant-", "Bearer ", "access_token", "kis_app_secret"]) {
      expect(container.textContent).not.toContain(f);
    }
  });
});
