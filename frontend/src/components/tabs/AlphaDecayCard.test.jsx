import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AlphaDecayCard } from "./AlphaDecayCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    alphaDecayEvaluate: vi.fn(),
  },
}));


const _HEALTHY = {
  strategy_name: "sma_cross",
  score: 0,
  status: "HEALTHY",
  kind: "NONE",
  degraded_signals: [],
  cautions: [],
  recommended_action: "전략 정상. 운용 지속 + 정기 모니터링.",
  metrics: {
    baseline: { trade_count: 100, expectancy: 300, profit_factor: 1.5,
                win_rate: 0.55, max_drawdown: 200000, max_consecutive_losses: 3 },
    recent:   { trade_count: 50,  expectancy: 300, profit_factor: 1.5,
                win_rate: 0.55, max_drawdown: 200000, max_consecutive_losses: 3 },
  },
};


const _CANDIDATE = {
  strategy_name: "weak",
  score: 88,
  status: "DISABLE_CANDIDATE",
  kind: "STRUCTURAL_DECAY",
  degraded_signals: ["expectancy_drop", "expectancy_flip_to_negative",
                     "pf_drop", "pf_below_min", "winrate_drop", "mdd_worsen"],
  cautions: ["recent data quality 40.0 < 60.0 — 데이터 품질이 낮아 결과 신뢰도 저하."],
  recommended_action:
    "DISABLE_CANDIDATE — *비활성 후보*. **자동 비활성/삭제 절대 금지**.",
  metrics: {
    baseline: { trade_count: 100, expectancy: 500, profit_factor: 2.0,
                win_rate: 0.6, max_drawdown: 100000, max_consecutive_losses: 2 },
    recent:   { trade_count: 50,  expectancy: -200, profit_factor: 0.7,
                win_rate: 0.3, max_drawdown: 800000, max_consecutive_losses: 8 },
  },
};


const _INSUFFICIENT = {
  strategy_name: "new",
  score: -1,
  status: "INSUFFICIENT_DATA",
  kind: "INSUFFICIENT_DATA",
  degraded_signals: [],
  cautions: ["recent trade_count 5 < 20 — 표본 부족, 알파 감쇠 측정 불가."],
  recommended_action: "INSUFFICIENT_DATA — 추가 운용으로 표본 확보 후 재평가.",
  metrics: {
    baseline: { trade_count: 50, expectancy: 300, profit_factor: 1.5,
                win_rate: 0.55, max_drawdown: 200000, max_consecutive_losses: 3 },
    recent:   { trade_count: 5,  expectancy: 300, profit_factor: 1.5,
                win_rate: 0.55, max_drawdown: 200000, max_consecutive_losses: 3 },
  },
};


afterEach(cleanup);


describe("AlphaDecayCard", () => {
  it("HEALTHY 스냅샷에서 '정상' 배지 노출", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_HEALTHY} />,
    );
    const badge = getByTestId("alpha-decay-status-HEALTHY");
    expect(badge.textContent).toBe("정상");
  });

  it("DISABLE_CANDIDATE에서 '비활성 후보' 배지 + 보조 배지 노출", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    expect(getByTestId("alpha-decay-status-DISABLE_CANDIDATE").textContent).toBe("비활성 후보");
    const aux = getByTestId("alpha-decay-disable-candidate-badge");
    expect(aux.textContent).toContain("자동 비활성 아님");
  });

  it("INSUFFICIENT_DATA에서 표본 부족 배지 + score —", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_INSUFFICIENT} />,
    );
    expect(getByTestId("alpha-decay-status-INSUFFICIENT_DATA").textContent).toBe("표본 부족");
    expect(getByTestId("alpha-decay-summary").textContent).toContain("—");
  });

  it("고지 문구가 항상 노출 (DISABLE_CANDIDATE는 자동 비활성 아님)", () => {
    const { getByTestId, rerender } = render(
      <AlphaDecayCard resultOverride={_HEALTHY} />,
    );
    let disc = getByTestId("alpha-decay-disclaimer").textContent;
    expect(disc).toContain("자동 비활성이 아닙니다");
    expect(disc).toContain("운영자 수동 승인");

    rerender(<AlphaDecayCard resultOverride={_CANDIDATE} />);
    disc = getByTestId("alpha-decay-disclaimer").textContent;
    expect(disc).toContain("자동 비활성이 아닙니다");
  });

  it("전략 비활성화 / 삭제 / promotion 변경 버튼 0개", () => {
    const { container } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBeGreaterThanOrEqual(1);  // 평가 버튼 1개
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "전략 비활성화",
        "전략 비활성 토글",
        "전략 삭제",
        "Disable Strategy",
        "Apply Parameters",
        "파라미터 적용",
        "promotion 변경",
        "AI 자동매매 활성화",
        "ENABLE_AI_EXECUTION",
        "Place Order",
        "주문 실행",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("BUY/SELL/HOLD 같은 주문 신호 문구 0건", () => {
    const { container } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("악화 신호 / cautions / recommended_action 노출", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    expect(getByTestId("alpha-decay-signals").textContent).toContain("expectancy_drop");
    expect(getByTestId("alpha-decay-recommendation").textContent).toContain("자동 비활성");
    expect(getByTestId("alpha-decay-cautions").textContent).toContain("데이터 품질");
  });

  it("평가 버튼 라벨이 '알파 감쇠 평가'", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_HEALTHY} />,
    );
    const btn = getByTestId("alpha-decay-evaluate-btn");
    expect(btn.textContent.trim()).toBe("알파 감쇠 평가");
  });

  it("baseline vs recent 메트릭 비교가 표시", () => {
    const { getByTestId } = render(
      <AlphaDecayCard resultOverride={_CANDIDATE} />,
    );
    const deltas = getByTestId("alpha-decay-deltas");
    expect(deltas.textContent).toContain("expectancy");
    expect(deltas.textContent).toContain("profit_factor");
    expect(deltas.textContent).toContain("win_rate");
    expect(deltas.textContent).toContain("max_drawdown");
    expect(deltas.textContent).toContain("max_consecutive_losses");
  });
});
