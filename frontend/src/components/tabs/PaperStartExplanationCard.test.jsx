import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PaperStartExplanationCard } from "./PaperStartExplanationCard";


function _ready_response(overrides = {}) {
  return {
    verdict: "READY_TO_REVIEW",
    verdict_label_ko: "AI Paper 검토 가능 — 추천 조합을 운영자가 검토 후 시작 결정",
    recommended_explanations: [
      {
        strategy: "sma_crossover",
        symbol: "005930",
        bucket: "recommended",
        paper_candidate_status: "READY_FOR_PAPER",
        rationale_lines: ["검증 단계 통과 + 위험 신호 임계 이내 — Paper 검토 가능"],
        risk_flags: [],
        overfit_verdict: "HEALTHY",
        overfit_reason: null,
        train_validation_gap: 0.20,
        regime_policy_role: "preferred",
        is_order_signal: false,
        auto_apply_allowed: false,
        is_live_authorization: false,
      },
    ],
    watchlist_explanations: [],
    excluded_explanations: [],
    market_regime: "TREND_UP",
    regime_confidence: 0.75,
    regime_reasons: ["trend_direction=UP"],
    regime_risk_flags: [],
    regime_allowed_tactics: ["sma_crossover", "volume_breakout"],
    regime_blocked_tactics: [],
    overfit_count: 0,
    overfit_strategies: [],
    headline: "오늘 AI Paper 검토 가능: 1건 — sma_crossover/005930. 본 추천은 advisory.",
    risk_summary: [],
    operator_note: "advisory",
    next_actions: ["추천 전략을 *수동* 으로 Paper Auto Loop 에 입력",
                    "본 설명은 advisory — 실거래 활성화는 별도 옵트인 절차 필요"],
    can_start_paper: true,
    blocking_reasons: [],
    advisory_disclaimer: "본 설명은 *advisory* — 실거래 주문이 아니며 ...",
    metadata: {},
    is_order_signal: false,
    auto_apply_allowed: false,
    is_live_authorization: false,
    ...overrides,
  };
}


function _mockApi(response) {
  return {
    paperStartExplanation: vi.fn(async () => response),
  };
}


describe("<PaperStartExplanationCard>", () => {
  afterEach(cleanup);

  it("renders safety badges (Paper 전용 / 실거래 아님 / 자동 시작 아님)", async () => {
    const api = _mockApi(_ready_response());
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(api.paperStartExplanation).toHaveBeenCalled());
    expect(screen.getByTestId("badge-paper-only").textContent).toContain("Paper 전용");
    expect(screen.getByTestId("badge-not-real-order").textContent).toContain("실거래 주문 아님");
    expect(screen.getByTestId("badge-not-auto-start").textContent).toContain("자동 시작 아님");
  });

  it("READY_TO_REVIEW: 추천 전략 + 사유 표시", async () => {
    const api = _mockApi(_ready_response());
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("paper-explanation-verdict")).toBeTruthy());
    const verdict = screen.getByTestId("paper-explanation-verdict");
    expect(verdict.getAttribute("data-verdict")).toBe("READY_TO_REVIEW");
    // 추천 전략 표시.
    const recBucket = screen.getByTestId("paper-explanation-bucket-recommended");
    expect(recBucket.textContent).toContain("sma_crossover");
    expect(recBucket.textContent).toContain("005930");
    expect(recBucket.textContent).toContain("검증 단계 통과");
  });

  it("장세 정보 표시 + reasons", async () => {
    const api = _mockApi(_ready_response());
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("paper-explanation-regime")).toBeTruthy());
    const regime = screen.getByTestId("paper-explanation-regime");
    expect(regime.textContent).toContain("TREND_UP");
    expect(regime.textContent).toContain("75%");
    expect(screen.getByTestId("paper-explanation-regime-reasons").textContent)
      .toContain("trend_direction=UP");
  });

  it("can_start_paper=false 시 blocking_reasons 표시", async () => {
    const api = _mockApi(_ready_response({
      verdict: "DO_NOT_START",
      can_start_paper: false,
      blocking_reasons: ["pre_market_block: api_unhealthy", "no_candidate: 분석 가능한 후보 0건"],
      recommended_explanations: [],
    }));
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(screen.getByTestId("paper-explanation-blocking")).toBeTruthy());
    const blocking = screen.getByTestId("paper-explanation-blocking");
    expect(blocking.textContent).toContain("pre_market_block");
    expect(blocking.textContent).toContain("no_candidate");
  });

  it("후보 0개 빈 상태 표시", async () => {
    const api = _mockApi(_ready_response({
      verdict: "DO_NOT_START",
      recommended_explanations: [],
      watchlist_explanations: [],
      excluded_explanations: [],
      can_start_paper: false,
      blocking_reasons: ["no_candidate"],
    }));
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() =>
      expect(screen.getByTestId("paper-explanation-bucket-recommended-empty")).toBeTruthy(),
    );
    expect(screen.getByTestId("paper-explanation-bucket-recommended-empty").textContent)
      .toContain("오늘 추천 전략이 없습니다");
  });

  it("OVERFIT_RISK 전략은 excluded 에 표시 + overfit 카운트", async () => {
    const api = _mockApi(_ready_response({
      verdict: "REVIEW_WITH_WARNING",
      recommended_explanations: [],
      excluded_explanations: [
        {
          strategy: "sma_crossover", symbol: "005930",
          bucket: "excluded",
          paper_candidate_status: "OVERFIT_RISK",
          rationale_lines: [
            "위험 한도 위반",
            "⚠ 과최적화 의심 — 훈련구간에서만 좋고 검증구간에서 성과 저하 (train/val gap=0.87)",
          ],
          risk_flags: [],
          overfit_verdict: "OVERFIT_RISK",
          overfit_reason: "OVERFIT_RISK",
          train_validation_gap: 0.87,
          regime_policy_role: "preferred",
          is_order_signal: false,
          auto_apply_allowed: false,
          is_live_authorization: false,
        },
      ],
      overfit_count: 1,
      overfit_strategies: ["sma_crossover/005930"],
    }));
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() =>
      expect(screen.getByTestId("paper-explanation-bucket-excluded")).toBeTruthy(),
    );
    const excluded = screen.getByTestId("paper-explanation-bucket-excluded");
    expect(excluded.textContent).toContain("OVERFIT_RISK");
    expect(excluded.textContent).toContain("훈련구간");
    // overfit 카운트 별도 강조.
    expect(screen.getByTestId("paper-explanation-overfit").textContent)
      .toContain("과최적화 의심 전략 1건");
  });

  it("위험 요약 표시", async () => {
    const api = _mockApi(_ready_response({
      verdict: "REVIEW_WITH_WARNING",
      risk_summary: ["high_volatility_size_reduce", "low_win_rate"],
    }));
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() =>
      expect(screen.getByTestId("paper-explanation-risk-summary")).toBeTruthy(),
    );
    expect(screen.getByTestId("paper-explanation-risk-summary").textContent)
      .toContain("high_volatility_size_reduce");
  });

  it("실거래 버튼 라벨 0개 (BUY/SELL/Place Order/실거래 시작/ENABLE_*)", async () => {
    const api = _mockApi(_ready_response());
    const { container } = render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(api.paperStartExplanation).toHaveBeenCalled());
    const banned = [
      "지금 매수", "지금 매도", "실거래 시작",
      "Place Order", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기",
    ];
    for (const b of banned) {
      expect(container.textContent).not.toContain(b);
    }
    // 모든 button 의 textContent 도 금지 라벨 0개.
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const t = (btn.textContent || "").trim();
      expect(t).not.toMatch(/^(매수|매도|BUY|SELL|EXIT|Place Order|실거래 시작|AI 자동매매 켜기)$/);
    }
  });

  it("secret 키워드 노출 0건", async () => {
    const api = _mockApi(_ready_response());
    const { container } = render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() => expect(api.paperStartExplanation).toHaveBeenCalled());
    const text = container.textContent.toLowerCase();
    for (const w of ["api_key", "anthropic_api_key", "openai_api_key",
                       "kis_app_key", "kis_app_secret", "account_no"]) {
      expect(text).not.toContain(w);
    }
  });

  it("advisory disclaimer 항상 표시", async () => {
    const api = _mockApi(_ready_response());
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() =>
      expect(screen.getByTestId("paper-explanation-disclaimer")).toBeTruthy(),
    );
    expect(screen.getByTestId("paper-explanation-disclaimer").textContent)
      .toContain("advisory");
  });

  it("다음 행동 표시", async () => {
    const api = _mockApi(_ready_response());
    render(<PaperStartExplanationCard apiClient={api} />);
    await waitFor(() =>
      expect(screen.getByTestId("paper-explanation-next-actions")).toBeTruthy(),
    );
    expect(screen.getByTestId("paper-explanation-next-actions").textContent)
      .toContain("Paper Auto Loop");
  });
});
