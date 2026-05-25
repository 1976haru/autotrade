/**
 * ForwardUniverseCard 테스트 — 4상태 + verdict + selector + 실전금지 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ForwardUniverseCard } from "./ForwardUniverseCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    final_universe_verdict: "UNIVERSE_WATCH",
    rule_locked_before_test: true,
    exe_rebuild_recommendation: "EXE 관찰용 재빌드 가능, 자동매매/모의주문 비활성",
    paper_rehearsal_recommendation: "Paper 리허설 아직 불가 — forward universe 보강 필요",
    live_trading_recommendation: false,
    best_selector: { selector: "FORWARD_SCORE_TOP10", forward_return_pct: 3.2, forward_mdd_pct: 12.0,
                     total_trades: 140, universe_stability: 0.6, verdict: "UNIVERSE_WATCH" },
    selector_results: [
      { _label: "SEL_FORWARD_SCORE_TOP10", selector: "FORWARD_SCORE_TOP10", verdict: "UNIVERSE_WATCH",
        forward_return_pct: 3.2, forward_mdd_pct: 12.0, total_trades: 140, universe_stability: 0.6,
        look_ahead_warning: false },
      { _label: "SEL_STATIC_IN_SAMPLE_TOP10", selector: "STATIC_IN_SAMPLE_TOP10", verdict: "LOOK_AHEAD_REF",
        forward_return_pct: 9.0, forward_mdd_pct: 8.0, total_trades: 200, universe_stability: 1.0,
        look_ahead_warning: true },
    ],
    static_vs_forward: { static_all_return: -18.0, static_in_sample_top10_return: 9.0,
                         best_forward_return: 3.2 },
    repeated_selected: ["005930", "000660"],
    repeated_excluded: ["035720"],
    missed_opportunity: [{ symbol: "012330", full_period_net_pnl: 100000 }],
    overfit_warning: { warning: false },
    conclusions: ["point-in-time universe 가 forward 에서 양(+) 유지"],
    next_steps: ["추가 기간 재검증"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { forwardUniverseLatest: vi.fn(async () => report) };
}

describe("<ForwardUniverseCard>", () => {
  it("완료: verdict + best + EXE 권고 + 실전금지", async () => {
    render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-verdict");
    expect(screen.getByTestId("fu-final-verdict").textContent).toContain("UNIVERSE_WATCH");
    expect(screen.getByTestId("fu-best").textContent).toContain("FORWARD_SCORE_TOP10");
    expect(screen.getByTestId("fu-exe-rec").textContent).toContain("관찰용");
    expect(screen.getByTestId("fu-live").textContent).toContain("false");
  });

  it("selector 결과 + look-ahead 참고 표시 + static vs forward", async () => {
    render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-verdict");
    expect(screen.getByTestId("fu-selectors").textContent).toContain("FORWARD_SCORE_TOP10");
    expect(screen.getByTestId("fu-lookahead-note").textContent).toContain("look-ahead");
    expect(screen.getByTestId("fu-static-vs").textContent).toContain("9");
  });

  it("반복 선택/제외 종목 표시", async () => {
    render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-verdict");
    expect(screen.getByTestId("fu-repeated").textContent).toContain("005930");
    expect(screen.getByTestId("fu-excluded").textContent).toContain("035720");
  });

  it("데이터 없음", async () => {
    render(<ForwardUniverseCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("fu-empty")).toBeTruthy();
    expect(screen.queryByTestId("fu-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { forwardUniverseLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<ForwardUniverseCard apiClient={api} />);
    expect(await screen.findByTestId("fu-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<ForwardUniverseCard apiClient={_api()} />);
    await screen.findByTestId("fu-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("UNIVERSE_PAPER_CANDIDATE verdict 표시", async () => {
    render(<ForwardUniverseCard apiClient={_api(_report({
      final_universe_verdict: "UNIVERSE_PAPER_CANDIDATE",
      paper_rehearsal_recommendation: "KIS 모의매매 dry-run 리허설 후보 (forward universe 통과, 단 실전 금지)" }))} />);
    await screen.findByTestId("fu-verdict");
    expect(screen.getByTestId("fu-final-verdict").textContent).toContain("UNIVERSE_PAPER_CANDIDATE");
    expect(screen.getByTestId("fu-live").textContent).toContain("false");
  });
});
