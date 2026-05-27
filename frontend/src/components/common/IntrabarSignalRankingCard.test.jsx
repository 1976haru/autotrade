/**
 * IntrabarSignalRankingCard 테스트 — 상태/표시 + 주문/실전 버튼 0개 + 경고 문구.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { IntrabarSignalRankingCard } from "./IntrabarSignalRankingCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    one_minute_coverage: {
      status: "WARN", replayable_trades: 2, fallback_trades: 1, ambiguous_trades: 1,
      confidence_distribution: { HIGH: 2, LOW: 1 },
      warning: "1분봉 subset 없음 — 체결 정확도 낮음(LOW).",
    },
    conservative_stop_first_count: 1,
    ambiguous_trade_count: 1,
    earliest_first_selected: ["000001", "000002"],
    composite_selected: ["000002", "005930"],
    ranking_differs: true,
    selected_avg_score: 67.3,
    rejected_avg_score: 39.97,
    vetoed_count: 1,
    cost_model: { commission_bps: 1.5, tax_bps: 18, slippage_bps: 5 },
    is_live_authorization: false,
    auto_apply_allowed: false,
    is_order_signal: false,
    no_profit_guarantee: true,
    disclaimer: "이 결과는 연구용 백테스트이며 실전매매 권고가 아닙니다. 수익을 보장하지 않습니다.",
    ...overrides,
  };
}

function _api(report = _report()) {
  return { intrabarSignalRankingLatest: vi.fn(async () => report) };
}

describe("<IntrabarSignalRankingCard>", () => {
  it("완료: coverage + ranking + 비용 + 실전금지 표시", async () => {
    render(<IntrabarSignalRankingCard apiClient={_api()} />);
    await screen.findByTestId("isr-coverage");
    expect(screen.getByTestId("isr-coverage").textContent).toContain("WARN");
    expect(screen.getByTestId("isr-ranking").textContent).toContain("005930");
    expect(screen.getByTestId("isr-scores").textContent).toContain("67.3");
    expect(screen.getByTestId("isr-cost").textContent).toContain("18bps");
    expect(screen.getByTestId("isr-live").textContent).toContain("false");
  });

  it("데이터 없음", async () => {
    render(<IntrabarSignalRankingCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("isr-empty")).toBeTruthy();
    expect(screen.queryByTestId("isr-coverage")).toBeNull();
  });

  it("실패", async () => {
    const api = { intrabarSignalRankingLatest: vi.fn(async () => { throw new Error("x"); }) };
    render(<IntrabarSignalRankingCard apiClient={api} />);
    expect(await screen.findByTestId("isr-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<IntrabarSignalRankingCard apiClient={_api()} />);
    await screen.findByTestId("isr-coverage");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["결과 새로고침", "리포트 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<IntrabarSignalRankingCard apiClient={_api()} />);
    await screen.findByTestId("isr-coverage");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("연구용 경고 문구 노출", async () => {
    const { container } = render(<IntrabarSignalRankingCard apiClient={_api()} />);
    await screen.findByTestId("isr-warning");
    expect(container.textContent).toContain("연구용 백테스트이며 실전매매 권고가 아닙니다");
  });
});
