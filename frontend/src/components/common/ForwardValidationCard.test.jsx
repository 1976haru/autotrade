/**
 * ForwardValidationCard 테스트 — 4상태 + verdict + EXE 권고 + 실전금지 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ForwardValidationCard } from "./ForwardValidationCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    final_forward_verdict: "FORWARD_WATCH",
    rule_locked_before_validation: true,
    exe_rebuild_recommendation: "EXE 재빌드 가능 — 모의 리허설 준비 가능, 단 자동주문은 dry-run 우선",
    paper_rehearsal_recommendation: "Paper 리허설 아직 불가 — forward 검증 보강 필요",
    live_trading_recommendation: false,
    best: { best_candidate: "A_top10_riskveto_daily", forward_return_pct: 3.5, median_pf: 1.12,
            forward_mdd_pct: 14.0, total_trades: 130, worst_month_defended: true },
    candidate_scores: {
      A_top10_riskveto_daily: { verdict: "FORWARD_WATCH", forward_return_pct: 3.5, median_pf: 1.12,
                                forward_mdd_pct: 14.0, total_trades: 130 },
      D_agent_off_pure: { verdict: "FORWARD_FAIL", forward_return_pct: -8.0, median_pf: 0.85,
                          forward_mdd_pct: 25.0, total_trades: 400 },
    },
    worst_month_holdout: { worst_month: "2026-03", baseline_worst_month_return: -23.0,
                           A_top10_riskveto_daily: { holdout_return_pct: -3.0, defended: true } },
    agent_forward: {
      AGENT_OFF: { forward_return_pct: -10.0 },
      AGENT_RISK_VETO_ONLY: { forward_return_pct: 3.0 },
    },
    overfit_warning: { warning: false },
    conclusions: ["forward 에서도 양(+) 유지", "Agent RISK_VETO 안정"],
    next_steps: ["추가 기간 재검증"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { forwardValidationLatest: vi.fn(async () => report) };
}

describe("<ForwardValidationCard>", () => {
  it("완료: verdict + best + EXE 권고 + 실전금지", async () => {
    render(<ForwardValidationCard apiClient={_api()} />);
    await screen.findByTestId("fv-verdict");
    expect(screen.getByTestId("fv-final-verdict").textContent).toContain("FORWARD_WATCH");
    expect(screen.getByTestId("fv-best").textContent).toContain("A_top10_riskveto_daily");
    expect(screen.getByTestId("fv-exe-rec").textContent).toContain("모의 리허설");
    expect(screen.getByTestId("fv-live").textContent).toContain("false");
  });

  it("후보별 + holdout + agent forward 표시", async () => {
    render(<ForwardValidationCard apiClient={_api()} />);
    await screen.findByTestId("fv-verdict");
    expect(screen.getByTestId("fv-candidates").textContent).toContain("D_agent_off_pure");
    expect(screen.getByTestId("fv-holdout").textContent).toContain("2026-03");
    expect(screen.getByTestId("fv-agent").textContent).toContain("RISK_VETO");
  });

  it("데이터 없음", async () => {
    render(<ForwardValidationCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("fv-empty")).toBeTruthy();
    expect(screen.queryByTestId("fv-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { forwardValidationLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<ForwardValidationCard apiClient={api} />);
    expect(await screen.findByTestId("fv-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<ForwardValidationCard apiClient={_api()} />);
    await screen.findByTestId("fv-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<ForwardValidationCard apiClient={_api()} />);
    await screen.findByTestId("fv-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<ForwardValidationCard apiClient={_api()} />);
    await screen.findByTestId("fv-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("PAPER_REHEARSAL_CONFIRMED verdict 표시", async () => {
    render(<ForwardValidationCard apiClient={_api(_report({
      final_forward_verdict: "PAPER_REHEARSAL_CONFIRMED",
      paper_rehearsal_recommendation: "KIS 모의매매 리허설 후보 (forward 통과, 단 실전 금지)" }))} />);
    await screen.findByTestId("fv-verdict");
    expect(screen.getByTestId("fv-final-verdict").textContent).toContain("PAPER_REHEARSAL_CONFIRMED");
    expect(screen.getByTestId("fv-live").textContent).toContain("false");
  });
});
