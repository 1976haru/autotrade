/**
 * StrategyAgentDecompositionCard 테스트 — 4상태 + verdict + EXE 권고 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { StrategyAgentDecompositionCard } from "./StrategyAgentDecompositionCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    final_verdict: "PAPER_REHEARSAL_CANDIDATE",
    paper_rehearsal_candidate: true,
    exe_rebuild_recommendation: "EXE 재빌드 후 *모의매매 리허설* 가능 (실전 금지)",
    baseline: { total_return_pct: -18.1, profit_factor: 0.85, max_drawdown_pct: 30.1,
                trade_count: 676 },
    strategy_only: [
      { _label: "STRAT_GAP_only", total_return_pct: 2.0, profit_factor: 1.65 },
      { _label: "STRAT_GAP+ORB+VWAP", total_return_pct: -1.5, profit_factor: 0.9 },
    ],
    agent_roles: [],
    agent_off_comparison: {
      agent_off_return_pct: -13.0,
      by_role: [
        { role: "ROLE_AGENT_OFF", return_pct: -13.0, mdd: 30.8, pf: 0.92, vs_off_return_pp: 0 },
        { role: "ROLE_AGENT_RISK_VETO_ONLY", return_pct: 5.7, mdd: 18.0, pf: 1.2, vs_off_return_pp: 18.7 },
      ],
    },
    top_by_return: [
      { _label: "UNI_go_tune_top10", total_return_pct: 13.8, profit_factor: 1.25,
        max_drawdown_pct: 11.0, low_confidence: false },
    ],
    top_low_live_risk: [],
    conclusions: ["매매기법 자체는 죽지 않음", "Agent 제거가 더 나쁨 → veto 유지"],
    do_not_auto_apply: true, auto_apply_allowed: false, is_live_authorization: false,
    broker_order_sent: false, order_created: false, exe_build_executed: false,
    contains_secret: false, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { strategyAgentDecompositionLatest: vi.fn(async () => report) };
}

describe("<StrategyAgentDecompositionCard>", () => {
  it("완료: verdict + EXE 권고 + baseline vs Agent OFF + 매매기법 최고", async () => {
    render(<StrategyAgentDecompositionCard apiClient={_api()} />);
    await screen.findByTestId("sad-verdict");
    expect(screen.getByTestId("sad-final-verdict").textContent).toContain("PAPER_REHEARSAL_CANDIDATE");
    expect(screen.getByTestId("sad-exe-rec").textContent).toContain("모의매매 리허설");
    expect(screen.getByTestId("sad-agent-off").textContent).toContain("-13");
    expect(screen.getByTestId("sad-best-strat").textContent).toContain("STRAT_GAP_only");
    expect(screen.getByTestId("sad-roles").textContent).toContain("RISK_VETO");
  });

  it("데이터 없음", async () => {
    render(<StrategyAgentDecompositionCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("sad-empty")).toBeTruthy();
    expect(screen.queryByTestId("sad-verdict")).toBeNull();
  });

  it("실패", async () => {
    const api = { strategyAgentDecompositionLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<StrategyAgentDecompositionCard apiClient={api} />);
    expect(await screen.findByTestId("sad-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<StrategyAgentDecompositionCard apiClient={_api()} />);
    await screen.findByTestId("sad-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<StrategyAgentDecompositionCard apiClient={_api()} />);
    await screen.findByTestId("sad-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출", async () => {
    const { container } = render(<StrategyAgentDecompositionCard apiClient={_api()} />);
    await screen.findByTestId("sad-warning");
    expect(container.textContent).toContain("연구/백테스트 결과이며 실전매매 권고가 아닙니다");
  });

  it("STILL_NOT_RECOMMENDED 시 EXE 보류 권고", async () => {
    render(<StrategyAgentDecompositionCard apiClient={_api(_report({
      final_verdict: "STILL_NOT_RECOMMENDED", paper_rehearsal_candidate: false,
      exe_rebuild_recommendation: "EXE 재빌드 보류 (개선 미달)" }))} />);
    await screen.findByTestId("sad-verdict");
    expect(screen.getByTestId("sad-exe-rec").textContent).toContain("보류");
  });
});
