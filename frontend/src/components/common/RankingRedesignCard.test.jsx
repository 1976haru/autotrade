/**
 * RankingRedesignCard 테스트 — complete/notready/error + 주문버튼 0 + 자동적용 false + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RankingRedesignCard } from "./RankingRedesignCard";

afterEach(cleanup);

function _complete() {
  return {
    available: true, verdict: "RANKING_FILTER_ONLY_RECOMMENDED",
    best_candidate: { name: "NO_RANKING_RISK_VETO_ONLY", profit_factor: 1.372, mdd_pct: 16.9 },
    oos: {
      EARLIEST_FIRST: { profit_factor: 1.279, mdd_pct: 20.7, expectancy: 14.0 },
      CURRENT_COMPOSITE: { profit_factor: 0.794, mdd_pct: 31.5, expectancy: -13.3 },
    },
    look_ahead_candidates: ["CURRENT_COMPOSITE", "REGIME_AWARE", "RISK_FIRST", "TOP_SCORE_WITH_MIN_EDGE"],
    recommendation: "순위 재배열은 가치 없음 — 필터만 권고.",
    auto_apply_allowed: false, applied_to_runtime: false, is_live_authorization: false,
    no_profit_guarantee: true,
    disclaimer: "연구용 백테스트이며 실전매매 권고가 아닙니다.",
  };
}

const _api = (r) => ({ rankingRedesignLatest: vi.fn(async () => r) });

describe("<RankingRedesignCard>", () => {
  it("complete: verdict + baseline + 자동적용 false", async () => {
    render(<RankingRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rr-verdict");
    expect(screen.getByTestId("rr-verdict").textContent).toContain("FILTER_ONLY");
    expect(screen.getByTestId("rr-baseline").textContent).toContain("0.794");
    expect(screen.getByTestId("rr-lookahead").textContent).toContain("CURRENT_COMPOSITE");
    expect(screen.getByTestId("rr-auto").textContent).toContain("false");
  });

  it("notready", async () => {
    render(<RankingRedesignCard apiClient={_api({ available: false, verdict: "RANKING_REJECTED" })} />);
    expect(await screen.findByTestId("rr-notready")).toBeTruthy();
    expect(screen.queryByTestId("rr-verdict")).toBeNull();
  });

  it("error", async () => {
    render(<RankingRedesignCard apiClient={{ rankingRedesignLatest: vi.fn(async () => { throw new Error("x"); }) }} />);
    expect(await screen.findByTestId("rr-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 버튼 0개", async () => {
    const { container } = render(<RankingRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rr-verdict");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    for (const t of labels) expect(t).not.toMatch(/실전|주문|적용|자동매매 시작|매수|매도|Place Order/);
  });

  it("input 0개 + 경고 문구", async () => {
    const { container } = render(<RankingRedesignCard apiClient={_api(_complete())} />);
    await screen.findByTestId("rr-verdict");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
    expect(container.textContent).toContain("자동 적용되지 않습니다");
  });
});
