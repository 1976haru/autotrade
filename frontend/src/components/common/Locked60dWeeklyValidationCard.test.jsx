/**
 * Locked60dWeeklyValidationCard 테스트 — 4상태 + verdict + holdout + 실전금지 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { Locked60dWeeklyValidationCard } from "./Locked60dWeeklyValidationCard";

afterEach(cleanup);

function _hd(ret, pf, mdd, tr) {
  return { forward_return_pct: ret, median_pf: pf, forward_mdd_pct: mdd, total_trades: tr };
}

function _report(overrides = {}) {
  return {
    available: true,
    final_verdict: "LOCKED_RULE_WATCH",
    locked_rule_name: "FORWARD_UNIVERSE_60D_WEEKLY_LOCKED_V1",
    rule_locked_before_validation: true, no_look_ahead: true,
    exe_rebuild_recommendation: "EXE 관찰용 재빌드 가능, 자동매매/모의주문 비활성",
    paper_rehearsal_recommendation: "Paper 리허설 아직 불가 — holdout/추가기간 보강 필요",
    live_trading_recommendation: false,
    original_6m_replay: _hd(8.3, 1.2, 2.5, 102),
    last20_holdout: _hd(2.1, 1.1, 1.5, 40),
    last40_holdout: _hd(4.2, 1.12, 3.0, 70),
    worst_month_holdout: { ..._hd(-5.0, 0.95, 6.0, 30), worst_month: "2026-03",
                           baseline_worst_month_return: -23.2, defended: true },
    symbol_split: { even: _hd(3.0, 1.1, 2.0, 50), odd: _hd(2.0, 1.05, 2.5, 48), return_decay_pp: 1.0 },
    slippage_stress: { slippage_5bps: _hd(4.2, 1.12, 3.0, 70), slippage_10bps: _hd(1.0, 1.02, 4.0, 70),
                       slippage_10bps_ok: true },
    compare_40d_monthly: { static_all: _hd(-1.3, 0.95, 5, 250), forward_40d_monthly: _hd(3.6, 1.1, 1.4, 101),
                           locked_60d_weekly_full: _hd(8.3, 1.2, 2.5, 102) },
    risk_veto_recheck: { risk_veto_only: _hd(8.3, 1.2, 2.5, 102), agent_off: _hd(-1.1, 0.96, 5, 200),
                         risk_veto_better: true },
    defense_recheck: { no_defense: _hd(8.3, 1.2, 2.5, 102), "daily_loss_1.5": _hd(8.3, 1.2, 2.5, 102),
                       defense_changed_outcome: false },
    repeated_selected: ["005930", "042700"],
    conclusions: ["last-40D holdout 양(+) 유지", "worst-month 방어 성공"],
    next_steps: ["관찰용 EXE 검토"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { locked60dWeeklyLatest: vi.fn(async () => report) };
}

describe("<Locked60dWeeklyValidationCard>", () => {
  it("완료: verdict + locked rule + EXE 권고 + 실전금지", async () => {
    render(<Locked60dWeeklyValidationCard apiClient={_api()} />);
    await screen.findByTestId("lk-verdict");
    expect(screen.getByTestId("lk-final-verdict").textContent).toContain("LOCKED_RULE_WATCH");
    expect(screen.getByTestId("lk-rule").textContent).toContain("60D_WEEKLY_LOCKED_V1");
    expect(screen.getByTestId("lk-exe-rec").textContent).toContain("관찰용");
    expect(screen.getByTestId("lk-live").textContent).toContain("false");
  });

  it("holdout 결과 + worst-month 방어 + 비교 + RISK_VETO 표시", async () => {
    render(<Locked60dWeeklyValidationCard apiClient={_api()} />);
    await screen.findByTestId("lk-verdict");
    expect(screen.getByTestId("lk-h40").textContent).toContain("4.2");
    expect(screen.getByTestId("lk-wm").textContent).toContain("2026-03");
    expect(screen.getByTestId("lk-wm").textContent).toContain("true");
    expect(screen.getByTestId("lk-compare").textContent).toContain("8.3");
    expect(screen.getByTestId("lk-rv").textContent).toContain("true");
    expect(screen.getByTestId("lk-slip").textContent).toContain("true");
  });

  it("데이터 없음", async () => {
    render(<Locked60dWeeklyValidationCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("lk-empty")).toBeTruthy();
    expect(screen.queryByTestId("lk-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { locked60dWeeklyLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<Locked60dWeeklyValidationCard apiClient={api} />);
    expect(await screen.findByTestId("lk-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<Locked60dWeeklyValidationCard apiClient={_api()} />);
    await screen.findByTestId("lk-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<Locked60dWeeklyValidationCard apiClient={_api()} />);
    await screen.findByTestId("lk-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<Locked60dWeeklyValidationCard apiClient={_api()} />);
    await screen.findByTestId("lk-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("LOCKED_RULE_PAPER_CANDIDATE verdict 표시", async () => {
    render(<Locked60dWeeklyValidationCard apiClient={_api(_report({
      final_verdict: "LOCKED_RULE_PAPER_CANDIDATE",
      paper_rehearsal_recommendation: "KIS 모의매매 dry-run 리허설 후보 (holdout 통과, 단 실전 금지)" }))} />);
    await screen.findByTestId("lk-verdict");
    expect(screen.getByTestId("lk-final-verdict").textContent).toContain("PAPER_CANDIDATE");
    expect(screen.getByTestId("lk-live").textContent).toContain("false");
  });
});
