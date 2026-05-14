import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { StrategyRegistryCard } from "./StrategyRegistryCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: { engineBeginnerRegistry: vi.fn() },
}));


const _SIX = [
  {
    strategy_id: "sma_crossover", internal_name: "SmaCrossoverStrategy",
    display_name: "단기/장기 이동평균 교차",
    beginner_name: "이평선 교차 추세 추종",
    description: "단기 이동평균선이 장기 이동평균선을 위로 뚫고 올라가면 매수.",
    risk_level: "medium", recommended_mode: "paper_recommended",
    typical_hold_minutes: null,
    notes: ["기본 파라미터: short=5, long=20"],
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    entry_rule: "short > long crossover",
    exit_rule: "short < long crossover",
    invalidation: "None",
    required_regime: "trend",
    risk_profile: { stop_loss_pct: 2 },
    parameters: [
      { name: "short", type: "int", default: 5, required: false },
      { name: "long",  type: "int", default: 20, required: false },
    ],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
  {
    strategy_id: "rsi_reversion", internal_name: "RsiReversionStrategy",
    display_name: "RSI 과매도/과매수 회복",
    beginner_name: "RSI 반등 / 반락 단타",
    description: "RSI 과매도/과매수 회복 시 매수/매도.",
    risk_level: "medium", recommended_mode: "paper_recommended",
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    notes: [],
    parameters: [],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
  {
    strategy_id: "vwap_strategy", internal_name: "VWAPStrategy",
    display_name: "VWAP 평균 회귀",
    beginner_name: "거래량가중평균 회복 단타",
    description: "VWAP 회복 시 매수.",
    risk_level: "medium", recommended_mode: "paper_recommended",
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    notes: [], parameters: [],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
  {
    strategy_id: "orb_vwap", internal_name: "OrbVwapStrategy",
    display_name: "ORB + VWAP 돌파",
    beginner_name: "시가 범위(ORB) 돌파 단타",
    description: "ORB 상단 돌파 + VWAP 위.",
    risk_level: "high", recommended_mode: "paper_recommended",
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    notes: [], parameters: [],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
  {
    strategy_id: "volume_breakout", internal_name: "VolumeBreakoutStrategy",
    display_name: "거래량 급증 돌파",
    beginner_name: "거래대금 급증 + 신고가 돌파 단타",
    description: "거래량 급증 + 고점 돌파.",
    risk_level: "high", recommended_mode: "paper_recommended",
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    notes: [], parameters: [],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
  {
    strategy_id: "pullback_rebreak", internal_name: "PullbackRebreakStrategy",
    display_name: "눌림목 재돌파",
    beginner_name: "상승 임펄스 → 거래량 눌림 → 재돌파 단타",
    description: "상승 후 눌림목 → 재돌파.",
    risk_level: "high", recommended_mode: "paper_recommended",
    supported_modes: ["SIMULATION", "PAPER", "LIVE_SHADOW", "LIVE_MANUAL_APPROVAL"],
    backtest_available: true, paper_trading_available: true, live_trading_available: false,
    notes: [], parameters: [],
    is_order_signal: false, auto_apply_allowed: false, is_investment_advice: false,
  },
];


afterEach(cleanup);


describe("StrategyRegistryCard", () => {
  it("6개 전략을 모두 렌더링", () => {
    const { getByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    expect(getByTestId("strategy-registry-count").textContent).toBe("6개");
    for (const id of ["sma_crossover", "rsi_reversion", "vwap_strategy",
                      "orb_vwap", "volume_breakout", "pullback_rebreak"]) {
      expect(getByTestId(`strategy-row-${id}`)).toBeTruthy();
    }
  });

  it("displayName(한글) 과 internal id(괄호) 가 함께 표시", () => {
    const { getByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const sma = getByTestId("strategy-row-sma_crossover").textContent;
    expect(sma).toContain("단기/장기 이동평균 교차");
    expect(sma).toContain("(sma_crossover)");
    expect(sma).toContain("이평선 교차 추세 추종");  // beginner name
  });

  it("위험도 배지가 색상별로 표시", () => {
    const { getAllByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const med = getAllByTestId("strategy-risk-medium");
    const hi  = getAllByTestId("strategy-risk-high");
    expect(med.length).toBe(3);  // sma / rsi / vwap
    expect(hi.length).toBe(3);   // orb / volume / pullback
    expect(med[0].textContent).toContain("위험도 보통");
    expect(hi[0].textContent).toContain("위험도 높음");
  });

  it("모든 전략의 실전투자(live) 가용성이 false 로 표시", () => {
    const { getAllByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    for (const id of _SIX.map((e) => e.strategy_id)) {
      const live = getAllByTestId(`strategy-${id}-live`)[0];
      expect(live.textContent).toContain("실전투자");
      expect(live.textContent.startsWith("—")).toBe(true);  // 비활성 마크
    }
  });

  it("disclaimer 가 영구 노출 + BotControl 안내 포함", () => {
    const { getByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const d = getByTestId("strategy-registry-disclaimer").textContent;
    expect(d).toContain("메타데이터 표시");
    expect(d).toContain("불가능");
    expect(d).toContain("BotControl");
  });

  it("전략 활성화 / 비활성화 / 주문 실행 / Apply 버튼 0개", () => {
    const { container } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "전략 활성화", "전략 비활성화",
        "Apply Parameters", "파라미터 적용",
        "전략 시작", "전략 중단",
        "주문 실행", "Place Order",
        "활성화 토글", "ENABLE_LIVE_TRADING",
        "실거래 활성화",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("세부 정보 펼치기 / 접기 토글", () => {
    const { getByTestId, queryByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    expect(queryByTestId("strategy-sma_crossover-detail")).toBeNull();
    fireEvent.click(getByTestId("strategy-sma_crossover-toggle-detail"));
    const d = getByTestId("strategy-sma_crossover-detail");
    expect(d.textContent).toContain("매수 규칙");
    expect(d.textContent).toContain("short=5");
    expect(d.textContent).toContain("long=20");
  });

  it("BUY/SELL/HOLD 주문 신호 라벨 / 가짜 전략명 0건", () => {
    const { container } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const text = container.textContent || "";
    for (const banned of [
      "매수 실행", "매도 실행", "BUY signal", "SELL signal", "HOLD signal",
      "긴급정지 토글",
      "골든브릿지", "트라이앵글 전설", "다이아 전략", "퀀텀 점프",
      "황금알", "100% 승률",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("권장 모드 / 백테스트 / 모의투자 가용 칩 노출", () => {
    const { getAllByTestId } = render(
      <StrategyRegistryCard registryOverride={_SIX} />,
    );
    const bt = getAllByTestId("strategy-sma_crossover-backtest")[0];
    expect(bt.textContent.startsWith("✓")).toBe(true);
    const pp = getAllByTestId("strategy-sma_crossover-paper")[0];
    expect(pp.textContent.startsWith("✓")).toBe(true);
  });
});
