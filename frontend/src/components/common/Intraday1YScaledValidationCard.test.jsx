/**
 * Intraday1YScaledValidationCard 테스트 — 상태/verdict/단계/실전금지 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Intraday1YScaledValidationCard } from "./Intraday1YScaledValidationCard";

afterEach(cleanup);

function _st(ret, pf, mdd, tr, v) {
  return { forward_return_pct: ret, median_pf: pf, forward_mdd_pct: mdd, total_trades: tr, verdict: v };
}

function _report(overrides = {}) {
  return {
    available: true,
    final_verdict: "SCALE_WATCH",
    locked_rule_name: "FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1",
    rule_hash_match: true, no_parameter_change: true, no_look_ahead: true,
    trading_days: 245,
    data_quality: { quality_status: "PASS", symbol_count: 50, total_trading_days: 245 },
    exe_rebuild_recommendation: "관찰용 EXE 재빌드 가능, 자동매매/모의주문 비활성",
    paper_rehearsal_recommendation: "Paper 리허설 아직 불가 — 추가 검증/데이터 필요",
    live_trading_recommendation: false, real_order_allowed: false, dry_run_required: true,
    stage_10: _st(7.0, 1.3, 2.0, 200, "SCALE_WATCH"),
    stage_25: _st(4.5, 1.12, 3.0, 350, "SCALE_WATCH"),
    stage_50: _st(3.2, 1.08, 4.0, 500, "SCALE_WEAK"),
    breadth_dependency: { stage10_return: 7.0, stage25_return: 4.5, stage50_return: 3.2,
                          split_decay_pp: 2.0 },
    monthly_quarterly: { positive_months: 7, negative_months: 5, worst_month: "2026-03",
                         worst_month_return: -4.0 },
    conclusions: ["10→50 으로 갈수록 성과 약화(breadth 의존)"],
    next_steps: ["종목 폭 확대 후 재검증"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { intraday1yScaledValidationLatest: vi.fn(async () => report) };
}

describe("<Intraday1YScaledValidationCard>", () => {
  it("완료: verdict + rule hash + EXE 권고 + 실전금지", async () => {
    render(<Intraday1YScaledValidationCard apiClient={_api()} />);
    await screen.findByTestId("y1-verdict");
    expect(screen.getByTestId("y1-final-verdict").textContent).toContain("SCALE_WATCH");
    expect(screen.getByTestId("y1-rule").textContent).toContain("rule_hash_match=true");
    expect(screen.getByTestId("y1-exe-rec").textContent).toContain("관찰용");
    expect(screen.getByTestId("y1-live").textContent).toContain("dry_run 필수");
  });

  it("10/25/50 단계 + breadth + 월별 표시", async () => {
    render(<Intraday1YScaledValidationCard apiClient={_api()} />);
    await screen.findByTestId("y1-verdict");
    expect(screen.getByTestId("y1-s10").textContent).toContain("7");
    expect(screen.getByTestId("y1-s50").textContent).toContain("3.2");
    expect(screen.getByTestId("y1-breadth").textContent).toContain("decay");
    expect(screen.getByTestId("y1-mq").textContent).toContain("2026-03");
  });

  it("데이터 없음", async () => {
    render(<Intraday1YScaledValidationCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("y1-empty")).toBeTruthy();
    expect(screen.queryByTestId("y1-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { intraday1yScaledValidationLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Intraday1YScaledValidationCard apiClient={api} />);
    expect(await screen.findByTestId("y1-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Intraday1YScaledValidationCard apiClient={_api()} />);
    await screen.findByTestId("y1-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Intraday1YScaledValidationCard apiClient={_api()} />);
    await screen.findByTestId("y1-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<Intraday1YScaledValidationCard apiClient={_api()} />);
    await screen.findByTestId("y1-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("SCALE_PAPER_CANDIDATE verdict 표시", async () => {
    render(<Intraday1YScaledValidationCard apiClient={_api(_report({
      final_verdict: "SCALE_PAPER_CANDIDATE",
      paper_rehearsal_recommendation: "KIS 모의매매 dry-run 리허설 후보 (1년 확장 통과, 단 실전 금지)" }))} />);
    await screen.findByTestId("y1-verdict");
    expect(screen.getByTestId("y1-final-verdict").textContent).toContain("PAPER_CANDIDATE");
    expect(screen.getByTestId("y1-live").textContent).toContain("false");
  });
});
