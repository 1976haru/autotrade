/**
 * #96 Loss Root Cause Tagging Card 테스트.
 *
 * 요구 사항:
 * - 단일 거래 결과 렌더링 (primary tag / category / severity)
 * - 집계 요약 (top tags / high severity / by_strategy)
 * - 5 invariant 배지 영구 노출
 * - secret 입력 form 0개
 * - 매수 / 매도 / Place Order / 실거래 라벨 button 0개
 * - secret 패턴 노출 0건
 * - 태그 분포 펼치기 토글
 * - API 호출 + 에러 처리
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    lossRootCauseEvaluate:  vi.fn(),
    lossRootCauseSummarize: vi.fn(),
  },
}));


import { backendApi } from "../../services/backend/client";
import { LossRootCauseCard } from "./LossRootCauseCard";


const _RESULT_MULTI = {
  symbol: "005930",
  is_loss: true,
  trade_pnl: -50000,
  tags: [
    { tag: "stale_signal",     category: "decision",
      severity: "HIGH",   rationale: "signal_age=45m > 30m" },
    { tag: "high_correlation", category: "risk",
      severity: "HIGH",   rationale: "portfolio_max=0.92 >= 0.85" },
    { tag: "slippage",         category: "execution",
      severity: "MEDIUM", rationale: "slippage=75bps > 50bps" },
  ],
  primary_tag:      "high_correlation",
  primary_category: "risk",
  rationale: ["signal_age=45m > 30m", "portfolio_max=0.92 >= 0.85"],
  improvement_advice: [
    "HIGH_CORRELATION — 동시 노출 종목이 매우 강한 상관관계",
    "STALE_SIGNAL — 신호 생성 후 진입까지 시간이 길었음",
  ],
  is_estimated:         true,
  is_order_signal:      false,
  auto_apply_allowed:   false,
  is_investment_advice: false,
  generated_at:         new Date().toISOString(),
};


const _RESULT_UNKNOWN = {
  ..._RESULT_MULTI,
  symbol: "000660",
  trade_pnl: -10000,
  tags: [
    { tag: "unknown", category: "unknown", severity: "UNKNOWN",
      rationale: "입력 metric 으로는 원인 추정 불가" },
  ],
  primary_tag: "unknown",
  primary_category: "unknown",
  rationale: ["입력 metric 으로는 원인 추정 불가"],
  improvement_advice: ["UNKNOWN — collector 보강 권장"],
};


const _SUMMARY = {
  total_loss_count: 5,
  by_tag: [
    { tag: "stale_signal",     category: "decision",
      count: 3, share_pct: 60.0,
      severity_dist: { HIGH: 3 } },
    { tag: "high_correlation", category: "risk",
      count: 2, share_pct: 40.0,
      severity_dist: { HIGH: 2 } },
    { tag: "slippage",         category: "execution",
      count: 1, share_pct: 20.0,
      severity_dist: { MEDIUM: 1 } },
  ],
  by_category:        { decision: 3, risk: 2, execution: 1 },
  top_tags:           ["stale_signal", "high_correlation", "slippage"],
  high_severity_tags: ["high_correlation", "stale_signal"],
  by_strategy: {
    sma_crossover: { decision: 2, risk: 1 },
    vwap_strategy: { execution: 1, risk: 1 },
  },
  is_estimated:       true,
  is_order_signal:    false,
  auto_apply_allowed: false,
};


beforeEach(() => {
  for (const k of Object.keys(backendApi)) {
    if (typeof backendApi[k]?.mockReset === "function") {
      backendApi[k].mockReset();
    }
  }
  backendApi.lossRootCauseEvaluate.mockResolvedValue(_RESULT_MULTI);
  backendApi.lossRootCauseSummarize.mockResolvedValue(_SUMMARY);
});

afterEach(cleanup);


describe("LossRootCauseCard — 단일 거래 결과 렌더링", () => {
  it("primary tag / category 표시", () => {
    const { getByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    expect(getByTestId("loss-root-cause-primary-high_correlation")).toBeTruthy();
    expect(getByTestId("loss-root-cause-detail").textContent)
      .toContain("HIGH_CORRELATION");
  });

  it("multi-cause tag 모두 표시", () => {
    const { getByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    const detail = getByTestId("loss-root-cause-detail").textContent;
    expect(detail).toContain("STALE_SIGNAL");
    expect(detail).toContain("HIGH_CORRELATION");
    expect(detail).toContain("SLIPPAGE");
  });

  it("rationale + improvement advice 표시", () => {
    const { getByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    expect(getByTestId("loss-root-cause-rationale").textContent)
      .toContain("portfolio_max");
    expect(getByTestId("loss-root-cause-advice").textContent)
      .toContain("HIGH_CORRELATION");
  });

  it("UNKNOWN 결과도 정상 표시", () => {
    const { getByTestId, queryByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_UNKNOWN} />,
    );
    expect(getByTestId("loss-root-cause-primary-unknown")).toBeTruthy();
    // detail 안에 UNKNOWN 표시.
    expect(getByTestId("loss-root-cause-detail").textContent)
      .toContain("UNKNOWN");
  });
});


describe("LossRootCauseCard — 집계 요약 렌더링", () => {
  it("총 건수 + top tags + high severity", () => {
    const { getByTestId } = render(
      <LossRootCauseCard summaryOverride={_SUMMARY} />,
    );
    const summary = getByTestId("loss-root-cause-summary").textContent;
    expect(summary).toContain("5건");
    expect(getByTestId("loss-root-cause-top-tags").textContent)
      .toContain("stale_signal");
    expect(getByTestId("loss-root-cause-high-severity").textContent)
      .toContain("high_correlation");
  });

  it("'태그 분포 펼치기' 토글 + by_strategy", () => {
    const { getByTestId, queryByTestId } = render(
      <LossRootCauseCard summaryOverride={_SUMMARY} />,
    );
    expect(queryByTestId("loss-root-cause-by-tag-table")).toBeNull();
    fireEvent.click(getByTestId("loss-root-cause-toggle-detail-btn"));
    const table = getByTestId("loss-root-cause-by-tag-table").textContent;
    expect(table).toContain("stale_signal");
    expect(table).toContain("60");
    const strat = getByTestId("loss-root-cause-by-strategy").textContent;
    expect(strat).toContain("sma_crossover");
    expect(strat).toContain("vwap_strategy");
  });
});


describe("LossRootCauseCard — invariant 영구 노출", () => {
  it("5개 invariant 배지 모두 노출 (estimated / no-order / no-auto / no-advice / analysis-only)", () => {
    const { getByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    const block = getByTestId("loss-root-cause-invariants");
    expect(block.textContent).toContain("추정 태그");
    expect(block.textContent).toContain("주문 신호 아님");
    expect(block.textContent).toContain("자동 적용 안 함");
    expect(block.textContent).toContain("투자 조언 아님");
    expect(block.textContent).toContain("분석 전용");
  });

  it("disclaimer 영구 노출 + '분석 전용이며 주문 기능이 아닙니다' 명시", () => {
    const { getByTestId } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    const d = getByTestId("loss-root-cause-disclaimer").textContent;
    expect(d).toContain("추정값");
    expect(d).toContain("확정 원인이 아닙니다");
    expect(d).toContain("분석 전용이며 주문 기능이 아닙니다");
  });
});


describe("LossRootCauseCard — 금지 라벨 button 0개", () => {
  it("어떤 케이스에서도 매수 / 매도 / Place Order / 실거래 라벨 button 0개", () => {
    for (const props of [
      { resultOverride: _RESULT_MULTI },
      { resultOverride: _RESULT_UNKNOWN },
      { summaryOverride: _SUMMARY },
      { resultOverride: _RESULT_MULTI, summaryOverride: _SUMMARY },
    ]) {
      const { container, unmount } = render(
        <LossRootCauseCard {...props} />,
      );
      const buttons = container.querySelectorAll("button");
      for (const b of buttons) {
        const txt = (b.textContent || "").trim();
        for (const banned of [
          "지금 매수", "지금 매도", "매수 실행", "매도 실행",
          "Place Order", "BUY signal", "SELL signal", "HOLD signal",
          "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING 토글",
          "AI 자동 실행 활성화", "전략 비활성화", "Apply Parameters",
        ]) {
          expect(txt.includes(banned)).toBe(false);
        }
      }
      unmount();
    }
  });

  it("BUY / SELL / HOLD signal 텍스트 0건", () => {
    const { container } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    const text = container.textContent || "";
    for (const banned of ["BUY signal", "SELL signal", "HOLD signal",
                          "매수 실행", "매도 실행"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });
});


describe("LossRootCauseCard — secret 노출 0건", () => {
  it("secret 입력 form (input / textarea) 0개", () => {
    const { container } = render(
      <LossRootCauseCard resultOverride={_RESULT_MULTI} />,
    );
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
  });

  it("secret 패턴 노출 0건", () => {
    const { container } = render(
      <LossRootCauseCard
        resultOverride={_RESULT_MULTI}
        summaryOverride={_SUMMARY}
      />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key=", "kis_app_secret=", "anthropic_api_key=",
      "telegram_bot_token=", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });
});


describe("LossRootCauseCard — API 통합", () => {
  it("'예시 평가' 클릭 시 backendApi 호출", async () => {
    const { getByTestId } = render(<LossRootCauseCard />);
    fireEvent.click(getByTestId("loss-root-cause-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("loss-root-cause-primary-high_correlation")).toBeTruthy();
    });
    expect(backendApi.lossRootCauseEvaluate).toHaveBeenCalled();
  });

  it("API 에러 시 error 메시지 표시", async () => {
    backendApi.lossRootCauseEvaluate.mockRejectedValue(new Error("backend down"));
    const { getByTestId } = render(<LossRootCauseCard />);
    fireEvent.click(getByTestId("loss-root-cause-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("loss-root-cause-error").textContent)
        .toContain("backend down");
    });
  });
});
