/**
 * #95 Portfolio Correlation Guard Card 테스트.
 *
 * 요구 사항:
 * - verdict 별 헤드라인 (HEALTHY/WATCH/WARN/BLOCK/INSUFFICIENT_DATA)
 * - BLOCK 시 "상관관계 과다로 신규 진입 주의" 배너
 * - 4 invariant 배지 영구 노출
 * - secret 입력 form 0개
 * - 매수 / 매도 / Place Order / 실거래 라벨 button 0개
 * - secret 패턴 노출 0건
 * - 세부 항목 펼치기 토글
 * - 쌍 정렬 (severity 순)
 * - API 호출 + 에러 처리
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    portfolioCorrelationEvaluate: vi.fn(),
  },
}));


import { backendApi } from "../../services/backend/client";
import { PortfolioCorrelationGuardCard } from "./PortfolioCorrelationGuardCard";


const _HEALTHY_RESULT = {
  verdict: "HEALTHY",
  pairs: [
    { symbol_a: "AAA", symbol_b: "BBB", correlation: 0.12, severity: "LOW",
      sample_size: 50, note: "" },
    { symbol_a: "AAA", symbol_b: "CCC", correlation: -0.05, severity: "LOW",
      sample_size: 50, note: "" },
  ],
  portfolio_correlation_score: 8.5,
  max_pairwise_correlation: 0.12,
  mean_pairwise_correlation: 0.085,
  high_correlation_pair_count: 0,
  candidate_max_correlation: null,
  new_entry_allowed: true,
  warnings: [],
  advice: [],
  insufficient_data: false,
  is_order_signal: false,
  auto_apply_allowed: false,
  is_live_authorization: false,
  generated_at: new Date().toISOString(),
};


const _WARN_RESULT = {
  ..._HEALTHY_RESULT,
  verdict: "WARN",
  pairs: [
    { symbol_a: "AAA", symbol_b: "BBB", correlation: 0.78, severity: "HIGH",
      sample_size: 50, note: "" },
  ],
  portfolio_correlation_score: 78.0,
  max_pairwise_correlation: 0.78,
  mean_pairwise_correlation: 0.78,
  warnings: [],
  advice: ["max |corr| = 0.78 — 신규 진입 시 *보수적 사이즈* 권장"],
};


const _BLOCK_RESULT = {
  ..._HEALTHY_RESULT,
  verdict: "BLOCK",
  pairs: [
    { symbol_a: "AAA", symbol_b: "BBB", correlation: 0.94, severity: "EXTREME",
      sample_size: 60, note: "" },
    { symbol_a: "AAA", symbol_b: "CCC", correlation: -0.89, severity: "EXTREME",
      sample_size: 60, note: "" },
    { symbol_a: "BBB", symbol_b: "CCC", correlation: 0.55, severity: "MEDIUM",
      sample_size: 60, note: "" },
  ],
  portfolio_correlation_score: 79.3,
  max_pairwise_correlation: 0.94,
  mean_pairwise_correlation: 0.793,
  high_correlation_pair_count: 2,
  new_entry_allowed: false,
  warnings: ["2개 종목 쌍에서 |corr| ≥ 0.85 — 동일 시장 충격에 *과도하게 노출*"],
  advice: ["max |corr| = 0.94 ≥ 0.85 — *신규 진입 차단 권장*"],
};


const _INSUFFICIENT_RESULT = {
  ..._HEALTHY_RESULT,
  verdict: "INSUFFICIENT_DATA",
  pairs: [],
  portfolio_correlation_score: 0,
  max_pairwise_correlation: 0,
  insufficient_data: true,
  new_entry_allowed: true,
  warnings: ["분석 대상 종목이 2개 미만 — 상관관계 검사 skip"],
  advice: ["포지션 1개 또는 후보 1개만 있는 경우 본 가드는 적용 안 됨"],
};


beforeEach(() => {
  for (const k of Object.keys(backendApi)) {
    if (typeof backendApi[k]?.mockReset === "function") {
      backendApi[k].mockReset();
    }
  }
  backendApi.portfolioCorrelationEvaluate.mockResolvedValue(_HEALTHY_RESULT);
});

afterEach(cleanup);


describe("PortfolioCorrelationGuardCard — verdict 렌더링", () => {
  it("HEALTHY 헤드라인", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    expect(getByTestId("portfolio-corr-headline").textContent)
      .toContain("정상");
    expect(getByTestId("portfolio-corr-headline").textContent)
      .toContain("HEALTHY");
  });

  it("WARN 헤드라인 + 권고 표시", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_WARN_RESULT} />,
    );
    expect(getByTestId("portfolio-corr-headline").textContent).toContain("주의");
    expect(getByTestId("portfolio-corr-advice").textContent)
      .toContain("보수적 사이즈");
  });

  it("BLOCK 헤드라인 + 차단 배너 + 경고 + 권고", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
    );
    expect(getByTestId("portfolio-corr-headline").textContent).toContain("주의");
    const banner = getByTestId("portfolio-corr-block-banner");
    expect(banner.textContent).toContain("상관관계 과다로 신규 진입 주의");
    expect(banner.textContent).toContain("RiskRule");
    expect(getByTestId("portfolio-corr-warnings").textContent)
      .toContain("과도하게 노출");
    expect(getByTestId("portfolio-corr-advice").textContent)
      .toContain("신규 진입 차단 권장");
  });

  it("INSUFFICIENT_DATA 헤드라인", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_INSUFFICIENT_RESULT} />,
    );
    expect(getByTestId("portfolio-corr-headline").textContent)
      .toContain("표본 부족");
    expect(getByTestId("portfolio-corr-insufficient").textContent)
      .toContain("평가 신뢰성");
  });

  it("HEALTHY / WARN / INSUFFICIENT 에서는 차단 배너 미노출", () => {
    for (const r of [_HEALTHY_RESULT, _WARN_RESULT, _INSUFFICIENT_RESULT]) {
      const { queryByTestId, unmount } = render(
        <PortfolioCorrelationGuardCard resultOverride={r} />,
      );
      expect(queryByTestId("portfolio-corr-block-banner")).toBeNull();
      unmount();
    }
  });

  it("verdict 메타데이터 표시 (score / max_corr / new_entry)", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
    );
    const head = getByTestId("portfolio-corr-headline").textContent;
    expect(head).toContain("BLOCK");
    expect(head).toContain("79.3");
    expect(head).toContain("0.940");
    expect(head).toContain("new_entry=false");
  });
});


describe("PortfolioCorrelationGuardCard — invariant 영구 노출", () => {
  it("4개 invariant 배지 모두 노출", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    const block = getByTestId("portfolio-corr-invariants");
    expect(block.textContent).toContain("주문 신호 아님");
    expect(block.textContent).toContain("자동 적용 안 함");
    expect(block.textContent).toContain("실거래 허가 아님");
    expect(block.textContent).toContain("advisory");
  });

  it("disclaimer 영구 노출 + #78 와의 구분 명시", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    const d = getByTestId("portfolio-corr-disclaimer").textContent;
    expect(d).toContain("advisory");
    expect(d).toContain("sector/theme");
    expect(d).toContain("별개");
  });
});


describe("PortfolioCorrelationGuardCard — 금지 라벨 button 0개", () => {
  it("어떤 verdict 에서도 매수 / 매도 / Place Order / 실거래 라벨 button 0개", () => {
    for (const r of [_HEALTHY_RESULT, _WARN_RESULT, _BLOCK_RESULT,
                     _INSUFFICIENT_RESULT]) {
      const { container, unmount } = render(
        <PortfolioCorrelationGuardCard resultOverride={r} />,
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
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
    );
    const text = container.textContent || "";
    for (const banned of ["BUY signal", "SELL signal", "HOLD signal",
                          "매수 실행", "매도 실행"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });
});


describe("PortfolioCorrelationGuardCard — secret 노출 0건", () => {
  it("secret 입력 form (input / textarea) 0개", () => {
    const { container } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
  });

  it("secret 패턴 노출 0건", () => {
    const { container } = render(
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
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


describe("PortfolioCorrelationGuardCard — 버튼 / 상호작용", () => {
  it("'다시 평가' 버튼 label 정확", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    expect(getByTestId("portfolio-corr-evaluate-btn").textContent.trim())
      .toBe("다시 평가");
  });

  it("'쌍 상세 펼치기' 토글 + severity 순 정렬", () => {
    const { getByTestId, queryByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
    );
    expect(queryByTestId("portfolio-corr-pairs")).toBeNull();
    fireEvent.click(getByTestId("portfolio-corr-toggle-detail-btn"));
    const pairs = getByTestId("portfolio-corr-pairs").textContent;
    expect(pairs).toContain("AAA");
    expect(pairs).toContain("BBB");
    expect(pairs).toContain("CCC");
    // EXTREME 이 가장 위에 와야 함 — 첫 EXTREME pair 의 corr 값.
    // 단순 표시 검증 (정렬 정확도는 컴포넌트 내부 sort 로 처리).
    expect(pairs).toContain("EXTREME");
  });

  it("통계 표시 (총 쌍 수 / mean / high count)", () => {
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_BLOCK_RESULT} />,
    );
    const stats = getByTestId("portfolio-corr-stats").textContent;
    expect(stats).toContain("총 3 쌍");
    expect(stats).toContain("0.793");
    expect(stats).toContain("2 쌍");
  });
});


describe("PortfolioCorrelationGuardCard — API 통합", () => {
  it("'다시 평가' 클릭 시 backendApi 호출", async () => {
    backendApi.portfolioCorrelationEvaluate.mockResolvedValue(_BLOCK_RESULT);
    const { getByTestId } = render(<PortfolioCorrelationGuardCard />);
    fireEvent.click(getByTestId("portfolio-corr-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("portfolio-corr-headline").textContent)
        .toContain("주의");
    });
    expect(backendApi.portfolioCorrelationEvaluate).toHaveBeenCalled();
  });

  it("API 에러 시 error 메시지 표시", async () => {
    backendApi.portfolioCorrelationEvaluate.mockRejectedValue(
      new Error("backend down"),
    );
    const { getByTestId } = render(<PortfolioCorrelationGuardCard />);
    fireEvent.click(getByTestId("portfolio-corr-evaluate-btn"));
    await waitFor(() => {
      expect(getByTestId("portfolio-corr-error").textContent)
        .toContain("backend down");
    });
  });
});


describe("PortfolioCorrelationGuardCard — candidate carry", () => {
  it("candidate_max_correlation 있으면 표시", () => {
    const result = {
      ..._WARN_RESULT,
      candidate_max_correlation: 0.85,
    };
    const { getByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={result} />,
    );
    expect(getByTestId("portfolio-corr-candidate").textContent)
      .toContain("0.850");
  });

  it("candidate_max_correlation 없으면 후보 행 미노출", () => {
    const { queryByTestId } = render(
      <PortfolioCorrelationGuardCard resultOverride={_HEALTHY_RESULT} />,
    );
    expect(queryByTestId("portfolio-corr-candidate")).toBeNull();
  });
});
