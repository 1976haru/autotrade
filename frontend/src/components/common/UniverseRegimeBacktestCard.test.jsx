/**
 * UniverseRegimeBacktestCard 테스트 — loading/complete/notready/error + 버튼/경고 lock.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { UniverseRegimeBacktestCard } from "./UniverseRegimeBacktestCard";

afterEach(cleanup);

function _complete() {
  const blk = (pf) => ({ trade_count: 100, net_pf: pf });
  return {
    available: true, verdict: "UNIVERSE_EDGE_NOT_FOUND", is_research_only: true,
    conclusion: ["데이터 있는 모든 종목군에서 4전략/Council/평균회귀 모두 비용 후 PF<1."],
    group_results: {
      LARGE_CAP_CORE: { present: 10, best_single_strategy: "ORB", best_single_pf: 0.20,
                        council: blk(0.64), council_risk_filter_pf: 0.66,
                        four_strategy: { ORB: blk(0.20), MOMENTUM: blk(0.19), GAP: blk(0.11), VWAP: blk(0.14) },
                        mean_reversion: {} },
      ETF_PROXY: { present: 0, note: "데이터 없음" },
    },
    regime_results: {
      STRONG_UPTREND: { ORB: blk(0.3), MOMENTUM: blk(0.25), COUNCIL: blk(1.299) },
      SIDEWAYS: { ORB: blk(0.15), MOMENTUM: blk(0.12), COUNCIL: blk(0.5) },
    },
    point_in_time_regime_results: {
      PIT_STRONG_UPTREND: { ORB: blk(0.156), MOMENTUM: blk(0.224), COUNCIL: blk(0.728) },
      PIT_HIGH_VOLATILITY: { ORB: blk(0.203), MOMENTUM: blk(0.16), COUNCIL: blk(0.774) },
    },
    posthoc_vs_pit_agreement_rate: 0.505,
    strong_uptrend_comparison: {
      posthoc_council_pf: 1.299, pit_council_pf: 0.728, pit_council_trade_count: 161,
      hint_survives_without_lookahead: false,
    },
    regime_lookahead_flags: { posthoc_regime_is_lookahead: true },
    survivors: [], failures: ["LARGE_CAP_CORE"],
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
  };
}

const _api = (r) => ({ universeRegimeBacktestLatest: vi.fn(async () => r) });

describe("<UniverseRegimeBacktestCard>", () => {
  it("loading 초기 상태", () => {
    render(<UniverseRegimeBacktestCard apiClient={{ universeRegimeBacktestLatest: () => new Promise(() => {}) }} />);
    expect(screen.getByTestId("urb-loading")).toBeTruthy();
  });

  it("complete: 그룹/국면/verdict + research_only", async () => {
    render(<UniverseRegimeBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("urb-verdict");
    expect(screen.getByTestId("urb-verdict").textContent).toContain("UNIVERSE_EDGE_NOT_FOUND");
    expect(screen.getByTestId("urb-groups").textContent).toContain("LARGE_CAP_CORE");
    expect(screen.getByTestId("urb-groups").textContent).toContain("데이터 없음");
    expect(screen.getByTestId("urb-regimes").textContent).toContain("STRONG_UPTREND");
    // PIT section + STRONG_UPTREND look-ahead 검증 표시.
    expect(screen.getByTestId("urb-pit-regimes").textContent).toContain("PIT_STRONG_UPTREND");
    expect(screen.getByTestId("urb-strong-uptrend").textContent).toContain("false");
    expect(screen.getByTestId("urb-strong-uptrend").textContent).toContain("사라짐");
    expect(screen.getByTestId("urb-survivors").textContent).toContain("없음");
    expect(screen.getByTestId("urb-auto").textContent).toContain("true");
    expect(screen.getByTestId("urb-auto").textContent).toContain("false");
  });

  it("notready (no data)", async () => {
    render(<UniverseRegimeBacktestCard apiClient={_api({ available: false, verdict: "NEED_MORE_DATA" })} />);
    expect(await screen.findByTestId("urb-notready")).toBeTruthy();
    expect(screen.queryByTestId("urb-verdict")).toBeNull();
  });

  it("error/fail", async () => {
    render(<UniverseRegimeBacktestCard apiClient={{ universeRegimeBacktestLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("urb-error")).toBeTruthy();
  });

  it("적용/실전/주문/자동매매 버튼 0개", async () => {
    const { container } = render(<UniverseRegimeBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("urb-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<UniverseRegimeBacktestCard apiClient={_api(_complete())} />);
    await screen.findByTestId("urb-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("등록/자동 적용되지 않습니다");
  });
});
