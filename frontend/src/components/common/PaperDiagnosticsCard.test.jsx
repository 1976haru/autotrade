/**
 * PaperDiagnosticsCard — 단위 테스트.
 *
 * 사용자 요청서 §7 frontend 필수 테스트:
 *  - "관심종목이 없어 시가총액 상위 50개 기본 Universe 를 사용합니다." 표시
 *  - fallback universe 안내 표시
 *  - "오늘 주문 0건 원인" 표시
 *  - Universe count 표시
 *  - market data provider 표시
 *  - strategy engine 연결 상태 표시
 *  - permission gate 차단 사유 표시
 *  - PAPER 가상 실행 차단 메시지 표시
 *  - 자동봇 미연동 메시지 표시
 *  - 기존 테스트 깨지지 않음
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { PaperDiagnosticsCard } from "./PaperDiagnosticsCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    paperDiagnosticsPreflight: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


// 한 곳에서 base report 만들고 case 별로 override.
function makeReport(overrides = {}) {
  return {
    primary_block_reason:  "USING_FALLBACK_UNIVERSE",
    blocking_reasons:      [],
    warnings:              ["USING_FALLBACK_UNIVERSE", "LIVE_DISABLED_SAFE",
                            "AI_EXECUTION_DISABLED_SAFE"],
    summary_ko:            "PAPER 자동매매 사전 점검 통과 (경고 3건)",
    universe_source:       "FALLBACK_MARKET_CAP_TOP50",
    universe_count:        50,
    universe_fallback_used: true,
    universe_warning_ko:   "사용자 관심종목이 없어 시가총액 상위 50개 fallback universe 를 사용합니다. 본 목록은 PAPER 테스트용이며 최신 시가총액 순위 보장이 아니고 투자 추천이 아닙니다.",
    market_data_provider:  "mock",
    default_mode:          "PAPER",
    enable_live_trading:   false,
    enable_ai_execution:   false,
    enable_futures_live_trading: false,
    kis_is_paper:          true,
    auto_bot_state:        "RUNNING",
    auto_bot_running:      true,
    strategy_engine_connected: true,
    last_decision_count:   1,
    last_ledger_events:    1,
    last_decision_log_count: 1,
    paper_virtual_execution_allowed: true,
    live_execution_blocked: true,
    frontend_mode:         null,
    mode_mismatch:         false,
    has_blocking:          false,
    blocking_messages_ko:  [],
    warning_messages_ko:   [
      "사용자 관심종목이 없어 시가총액 상위 50개 기본 Universe (fallback) 를 사용합니다. 본 목록은 PAPER 테스트용이며 투자 추천이 아닙니다.",
      "실거래(LIVE) 가 안전하게 비활성화되어 있습니다 (ENABLE_LIVE_TRADING=false). 이는 정상 안전 상태이며 PAPER / SIMULATION 검증을 차단하지 않습니다.",
    ],
    next_actions_ko:       [
      "정식 관심종목 등록을 권장합니다 (fallback 은 PAPER 테스트용).",
    ],
    is_paper_safe_only:    true,
    is_live_authorization: false,
    is_order_signal:       false,
    advisory_disclaimer:   "본 응답은 PAPER 검증 advisory — broker / route_order / OrderExecutor 호출 0건.",
    ...overrides,
  };
}


beforeEach(() => {
  backendApi.paperDiagnosticsPreflight.mockReset();
});

afterEach(cleanup);


describe("<PaperDiagnosticsCard>", () => {
  it("renders headline section '오늘 주문 0건 원인'", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    const { container } = render(<PaperDiagnosticsCard />);
    await waitFor(() => screen.getByTestId("paper-diagnostics-card-summary-ko"));
    expect(container.textContent).toContain("오늘 주문 0건 원인");
  });

  it("shows '관심종목이 없어 시가총액 상위 50개 기본 Universe 를 사용합니다.'", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-universe-fallback-warning"),
    );
    const text = screen
      .getByTestId("paper-diagnostics-card-universe-fallback-warning")
      .textContent || "";
    expect(text).toContain("관심종목이 없어");
    expect(text).toContain("시가총액 상위 50");
    expect(text).toContain("기본 Universe");
  });

  it("displays Universe source and count", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-universe-source"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-universe-source").textContent,
    ).toContain("FALLBACK_MARKET_CAP_TOP50");
    expect(
      screen.getByTestId("paper-diagnostics-card-universe-count").textContent,
    ).toContain("50");
  });

  it("displays market data provider", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-market-provider"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-market-provider")
        .textContent,
    ).toContain("mock");
  });

  it("displays strategy engine connection status", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({ strategy_engine_connected: true }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-strategy-engine"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-strategy-engine")
        .textContent,
    ).toContain("연결됨");
  });

  it("displays '미연동' when strategy engine disconnected", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({
        strategy_engine_connected: false,
        primary_block_reason: "STRATEGY_ENGINE_NOT_CONNECTED",
        has_blocking: true,
        blocking_reasons: ["STRATEGY_ENGINE_NOT_CONNECTED"],
        blocking_messages_ko: [
          "전략 엔진이 자동봇과 연결되지 않아 신호가 생성되지 않았습니다.",
        ],
      }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-strategy-engine"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-strategy-engine")
        .textContent,
    ).toContain("미연동");
    // 자동봇 미연동 별도 banner 도 노출.
    expect(
      screen.getByTestId("paper-diagnostics-card-autobot-not-connected-banner")
        .textContent,
    ).toContain("backend strategy loop 와 연결되지 않았습니다");
  });

  it("displays PAPER 가상 실행 차단 메시지 when paper_virtual_execution_allowed=false", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({
        paper_virtual_execution_allowed: false,
        primary_block_reason: "PAPER_EXECUTION_DISABLED",
        has_blocking: true,
        blocking_reasons: ["PAPER_EXECUTION_DISABLED"],
        blocking_messages_ko: [
          "현재 PAPER 모드이나 가상 실행이 차단되어 주문이 생성되지 않았습니다.",
        ],
      }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-paper-blocked-banner"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-paper-blocked-banner")
        .textContent,
    ).toContain("PAPER 모드이나 가상 실행이 차단");
  });

  it("displays permission gate status (허용)", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-permission"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-permission")
        .textContent,
    ).toContain("허용");
  });

  it("displays permission gate status (차단) when blocked", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({ paper_virtual_execution_allowed: false }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-permission"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-permission")
        .textContent,
    ).toContain("차단");
  });

  it("renders blocking messages list when blocking_messages_ko present", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({
        primary_block_reason: "NO_UNIVERSE",
        has_blocking: true,
        blocking_reasons: ["NO_UNIVERSE"],
        universe_source: "EMPTY",
        universe_count: 0,
        universe_fallback_used: false,
        blocking_messages_ko: [
          "관심종목이 없고 fallback universe 도 사용할 수 없어 후보 종목 0건.",
        ],
      }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-blocking-messages"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-blocking-message-0").textContent,
    ).toContain("관심종목이 없고");
  });

  it("renders warning messages list when warning_messages_ko present", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-warning-messages"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-warning-message-0").textContent,
    ).toContain("시가총액 상위 50");
  });

  it("renders next actions list", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-next-actions"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-next-action-0").textContent,
    ).toContain("관심종목 등록");
  });

  it("displays primary block reason headline with code", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({
        primary_block_reason: "AUTO_BOT_NOT_RUNNING",
        has_blocking: true,
        blocking_reasons: ["AUTO_BOT_NOT_RUNNING"],
        summary_ko: "PAPER 자동매매 주문 0건 — 주요 사유: AUTO_BOT_NOT_RUNNING",
        auto_bot_state: "PAUSED",
        auto_bot_running: false,
        blocking_messages_ko: [
          "자동봇 backend loop 가 RUNNING 상태가 아니라 tick 이 발생하지 않았습니다.",
        ],
      }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-primary-reason-code"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-primary-reason-code")
        .textContent,
    ).toBe("AUTO_BOT_NOT_RUNNING");
    expect(
      screen.getByTestId("paper-diagnostics-card-summary-ko").textContent,
    ).toContain("AUTO_BOT_NOT_RUNNING");
  });

  it("shows disclaimer + invariant badges", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-disclaimer"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-disclaimer").textContent,
    ).toContain("진단");
    const badges = screen.getByTestId(
      "paper-diagnostics-card-invariant-badges",
    ).textContent || "";
    expect(badges).toContain("Paper");
    expect(badges).toContain("broker 호출 0건");
    expect(badges).toContain("실거래 권한 없음");
  });

  it("error response shows fallback message", async () => {
    backendApi.paperDiagnosticsPreflight.mockRejectedValue(new Error("boom"));
    render(<PaperDiagnosticsCard />);
    await waitFor(() => screen.getByTestId("paper-diagnostics-card-error"));
    expect(
      screen.getByTestId("paper-diagnostics-card-error").textContent,
    ).toContain("boom");
  });

  it("has NO trade-execution / live-order action buttons or labels", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    const { container } = render(<PaperDiagnosticsCard />);
    await waitFor(() => screen.getByTestId("paper-diagnostics-card"));
    const text = container.textContent || "";
    // 주문 실행 / 활성화 라벨 차단. ENABLE_* 환경변수명 자체는 *경고
    // 메시지* (정상 안전 상태 안내) 에 나타날 수 있으므로 ban 대상에서 제외.
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "place order",
      "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화",
    ]) {
      expect(text.includes(banned)).toBe(false);
    }
    // *button* element 자체에 실행 라벨이 없는지 검증.
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = (b.textContent || "").trim();
      expect(t).not.toContain("매수");
      expect(t).not.toContain("매도");
      expect(t).not.toContain("실거래");
      expect(t.toLowerCase()).not.toContain("place order");
    }
  });

  it("has NO input / textarea / select form elements", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    const { container } = render(<PaperDiagnosticsCard />);
    await waitFor(() => screen.getByTestId("paper-diagnostics-card"));
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("does not expose secret patterns", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(makeReport());
    const { container } = render(<PaperDiagnosticsCard />);
    await waitFor(() => screen.getByTestId("paper-diagnostics-card"));
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "openai_api_key", "telegram_bot_token", "sk-ant-",
      "kis_account_no",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("displays mode mismatch indicator when frontend_mode differs", async () => {
    backendApi.paperDiagnosticsPreflight.mockResolvedValue(
      makeReport({
        frontend_mode: "LIVE",
        mode_mismatch: true,
        primary_block_reason: "MODE_MISMATCH",
        has_blocking: true,
        blocking_reasons: ["MODE_MISMATCH"],
        blocking_messages_ko: [
          "frontend 표시 모드와 backend runtime mode 가 일치하지 않습니다 — 운영자가 모드를 변경한 후 backend 가 반영되지 않았을 수 있습니다.",
        ],
      }),
    );
    render(<PaperDiagnosticsCard />);
    await waitFor(() =>
      screen.getByTestId("paper-diagnostics-card-runtime-mode"),
    );
    expect(
      screen.getByTestId("paper-diagnostics-card-runtime-mode").textContent,
    ).toContain("LIVE");
  });
});
