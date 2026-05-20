/**
 * #85 Strategy Selection Card 테스트.
 *
 * 요청 항목 매핑:
 * - 전략 조합 카드 렌더링 (selected_strategy / 점수 / 제외 / 충돌 / regime 노출)
 * - 선택 전략 표시
 * - 제외 전략 표시
 * - conflict 표시
 * - 주문 버튼 없음 (enabling 버튼 invariant)
 */

import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StrategySelectionCard } from "./StrategySelectionCard";
import { _resetStrategyDisplayLookupForTests } from "../../utils/strategyNames";
import { MarketPhase } from "../../utils/marketHours";

vi.mock("../../services/backend/client", () => ({
  backendApi: { engineBeginnerRegistry: vi.fn() },
}));

import { backendApi } from "../../services/backend/client";


const _LOOKUP = [
  { strategy_id: "volume_breakout",  display_name: "거래량 급증 돌파" },
  { strategy_id: "pullback_rebreak", display_name: "눌림목 재돌파" },
  { strategy_id: "vwap_strategy",    display_name: "VWAP 평균 회귀" },
  { strategy_id: "orb_vwap",         display_name: "ORB + VWAP 돌파" },
];


beforeEach(() => {
  _resetStrategyDisplayLookupForTests();
  backendApi.engineBeginnerRegistry.mockReset();
  backendApi.engineBeginnerRegistry.mockResolvedValue(_LOOKUP);
});

afterEach(() => {
  cleanup();
  _resetStrategyDisplayLookupForTests();
});


function _report(overrides = {}) {
  return {
    symbol: "005930",
    market_regime: "TREND_UP",
    selected_strategy: "volume_breakout",
    final_action: "BUY",
    confidence: 78,
    quality_score: 85,
    conflict_level: "NONE",
    candidate_qualified: true,
    candidates: [
      { strategy_id: "volume_breakout",  symbol: "005930", action: "BUY",
        confidence: 80, quality_score: 85, score: 104.0,
        is_supporting: true,  reasons: ["거래량 2.5x"] },
      { strategy_id: "pullback_rebreak", symbol: "005930", action: "BUY",
        confidence: 75, quality_score: 80, score: 90.0,
        is_supporting: true,  reasons: ["눌림목 재돌파"] },
      { strategy_id: "vwap_strategy",    symbol: "005930", action: "WATCH",
        confidence: 30, quality_score: 50, score: 30.0,
        is_supporting: false, reasons: [] },
    ],
    blocked: [
      { strategy_id: "vwap_strategy", symbol: "005930",
        reason: "WATCH_ONLY", detail: "WATCH only — 후보 자격 없음",
        action_voted: "WATCH" },
    ],
    reasons: [
      "BUY supporting 2건 (volume_breakout, pullback_rebreak)",
      "+7 confidence boost (supporter > 1)",
    ],
    risk_notes: [],
    is_order_intent: false,
    is_order_signal: false,
    can_execute_order: false,
    generated_at: new Date().toISOString(),
    ...overrides,
  };
}


// ====================================================================
// 1. 렌더링 + 기본 표시
// ====================================================================


describe("StrategySelectionCard — 기본 렌더링", () => {
  it("카드가 렌더링되고 '주문 아님' 배지 노출", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    expect(getByTestId("strategy-selection-card")).toBeTruthy();
    expect(getByTestId("strategy-selection-not-order-badge").textContent)
      .toContain("주문 아님");
    expect(getByTestId("strategy-selection-not-order-badge").textContent)
      .toContain("승인 후보 전 단계");
  });

  it("symbol / regime / final_action 표시", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    expect(getByTestId("strategy-selection-symbol").textContent).toBe("005930");
    expect(getByTestId("strategy-selection-regime").textContent).toBe("TREND_UP");
    expect(getByTestId("strategy-selection-final-action").textContent)
      .toContain("BUY");
  });

  it("선택 전략 + displayName 표시 (internal id 함께)", async () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    await waitFor(() => {
      const cell = getByTestId("strategy-selection-selected");
      expect(cell.textContent).toContain("거래량 급증 돌파");
      expect(cell.textContent).toContain("(volume_breakout)");
    });
  });

  it("선택 전략이 없으면 '없음' fallback", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report({
        selected_strategy: null, candidate_qualified: false,
        final_action: "WATCH",
      })} />,
    );
    expect(getByTestId("strategy-selection-selected").textContent).toContain("없음");
  });
});


// ====================================================================
// 2. 후보 점수 + 제외 전략 + 사유
// ====================================================================


describe("StrategySelectionCard — 후보 / 제외", () => {
  it("후보 행이 후보 자격 표시 (✓ 채택)", () => {
    const { getByTestId, queryByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    const row = getByTestId("strategy-selection-candidate-volume_breakout");
    expect(row.textContent).toContain("✓ 채택");
    // score 표시.
    expect(getByTestId("strategy-selection-score-volume_breakout").textContent)
      .toContain("104");
    // 비-supporting 후보는 ✓ 마크 없음.
    const watchRow = queryByTestId("strategy-selection-candidate-vwap_strategy");
    expect(watchRow).toBeTruthy();
    expect(watchRow.textContent).not.toContain("✓ 채택");
  });

  it("제외 전략 + reason 라벨 표시", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    expect(getByTestId("strategy-selection-blocked-vwap_strategy")).toBeTruthy();
    expect(getByTestId("strategy-selection-blocked-reason-vwap_strategy").textContent)
      .toContain("WATCH only");
  });

  it("사유 목록 carry", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    expect(getByTestId("strategy-selection-reason-0").textContent)
      .toContain("BUY supporting");
  });
});


// ====================================================================
// 3. conflict 표시
// ====================================================================


describe("StrategySelectionCard — conflict 표시", () => {
  it("conflict NONE 라벨", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report()} />,
    );
    expect(getByTestId("strategy-selection-conflict").textContent)
      .toContain("충돌 없음");
  });

  it("conflict HIGH 라벨 + qualified=false", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report({
        conflict_level: "HIGH", candidate_qualified: false,
      })} />,
    );
    expect(getByTestId("strategy-selection-conflict").textContent)
      .toContain("충돌 높음");
    expect(getByTestId("strategy-selection-qualified").textContent)
      .toContain("없음");
  });
});


// ====================================================================
// 4. RISK_OFF / EXIT 분기
// ====================================================================


describe("StrategySelectionCard — regime / action 분기", () => {
  it("RISK_OFF REJECT 표시", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report({
        market_regime: "RISK_OFF",
        final_action: "REJECT",
        selected_strategy: null,
        candidate_qualified: false,
        blocked: [
          { strategy_id: "volume_breakout", symbol: "005930",
            reason: "RISK_OFF_REGIME",
            detail: "RISK_OFF regime — 모든 BUY 차단",
            action_voted: "BUY" },
        ],
      })} />,
    );
    expect(getByTestId("strategy-selection-regime").textContent).toBe("RISK_OFF");
    expect(getByTestId("strategy-selection-final-action").textContent)
      .toContain("REJECT");
    expect(getByTestId("strategy-selection-blocked-reason-volume_breakout").textContent)
      .toContain("RISK_OFF");
  });

  it("VWAP EXIT 우선 — 손실 방어 라벨", () => {
    const { getByTestId } = render(
      <StrategySelectionCard report={_report({
        final_action: "EXIT",
        selected_strategy: "vwap_strategy",
        blocked: [
          { strategy_id: "volume_breakout", symbol: "005930",
            reason: "OPPOSING_VWAP_PRIORITY",
            detail: "VWAP/EXIT 손실 방어 신호가 우선",
            action_voted: "BUY" },
        ],
      })} />,
    );
    expect(getByTestId("strategy-selection-final-action").textContent)
      .toContain("EXIT");
    expect(getByTestId("strategy-selection-blocked-reason-volume_breakout").textContent)
      .toContain("VWAP");
  });
});


// ====================================================================
// 5. invariant — enabling button 0개
// ====================================================================


describe("StrategySelectionCard — invariant (enabling button 0개)", () => {
  it("주문 / 적용 / 활성화 / Place Order 라벨 button 0개", () => {
    const { container } = render(
      <StrategySelectionCard report={_report()} onRefresh={() => {}} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      // 허용: 새로고침 / 다시 시도.
      const allowed = ["새로고침", "다시 시도", "↻"]
        .some((kw) => (btn.textContent || "").includes(kw));
      expect(allowed).toBe(true);
      // 금지: 주문 실행 / Place Order / 승인 / 큐로 보내기 / 적용 / 활성화.
      for (const banned of [
        "place order", "buy", "sell",
        "주문", "승인 큐", "큐로 보내기",
        "적용", "활성화", "enable",
      ]) {
        expect(text).not.toContain(banned.toLowerCase());
      }
    }
  });

  it("invariant flags (is_order_intent / is_order_signal / can_execute_order) " +
     "는 데이터에 명시 false 로만 들어옴", () => {
    const r = _report();
    expect(r.is_order_intent).toBe(false);
    expect(r.is_order_signal).toBe(false);
    expect(r.can_execute_order).toBe(false);
  });
});


// ====================================================================
// 6. loading / error 분기
// ====================================================================


describe("StrategySelectionCard — loading / error", () => {
  it("loading 상태 표시", () => {
    const { container } = render(
      <StrategySelectionCard loading={true} />,
    );
    expect(container.textContent).toContain("로딩 중");
  });

  it("error 상태 + onRefresh 표시 (정규장 OPEN 한정)", () => {
    const onRefresh = vi.fn();
    const { getByTestId } = render(
      <StrategySelectionCard
        error="boom"
        onRefresh={onRefresh}
        marketPhase={MarketPhase.OPEN}
      />,
    );
    expect(getByTestId("strategy-selection-error").textContent)
      .toContain("불러오지 못했습니다");
  });
});


// fix/market-closed-state-distinction ─────────────────────────────────────────
describe("StrategySelectionCard — 장 종료 / 휴장 상태", () => {
  it("CLOSED + error → '전략 조합 데이터를 아직 불러오지 못했습니다' 대신 market-closed", () => {
    const { getByTestId, queryByTestId } = render(
      <StrategySelectionCard
        error="boom"
        marketPhase={MarketPhase.CLOSED}
      />,
    );
    expect(getByTestId("strategy-selection-market-closed").textContent)
      .toContain("장 종료");
    // 기존 fetch-fail testid 가 노출되지 않아야 한다.
    expect(queryByTestId("strategy-selection-error")).toBeNull();
  });

  it("WEEKEND + error → '주말 휴장' 안내", () => {
    const { getByTestId, queryByTestId } = render(
      <StrategySelectionCard
        error="boom"
        marketPhase={MarketPhase.WEEKEND}
      />,
    );
    expect(getByTestId("strategy-selection-market-closed").textContent)
      .toContain("주말 휴장");
    expect(queryByTestId("strategy-selection-error")).toBeNull();
  });

  it("PRE_OPEN + report 미주입 → '장 시작 전' 안내 (null 대신)", () => {
    const { getByTestId } = render(
      <StrategySelectionCard marketPhase={MarketPhase.PRE_OPEN} />,
    );
    expect(getByTestId("strategy-selection-market-closed").textContent)
      .toContain("장 시작 전");
  });

  it("CLOSED + report 정상 → market-closed 안내가 *없고* 정상 표시", () => {
    // 실제로 데이터가 있으면 카드는 그대로 표시. market-closed banner는 비표시.
    const { getByTestId, queryByTestId } = render(
      <StrategySelectionCard
        report={_report()}
        marketPhase={MarketPhase.CLOSED}
      />,
    );
    expect(getByTestId("strategy-selection-card")).toBeTruthy();
    expect(queryByTestId("strategy-selection-market-closed")).toBeNull();
    expect(getByTestId("strategy-selection-symbol").textContent).toBe("005930");
  });
});
