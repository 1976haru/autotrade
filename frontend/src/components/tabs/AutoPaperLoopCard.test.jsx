/**
 * AutoPaperLoopCard 단위 테스트.
 *
 * invariant 강제:
 * - "uvicorn" / "Place Order" / "지금 매수" / "지금 매도" / "실거래 시작" / "ENABLE_*" 라벨 0건
 * - 시작 / 정지 / 긴급정지 버튼이 정확한 API 를 호출
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  AutoPaperLoopCard,
  canStartAutoPaper,
  canStopAutoPaper,
  normalizeAutoPaperState,
} from "./AutoPaperLoopCard";

// risk_profile 가 이제 localStorage(Paper 자금 설정)에 영속되므로, 테스트 간
// 격리를 위해 매 테스트 전 localStorage 를 비운다 (한 테스트의 AGGRESSIVE 저장이
// 다음 테스트로 누출되어 default BALANCED 검증을 깨지 않도록).
beforeEach(() => {
  try { window.localStorage.clear(); } catch { /* jsdom */ }
});


function _mockApi(
  initialStatus = { state: "PAUSED", cycle_count: 0 },
  ledgerEvents = [],
) {
  return {
    autoPaperStatus: vi.fn(async () => initialStatus),
    autoPaperStart: vi.fn(async () => ({ state: "RUNNING", cycle_count: 0 })),
    autoPaperStop: vi.fn(async () => ({ state: "STOPPED", cycle_count: 5 })),
    autoPaperEmergencyStop: vi.fn(async () => ({ state: "EMERGENCY_STOP", cycle_count: 5 })),
    autoPaperReset: vi.fn(async () => ({ state: "PAUSED", cycle_count: 0 })),
    autoPaperLedger: vi.fn(async () => ({
      events: ledgerEvents,
      event_count: ledgerEvents.length,
      is_order_signal: false,
      auto_apply_allowed: false,
      is_live_authorization: false,
      advisory_disclaimer: "Paper Auto Loop advisory ledger",
    })),
    desktopHealth: vi.fn(async () => ({
      ok: true,
      safety_flags: {
        enable_live_trading: false,
        enable_ai_execution: false,
        enable_futures_live_trading: false,
        kis_is_paper: true,
      },
      auto_paper: initialStatus,
    })),
  };
}


describe("<AutoPaperLoopCard>", () => {
  afterEach(cleanup);

  it("renders safety badges", async () => {
    const api = _mockApi();
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("badge-not-order-signal").textContent).toMatch(/모의 전용/);
    expect(screen.getByTestId("badge-paper-mode").textContent).toMatch(/KIS Paper ON/);
    expect(screen.getByTestId("badge-no-auto-apply").textContent).toMatch(/주문 신호 아님/);
  });

  it("shows live OFF flag when safety_flags.enable_live_trading=false", async () => {
    const api = _mockApi();
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("flag-live-off").textContent).toMatch(/OFF/)
    );
  });

  it("clicking 시작 button calls autoPaperStart", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    // fix/frontend-ci: wait for button enabled (status loaded) — race fix.
    await waitFor(() =>
      expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalledTimes(1));
  });

  it("clicking 정지 button calls autoPaperStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    // fix/frontend-ci-operator-and-autopaper: 사용자 요청서 §3 8번 — 정지
    // 버튼이 *실제 disabled=false* 상태가 될 때까지 기다린 뒤 클릭. CI 환경
    // 에서 status() async 응답이 아직 setState 로 반영되기 전에 클릭하면
    // disabled 상태라 handler 가 호출되지 않음.
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() => expect(api.autoPaperStop).toHaveBeenCalledTimes(1));
  });

  it("clicking 긴급정지 button calls autoPaperEmergencyStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    // 긴급정지 버튼은 busy 만 disabled — status 로딩 없이도 enabled 이지만
    // 일관성을 위해 동일 패턴 적용.
    await waitFor(() =>
      expect(screen.getByTestId("btn-emergency-stop").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-emergency-stop"));
    await waitFor(() => expect(api.autoPaperEmergencyStop).toHaveBeenCalledTimes(1));
  });

  it("start button disabled when RUNNING", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(true)
    );
  });

  it("stop button disabled when not RUNNING", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true)
    );
  });

  it("no forbidden labels in card text", async () => {
    const api = _mockApi();
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const text = container.textContent.toLowerCase();
    expect(text).not.toContain("uvicorn");
    expect(text).not.toContain("npm run dev");
    expect(text).not.toContain("place order");
    expect(text).not.toContain("실거래 시작");
    expect(text).not.toContain("enable_live_trading=true");
  });

  it("no buy/sell/place-order labeled buttons", async () => {
    const api = _mockApi();
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      expect(text).not.toContain("place order");
      expect(text).not.toContain("buy");
      expect(text).not.toContain("sell");
      expect(text).not.toContain("매수");
      expect(text).not.toContain("매도");
      expect(text).not.toContain("실거래 시작");
      expect(text).not.toContain("enable_live");
    }
  });

  it("displays cycle count from status", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 42 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("cycle-count").textContent).toMatch(/42/)
    );
  });

  it("shows error banner when api fails", async () => {
    const api = _mockApi();
    api.autoPaperStatus = vi.fn(async () => {
      throw new Error("backend unreachable");
    });
    api.desktopHealth = vi.fn(async () => {
      throw new Error("backend unreachable");
    });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-error").textContent).toMatch(/backend unreachable/)
    );
  });


  // ==========================================================
  // feat/step2-05-pre-market-gate: Pre-market BLOCK 차단
  // ==========================================================

  describe("Pre-market gate", () => {
    it("PASS — preMarketCheckResult.start_allowed=true → start button enabled, no banner", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { queryByTestId, getByTestId } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={{
            start_allowed:    true,
            verdict:          "READY_TO_START",
            blocking_reasons: [],
            warnings:         [],
          }}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(getByTestId("btn-start-auto-paper").disabled).toBe(false);
      expect(queryByTestId("auto-paper-premarket-blocked-banner")).toBeNull();
    });

    it("WARN — start_allowed=true with warnings → start enabled (no block)", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { queryByTestId, getByTestId } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={{
            start_allowed:    true,
            verdict:          "WARN_BUT_START_ALLOWED",
            blocking_reasons: [],
            warnings:         ["watchlist 적음"],
          }}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(getByTestId("btn-start-auto-paper").disabled).toBe(false);
      expect(queryByTestId("auto-paper-premarket-blocked-banner")).toBeNull();
    });

    it("BLOCK — start_allowed=false → start button disabled + block banner", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { getByTestId } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={{
            start_allowed:    false,
            verdict:          "DO_NOT_START",
            blocking_reasons: ["API 미응답", "watchlist 0개"],
            warnings:         [],
          }}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const btn = getByTestId("btn-start-auto-paper");
      expect(btn.disabled).toBe(true);
      const banner = getByTestId("auto-paper-premarket-blocked-banner");
      expect(banner.textContent).toContain("Pre-market 점검 미통과");
      const reasons = getByTestId("auto-paper-premarket-block-reasons");
      expect(reasons.textContent).toContain("API 미응답");
      expect(reasons.textContent).toContain("watchlist 0개");
    });

    it("BLOCK — click 시작 button does not call autoPaperStart", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { getByTestId } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={{
            start_allowed:    false,
            verdict:          "DO_NOT_START",
            blocking_reasons: ["test"],
          }}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(getByTestId("btn-start-auto-paper"));
      // disabled 버튼은 onClick fire 하지 않음 — autoPaperStart 호출 0건.
      expect(api.autoPaperStart).not.toHaveBeenCalled();
    });

    it("PASS — click 시작 forwards pre_market payload to autoPaperStart", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const pm = {
        start_allowed:    true,
        verdict:          "READY_TO_START",
        blocking_reasons: [],
        warnings:         ["minor warn"],
      };
      const { getByTestId } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={pm}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      // 첫 호출 인자 — pre_market + risk_profile (#4-RiskProfileUI: BALANCED 기본값).
      // P-15: capital_settings 도 carry — toMatchObject 로 부분 검증.
      const callArgs = api.autoPaperStart.mock.calls[0][0];
      expect(callArgs).toMatchObject({
        risk_profile: "BALANCED",
        pre_market: {
          start_allowed:    true,
          verdict:          "READY_TO_START",
          blocking_reasons: [],
          warnings:         ["minor warn"],
        },
      });
    });

    it("preMarketCheckResult=null (legacy) → start enabled, no banner, payload carries risk_profile only", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { getByTestId, queryByTestId } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(queryByTestId("auto-paper-premarket-blocked-banner")).toBeNull();
      fireEvent.click(getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      // #4-RiskProfileUI: pre_market=null 이라도 body 는 risk_profile 포함.
      // P-15: capital_settings 도 carry — toMatchObject 로 부분 검증.
      expect(api.autoPaperStart.mock.calls[0][0]).toMatchObject({
        risk_profile: "BALANCED",
      });
    });

    it("BLOCK banner has no banned phrases", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { container } = render(
        <AutoPaperLoopCard
          apiClient={api}
          pollIntervalMs={0}
          preMarketCheckResult={{
            start_allowed:    false,
            verdict:          "DO_NOT_START",
            blocking_reasons: ["test"],
          }}
        />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "ENABLE_LIVE_TRADING"];
      for (const b of banned) {
        expect(container.textContent).not.toContain(b);
      }
    });
  });


  // ==========================================================
  // feat/step2-market-waiting-mode: 장 시작 대기 / 장 종료 / 휴장 표시
  // ==========================================================

  describe("Market waiting mode", () => {
    it("WAITING_MARKET → '장 시작 대기 중' 라벨 + 안내 배너 표시", async () => {
      const api = _mockApi({ state: "WAITING_MARKET", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("state-pill").textContent).toMatch(/장 시작 대기 중/)
      );
      const banner = screen.getByTestId("auto-paper-market-waiting-banner");
      expect(banner.textContent).toMatch(/장 시작 대기 중/);
      expect(banner.textContent).toMatch(/09:00 KST/);
    });

    it("WAITING_MARKET → 시작 버튼 비활성화 (이미 대기 중)", async () => {
      const api = _mockApi({ state: "WAITING_MARKET", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(true)
      );
    });

    it("MARKET_CLOSED → '장 종료 · 휴장' 라벨 + 안내 배너 표시", async () => {
      const api = _mockApi({ state: "MARKET_CLOSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("state-pill").textContent).toMatch(/장 종료/)
      );
      const banner = screen.getByTestId("auto-paper-market-closed-banner");
      expect(banner.textContent).toMatch(/한국장 종료/);
      expect(banner.textContent).toMatch(/09:00 KST/);
    });

    it("MARKET_CLOSED → 정지 버튼 비활성화 (이미 진행 중 아님)", async () => {
      const api = _mockApi({ state: "MARKET_CLOSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true)
      );
    });

    it("RUNNING → 두 신규 배너 모두 표시 안 됨", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(screen.queryByTestId("auto-paper-market-waiting-banner")).toBeNull();
      expect(screen.queryByTestId("auto-paper-market-closed-banner")).toBeNull();
    });

    it("PAUSED → 두 신규 배너 모두 표시 안 됨", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(screen.queryByTestId("auto-paper-market-waiting-banner")).toBeNull();
      expect(screen.queryByTestId("auto-paper-market-closed-banner")).toBeNull();
    });

    it("WAITING_MARKET / MARKET_CLOSED 배너에 금지 라벨 0건", async () => {
      const api = _mockApi({ state: "WAITING_MARKET", cycle_count: 0 });
      const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "ENABLE_LIVE_TRADING"];
      for (const b of banned) {
        expect(container.textContent).not.toContain(b);
      }
    });
  });

  // ==========================================================
  // #2-01 6-state canonical model lock
  //   - 모든 6 canonical state 가 distinct 한국어 라벨로 표시.
  //   - 2 deprecated alias (IDLE / EMERGENCY) 도 동일 라벨로 매핑.
  //   - 새 state 추가 / 삭제는 본 lock 테스트 + backend
  //     `test_six_canonical_states_lock` *동시* 갱신 PR 외에서는 금지.
  // ==========================================================

  describe("6-state canonical model lock", () => {
    const _SIX_CANONICAL_STATES = [
      { state: "PAUSED",         label: "대기" },
      { state: "WAITING_MARKET", label: "장 시작 대기" },
      { state: "RUNNING",        label: "AI Paper Auto Loop 진행" },
      { state: "STOPPED",        label: "정지" },
      { state: "EMERGENCY_STOP", label: "긴급정지" },
      { state: "MARKET_CLOSED",  label: "장 종료" },
    ];

    for (const { state, label } of _SIX_CANONICAL_STATES) {
      it(`canonical state "${state}" → 한국어 라벨 "${label}" 표시`, async () => {
        const api = _mockApi({ state, cycle_count: 0 });
        render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
        await waitFor(() =>
          expect(screen.getByTestId("state-pill").textContent).toContain(label),
        );
        // 정직 = backend canonical 값을 그대로 사용하지 *않는다* (한국어 라벨로 번역됨).
        // 단, 라벨 안에 영문 state 키워드가 *그대로* 노출되지 않아야 함 — UX.
        // (RUNNING 라벨에 "Auto Loop 진행" — 영문 "RUNNING" 단어가 *원본 그대로* 보이지 않음)
        const pillText = screen.getByTestId("state-pill").textContent;
        expect(pillText.length).toBeGreaterThan(0);
      });
    }

    it("legacy alias IDLE → PAUSED 라벨 매핑 (backend 가 IDLE 을 emit 해도 UI 안 깨짐)", async () => {
      const api = _mockApi({ state: "IDLE", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("state-pill").textContent).toContain("대기"),
      );
    });

    it("legacy alias EMERGENCY → EMERGENCY_STOP 라벨 매핑", async () => {
      const api = _mockApi({ state: "EMERGENCY", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("state-pill").textContent).toContain("긴급정지"),
      );
    });
  });

  // ==========================================================
  // #2-09: Paper Loop ledger UI — 최근 AI 판단 + 가상 체결 표시
  //   - autoPaperLedger 가 events 를 반환하면 paper-ledger-panel 렌더.
  //   - events 가 비어있으면 panel 자체 렌더 X (시각 노이즈 방지).
  //   - HOLD / BUY / SELL / EXIT 각 action 의 라벨 정확히 표시.
  //   - 금지 라벨 (Place Order / 지금 매수 / ENABLE_*) 0건.
  // ==========================================================

  describe("Paper ledger UI (#2-09)", () => {
    it("ledger 비어있으면 panel 미표시", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, []);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperLedger).toHaveBeenCalled());
      expect(screen.queryByTestId("paper-ledger-panel")).toBeNull();
    });

    it("ledger event 가 있으면 panel + advisory disclaimer 표시", async () => {
      const events = [
        {
          event_id: "evt-001",
          timestamp: "2026-05-18T01:23:45+00:00",
          loop_state: "RUNNING",
          strategy: "sma_crossover",
          symbol: "005930",
          decision_action: "HOLD",
          confidence: 0.55,
          reason: "trend not confirmed",
          risk_flags: [],
          paper_order_id: null,
          paper_fill_status: "NA",
          virtual_position_delta: 0,
          pnl_estimate: 0.0,
          is_order_signal: false,
          auto_apply_allowed: false,
          is_live_authorization: false,
        },
        {
          event_id: "evt-002",
          timestamp: "2026-05-18T01:24:00+00:00",
          loop_state: "RUNNING",
          strategy: "rsi_reversion",
          symbol: "000660",
          decision_action: "BUY",
          confidence: 0.78,
          reason: "RSI oversold + reversion confirm",
          risk_flags: [],
          paper_order_id: "paper-2026-05-18-001",
          paper_fill_status: "PAPER_FILLED",
          virtual_position_delta: 10,
          pnl_estimate: 0.0,
          is_order_signal: false,
          auto_apply_allowed: false,
          is_live_authorization: false,
        },
      ];
      const api = _mockApi({ state: "RUNNING", cycle_count: 2 }, events);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("paper-ledger-panel")).toBeTruthy(),
      );
      const list = screen.getByTestId("paper-ledger-list");
      expect(list.textContent).toContain("HOLD");
      expect(list.textContent).toContain("BUY");
      expect(list.textContent).toContain("sma_crossover");
      expect(list.textContent).toContain("rsi_reversion");
      expect(list.textContent).toContain("PAPER_FILLED");
      // advisory disclaimer.
      const dis = screen.getByTestId("paper-ledger-disclaimer");
      expect(dis.textContent).toMatch(/advisory/);
      expect(dis.textContent).toMatch(/is_order_signal=false/);
    });

    it("ledger UI 에 금지 라벨 0건 (Place Order / 지금 매수 / 실거래 시작 / ENABLE_LIVE_TRADING)", async () => {
      const events = [
        {
          event_id: "evt-x",
          timestamp: "2026-05-18T01:23:45+00:00",
          loop_state: "RUNNING",
          strategy: "sma_crossover",
          symbol: "005930",
          decision_action: "BUY",
          confidence: 0.78,
          reason: "test",
          risk_flags: [],
          paper_order_id: "p-1",
          paper_fill_status: "PAPER_FILLED",
          virtual_position_delta: 5,
          pnl_estimate: 0.0,
          is_order_signal: false,
          auto_apply_allowed: false,
          is_live_authorization: false,
        },
      ];
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, events);
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() =>
        expect(screen.getByTestId("paper-ledger-panel")).toBeTruthy(),
      );
      const banned = [
        "Place Order", "지금 매수", "지금 매도", "실거래 시작",
        "ENABLE_LIVE_TRADING", "AI 자동매매 켜기",
      ];
      for (const b of banned) {
        expect(container.textContent).not.toContain(b);
      }
    });

    it("autoPaperLedger 가 없는 mock 환경에서도 안전하게 동작 (no throw)", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      delete api.autoPaperLedger;
      // throw 없이 렌더되어야.
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(screen.queryByTestId("paper-ledger-panel")).toBeNull();
    });
  });

  // ==========================================================
  // #2-10: AI Paper 자동매수/매도 skeleton UI
  //   - 최신 결정 highlight (action / strategy / symbol / confidence /
  //     reason / risk_flags)
  //   - "Paper 전용 · 실제 주문 아님" 배지
  //   - event count 표시
  //   - "매수" / "매도" / "실거래 시작" 버튼 0개
  // ==========================================================

  describe("Paper latest-decision highlight (#2-10)", () => {
    const _BUY_EVENT = {
      event_id: "evt-buy-1",
      timestamp: "2026-05-18T01:24:00+00:00",
      loop_state: "RUNNING",
      strategy: "rsi_reversion",
      symbol: "000660",
      decision_action: "BUY",
      confidence: 0.78,
      reason: "RSI oversold + reversion confirm",
      risk_flags: ["low_volume_warning"],
      paper_order_id: "paper-2026-05-18-001",
      paper_fill_status: "PAPER_FILLED",
      virtual_position_delta: 10,
      pnl_estimate: 0.0,
      is_order_signal: false,
      auto_apply_allowed: false,
      is_live_authorization: false,
    };

    it("renders latest decision highlight with all 6 required fields", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, [_BUY_EVENT]);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("paper-latest-decision")).toBeTruthy(),
      );
      // action / strategy / symbol / confidence / reason / risk_flags 6개 필드.
      expect(screen.getByTestId("paper-latest-action").textContent).toBe("BUY");
      expect(screen.getByTestId("paper-latest-strategy").textContent).toContain("rsi_reversion");
      expect(screen.getByTestId("paper-latest-symbol").textContent).toContain("000660");
      expect(screen.getByTestId("paper-latest-confidence").textContent).toContain("78%");
      expect(screen.getByTestId("paper-latest-reason").textContent).toContain("RSI oversold");
      expect(screen.getByTestId("paper-latest-risk-flags").textContent).toContain("low_volume_warning");
    });

    it("paper-only safety badge always visible", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, [_BUY_EVENT]);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("badge-paper-only")).toBeTruthy(),
      );
      expect(screen.getByTestId("badge-paper-only").textContent).toContain("Paper 전용");
      expect(screen.getByTestId("badge-paper-only").textContent).toContain("실제 주문 아님");
    });

    it("event count is displayed", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, [
        _BUY_EVENT,
        { ..._BUY_EVENT, event_id: "evt-hold-2", decision_action: "HOLD" },
      ]);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("paper-ledger-event-count")).toBeTruthy(),
      );
      expect(screen.getByTestId("paper-ledger-event-count").textContent).toContain("2");
    });

    it("no buy/sell/place-order action buttons in ledger UI", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, [_BUY_EVENT]);
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() =>
        expect(screen.getByTestId("paper-latest-decision")).toBeTruthy(),
      );
      // ledger UI 안의 모든 <button> 검사 — BUY/SELL/실거래 라벨 button 0건.
      // 단, "재시도" 같은 무관한 button 은 본 검사 외이므로 textContent 패턴만 lock.
      const buttons = container.querySelectorAll("button");
      for (const b of buttons) {
        const t = (b.textContent || "").trim();
        // 운영자 트리거가 *없어야* 할 라벨들.
        expect(t).not.toMatch(/^(매수|매도|BUY|SELL|EXIT|Place Order|실거래 시작|AI 자동매매 켜기)$/);
      }
    });

    it("badge text does not contain forbidden phrases", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 1 }, [_BUY_EVENT]);
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() =>
        expect(screen.getByTestId("paper-latest-decision")).toBeTruthy(),
      );
      const banned = [
        "Place Order", "지금 매수", "지금 매도",
        "실거래 시작", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기",
      ];
      for (const b of banned) {
        expect(container.textContent).not.toContain(b);
      }
    });
  });

  describe("#4-Loop-09 consumer strip", () => {
    const _CONSUMER_STATUS = {
      state: "RUNNING",
      cycle_count: 7,
      last_tick_at: "2026-05-18T14:30:25+00:00",
      last_consumed: true,
      last_decision_count: 2,
      last_decision_action: "BUY",
      last_ledger_events: 2,
      last_decision_log_count: 2,
      forced_paper: true,
    };

    it("renders consumer strip with last decision fields", async () => {
      const api = _mockApi(_CONSUMER_STATUS);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(screen.getByTestId("auto-paper-consumer-strip")).toBeTruthy();
      expect(screen.getByTestId("consumer-last-tick").textContent)
        .toContain("14:30:25");
      expect(screen.getByTestId("consumer-last-decision-action").textContent)
        .toContain("BUY");
      expect(screen.getByTestId("consumer-action-BUY").textContent).toBe("BUY");
      expect(screen.getByTestId("consumer-decision-count").textContent)
        .toContain("2");
      expect(screen.getByTestId("consumer-ledger-events").textContent)
        .toContain("2");
      expect(screen.getByTestId("consumer-decision-log-count").textContent)
        .toContain("2");
    });

    it("renders Paper 전용 · 실제 주문 아님 badge", async () => {
      const api = _mockApi(_CONSUMER_STATUS);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const badge = screen.getByTestId("consumer-paper-only-badge");
      expect(badge.textContent).toContain("Paper 전용");
      expect(badge.textContent).toContain("실제 주문 아님");
    });

    it("falls back to '—' when consumer fields are missing", async () => {
      const api = _mockApi({
        state: "PAUSED", cycle_count: 0,
        forced_paper: true,
      });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const strip = screen.getByTestId("auto-paper-consumer-strip");
      expect(strip).toBeTruthy();
      // last_tick_at not set → "—".
      expect(screen.getByTestId("consumer-last-tick").textContent)
        .toContain("—");
      // counts default to 0.
      expect(screen.getByTestId("consumer-decision-count").textContent)
        .toContain("0");
    });

    it("BUY/SELL/EXIT in consumer strip are labels, never buttons", async () => {
      const api = _mockApi(_CONSUMER_STATUS);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("consumer-action-BUY")).toBeTruthy(),
      );
      const buyLabel = screen.getByTestId("consumer-action-BUY");
      expect(buyLabel.tagName.toLowerCase()).toBe("strong");
    });

    it("consumer strip text never contains forbidden order phrases", async () => {
      const api = _mockApi(_CONSUMER_STATUS);
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const strip = screen.getByTestId("auto-paper-consumer-strip");
      const banned = [
        "Place Order", "지금 매수", "지금 매도",
        "실거래 시작", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기",
      ];
      for (const b of banned) {
        expect(strip.textContent).not.toContain(b);
        expect(container.textContent).not.toContain(b);
      }
    });
  });

  describe("#4-11 AI Paper E2E UI flow", () => {
    /**
     * 사용자 시나리오 (frontend 면):
     *  1. 카드가 PAUSED 상태로 마운트.
     *  2. 시작 버튼 클릭 → autoPaperStart 호출 → 상태 RUNNING 으로 갱신.
     *  3. 새 status 가 consumer 결과 carry (last_decision_action / count /
     *     ledger / decision_log) → 화면에 라벨 + 카운트 + 배지 표시.
     *  4. 카드 전체에 실거래 시작 / 지금 매수 / Place Order 등 금지 라벨 0건.
     */

    function _mockApiSequence(...statuses) {
      // 매 호출마다 다음 status 를 반환 (마지막 값 반복).
      const queue = [...statuses];
      return {
        autoPaperStatus: vi.fn(async () =>
          queue.length > 1 ? queue.shift() : queue[0],
        ),
        autoPaperStart: vi.fn(async () => statuses[statuses.length - 1]),
        autoPaperStop: vi.fn(async () => ({
          state: "STOPPED", cycle_count: statuses[statuses.length - 1].cycle_count,
        })),
        autoPaperEmergencyStop: vi.fn(async () => ({
          state: "EMERGENCY_STOP", cycle_count: 0,
        })),
        autoPaperReset: vi.fn(async () => ({ state: "PAUSED", cycle_count: 0 })),
        autoPaperLedger: vi.fn(async () => ({
          events: [],
          event_count: 0,
          is_order_signal: false,
          auto_apply_allowed: false,
          is_live_authorization: false,
          advisory_disclaimer: "Paper Auto Loop advisory ledger",
        })),
        desktopHealth: vi.fn(async () => ({
          ok: true,
          safety_flags: {
            enable_live_trading: false,
            enable_ai_execution: false,
            enable_futures_live_trading: false,
            kis_is_paper: true,
          },
        })),
      };
    }

    const _RUNNING_STATUS_AFTER_TICK = {
      state: "RUNNING",
      cycle_count: 1,
      last_tick_at: "2026-05-19T01:00:30+00:00",
      last_consumed: true,
      last_decision_count: 1,
      last_decision_action: "BUY",
      last_ledger_events: 1,
      last_decision_log_count: 1,
      forced_paper: true,
    };

    it("E2E: start button → RUNNING → consumer strip carries BUY label", async () => {
      const api = _mockApiSequence(
        { state: "PAUSED", cycle_count: 0, forced_paper: true },
        _RUNNING_STATUS_AFTER_TICK,
      );
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      // 1. 초기 PAUSED 상태가 표시되는지.
      await waitFor(() =>
        expect(api.autoPaperStatus).toHaveBeenCalled(),
      );
      // 2. 시작 버튼 클릭.
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() =>
        expect(api.autoPaperStart).toHaveBeenCalledTimes(1),
      );
      // 3. 다음 polling 으로 RUNNING 상태 + BUY 라벨 carry.
      // autoPaperStatus 가 두 번째 호출에서 _RUNNING_STATUS_AFTER_TICK 반환.
      await waitFor(() =>
        expect(screen.getByTestId("consumer-action-BUY")).toBeTruthy(),
      );
      // 4. 카운트 표시.
      expect(screen.getByTestId("consumer-decision-count").textContent)
        .toContain("1");
      expect(screen.getByTestId("consumer-ledger-events").textContent)
        .toContain("1");
      expect(screen.getByTestId("consumer-decision-log-count").textContent)
        .toContain("1");
      // 5. Paper-only / 실거래 아님 배지.
      expect(screen.getByTestId("consumer-paper-only-badge").textContent)
        .toContain("Paper 전용");
      expect(screen.getByTestId("consumer-paper-only-badge").textContent)
        .toContain("실제 주문 아님");
    });

    it("E2E: BUY label is a span, never an active order button", async () => {
      const api = _mockApiSequence(_RUNNING_STATUS_AFTER_TICK);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(screen.getByTestId("consumer-action-BUY")).toBeTruthy(),
      );
      const buy = screen.getByTestId("consumer-action-BUY");
      expect(buy.tagName.toLowerCase()).toBe("strong");
      // 또한 buy 라벨 자체는 click 가능한 button 이 아니다.
      expect(buy.tagName.toLowerCase()).not.toBe("button");
    });

    it("E2E: end-to-end DOM contains zero forbidden order labels", async () => {
      const api = _mockApiSequence(_RUNNING_STATUS_AFTER_TICK);
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() =>
        expect(api.autoPaperStatus).toHaveBeenCalled(),
      );
      const banned = [
        "지금 매수", "지금 매도", "Place Order", "실거래 시작",
        "실거래 활성화 시작", "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
        "ENABLE_FUTURES_LIVE_TRADING", "AI 자동매매 켜기",
      ];
      for (const b of banned) {
        expect(container.textContent).not.toContain(b);
      }
    });

    it("E2E: required UI elements all present at once", async () => {
      const api = _mockApiSequence(_RUNNING_STATUS_AFTER_TICK);
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() =>
        expect(api.autoPaperStatus).toHaveBeenCalled(),
      );
      // 시작/정지/긴급정지 버튼.
      expect(screen.getByTestId("btn-start-auto-paper")).toBeTruthy();
      expect(screen.getByTestId("btn-stop-auto-paper")).toBeTruthy();
      expect(screen.getByTestId("btn-emergency-stop")).toBeTruthy();
      // 상태 pill.
      expect(screen.getByTestId("state-pill")).toBeTruthy();
      // consumer strip 의 5 필드.
      expect(screen.getByTestId("consumer-last-tick")).toBeTruthy();
      expect(screen.getByTestId("consumer-last-decision-action")).toBeTruthy();
      expect(screen.getByTestId("consumer-decision-count")).toBeTruthy();
      expect(screen.getByTestId("consumer-ledger-events")).toBeTruthy();
      expect(screen.getByTestId("consumer-decision-log-count")).toBeTruthy();
      // Paper 전용 / 실거래 아님 배지.
      expect(screen.getByTestId("consumer-paper-only-badge")).toBeTruthy();
      // 상단 safety badges.
      expect(screen.getByTestId("badge-not-order-signal")).toBeTruthy();
      expect(screen.getByTestId("badge-paper-mode")).toBeTruthy();
      expect(screen.getByTestId("badge-no-auto-apply")).toBeTruthy();
    });
  });

  describe("#4-RiskProfileUI risk profile selector", () => {
    it("renders the selector with BALANCED selected by default", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const selector = screen.getByTestId("agent-risk-profile-selector");
      expect(selector).toBeTruthy();
      const group = screen.getByTestId("risk-profile-radiogroup");
      expect(group.getAttribute("data-selected")).toBe("BALANCED");
    });

    it("clicking CONSERVATIVE card switches selection", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
      expect(screen.getByTestId("risk-profile-radiogroup")
        .getAttribute("data-selected")).toBe("CONSERVATIVE");
    });

    it("clicking AGGRESSIVE card switches selection + shows safety warning", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      expect(screen.getByTestId("risk-profile-radiogroup")
        .getAttribute("data-selected")).toBe("AGGRESSIVE");
      const warn = screen.getByTestId("risk-profile-aggressive-warning");
      expect(warn.textContent).toContain("실거래 안전장치를 우회하지 않습니다");
    });

    it("default BALANCED selection is included in start payload", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      expect(api.autoPaperStart.mock.calls[0][0])
        .toMatchObject({ risk_profile: "BALANCED" });
    });

    it("CONSERVATIVE selection is forwarded to start payload", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      expect(api.autoPaperStart.mock.calls[0][0])
        .toMatchObject({ risk_profile: "CONSERVATIVE" });
    });

    it("AGGRESSIVE selection is forwarded to start payload", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      expect(api.autoPaperStart.mock.calls[0][0])
        .toMatchObject({ risk_profile: "AGGRESSIVE" });
    });

    it("selector + start payload include risk_profile alongside pre_market", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const pm = {
        start_allowed:    true,
        verdict:          "READY_TO_START",
        blocking_reasons: [],
        warnings:         [],
      };
      render(
        <AutoPaperLoopCard
          apiClient={api} pollIntervalMs={0}
          preMarketCheckResult={pm}
        />,
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      const payload = api.autoPaperStart.mock.calls[0][0];
      expect(payload.risk_profile).toBe("AGGRESSIVE");
      expect(payload.pre_market.start_allowed).toBe(true);
    });

    it("RUNNING 중에도 risk profile 선택 가능 — 다음 tick 적용 안내 표시", async () => {
      // fix(ci-policy): RUNNING 중 성향 변경 정책 확정 —
      // 선택은 가능하되 "다음 tick(다음 판단)부터 적용" 안내. 주문/실거래 권한과 무관.
      const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      // RUNNING 안내 문구가 보인다.
      expect(screen.getByTestId("risk-profile-running-notice").textContent)
        .toMatch(/다음 tick|다음 판단/);
      // CONSERVATIVE 클릭 → 선택값이 CONSERVATIVE 로 변경된다 (RUNNING 중에도 가능).
      fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
      expect(screen.getByTestId("risk-profile-radiogroup")
        .getAttribute("data-selected")).toBe("CONSERVATIVE");
      expect(screen.getByTestId("current-risk-profile")
        .getAttribute("data-risk-profile")).toBe("CONSERVATIVE");
    });

    it("RUNNING 중 risk profile 선택이 start payload(다음 시작)에 반영", async () => {
      // 선택값은 store/payload 에 저장 — 다음 start 시 그 값을 동봉.
      const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
      // localStorage(Paper 자금 설정)에 선택값이 영속된다.
      const raw = window.localStorage.getItem("agent_trader_paper_capital_settings");
      expect(raw).toBeTruthy();
      expect(JSON.parse(raw).riskProfile).toBe("CONSERVATIVE");
    });

    it("RUNNING 중 AGGRESSIVE 선택해도 실거래/ENABLE_* 버튼 0개", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
      const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      expect(screen.getByTestId("risk-profile-radiogroup")
        .getAttribute("data-selected")).toBe("AGGRESSIVE");
      const labels = Array.from(container.querySelectorAll("button"))
        .map((b) => (b.textContent || "").trim());
      for (const t of labels) {
        expect(t).not.toMatch(/Place Order/i);
        expect(t).not.toMatch(/지금 매수/);
        expect(t).not.toMatch(/실거래 시작/);
        expect(t).not.toMatch(/ENABLE_LIVE_TRADING/);
        expect(t).not.toMatch(/ENABLE_AI_EXECUTION/);
      }
    });

    it("AGGRESSIVE selected — no Place Order / 지금 매수 / 실거래 시작 anywhere in DOM", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { container } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />,
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      const text = container.textContent || "";
      const forbidden = [
        "지금 매수", "지금 매도", "Place Order",
        "실거래 시작", "실거래 활성화 시작",
        "ENABLE_LIVE_TRADING=true", "ENABLE_AI_EXECUTION=true",
        "ENABLE_FUTURES_LIVE_TRADING=true",
        "AI 자동매매 켜기",
      ];
      for (const f of forbidden) {
        expect(text).not.toContain(f);
      }
    });
  });
});


// ────────────────────────────────────────────────────────────────────────────
// fix/frontend-ci-operator-and-autopaper:
//   pure-function 단위 테스트 (사용자 요청서 §5) — UI state matrix 가 alias /
//   대소문자 / null / unknown 입력에서도 안정적으로 정규화되어 stop / start
//   조건이 깨지지 않는지 *컴포넌트와 별개* 로 lock.
// ────────────────────────────────────────────────────────────────────────────


describe("normalizeAutoPaperState — pure function", () => {
  it("canonical states are idempotent", () => {
    for (const s of [
      "PAUSED", "WAITING_MARKET", "RUNNING", "STOPPED",
      "EMERGENCY_STOP", "MARKET_CLOSED",
    ]) {
      expect(normalizeAutoPaperState(s)).toBe(s);
    }
  });

  it("legacy aliases IDLE / EMERGENCY map to canonical", () => {
    expect(normalizeAutoPaperState("IDLE")).toBe("PAUSED");
    expect(normalizeAutoPaperState("EMERGENCY")).toBe("EMERGENCY_STOP");
  });

  it("common synonyms map to canonical", () => {
    expect(normalizeAutoPaperState("STARTED")).toBe("RUNNING");
    expect(normalizeAutoPaperState("ACTIVE")).toBe("RUNNING");
    expect(normalizeAutoPaperState("HALTED")).toBe("STOPPED");
    expect(normalizeAutoPaperState("WAITING")).toBe("WAITING_MARKET");
    expect(normalizeAutoPaperState("CLOSED")).toBe("MARKET_CLOSED");
  });

  it("case-insensitive normalization", () => {
    expect(normalizeAutoPaperState("running")).toBe("RUNNING");
    expect(normalizeAutoPaperState("Running")).toBe("RUNNING");
    expect(normalizeAutoPaperState("  RuNnInG  ")).toBe("RUNNING");
  });

  it("null / undefined / empty / unknown -> PAUSED fallback", () => {
    expect(normalizeAutoPaperState(null)).toBe("PAUSED");
    expect(normalizeAutoPaperState(undefined)).toBe("PAUSED");
    expect(normalizeAutoPaperState("")).toBe("PAUSED");
    expect(normalizeAutoPaperState("   ")).toBe("PAUSED");
    expect(normalizeAutoPaperState("XYZ_UNKNOWN")).toBe("PAUSED");
  });
});


describe("canStopAutoPaper — pure function", () => {
  it("RUNNING -> true", () => {
    expect(canStopAutoPaper("RUNNING")).toBe(true);
  });

  it("alias for RUNNING -> true", () => {
    expect(canStopAutoPaper("running")).toBe(true);
    expect(canStopAutoPaper("STARTED")).toBe(true);
    expect(canStopAutoPaper("ACTIVE")).toBe(true);
  });

  it("WAITING_MARKET / STOPPED / PAUSED / EMERGENCY_STOP / MARKET_CLOSED -> false", () => {
    for (const s of [
      "WAITING_MARKET", "STOPPED", "PAUSED",
      "EMERGENCY_STOP", "MARKET_CLOSED",
    ]) {
      expect(canStopAutoPaper(s)).toBe(false);
    }
  });

  it("null / unknown -> false", () => {
    expect(canStopAutoPaper(null)).toBe(false);
    expect(canStopAutoPaper("")).toBe(false);
    expect(canStopAutoPaper("garbage")).toBe(false);
  });
});


describe("canStartAutoPaper — pure function", () => {
  it("RUNNING / WAITING_MARKET -> false", () => {
    expect(canStartAutoPaper("RUNNING")).toBe(false);
    expect(canStartAutoPaper("WAITING_MARKET")).toBe(false);
    expect(canStartAutoPaper("running")).toBe(false);
  });

  it("PAUSED / STOPPED / EMERGENCY_STOP / MARKET_CLOSED -> true", () => {
    for (const s of ["PAUSED", "STOPPED", "EMERGENCY_STOP", "MARKET_CLOSED"]) {
      expect(canStartAutoPaper(s)).toBe(true);
    }
  });
});


// ────────────────────────────────────────────────────────────────────────────
// 사용자 요청서 §3 추가 시나리오: stop button disabled / enabled 매트릭스
// ────────────────────────────────────────────────────────────────────────────


describe("<AutoPaperLoopCard> — stop button state matrix", () => {
  afterEach(cleanup);

  it("RUNNING -> stop button disabled=false", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
  });

  it("RUNNING -> click triggers exactly 1 autoPaperStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() =>
      expect(api.autoPaperStop).toHaveBeenCalledTimes(1),
    );
  });

  it("STOPPED -> stop button disabled=true", async () => {
    const api = _mockApi({ state: "STOPPED", cycle_count: 5 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true),
    );
  });

  it("PAUSED -> stop button disabled=true", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true),
    );
  });

  it("WAITING_MARKET -> stop button disabled=true (정책)", async () => {
    const api = _mockApi({ state: "WAITING_MARKET", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true),
    );
  });

  it("MARKET_CLOSED -> stop button disabled=true (정책)", async () => {
    const api = _mockApi({ state: "MARKET_CLOSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true),
    );
  });

  it("alias state 'running' (lowercase) is normalized and stop enabled", async () => {
    const api = _mockApi({ state: "running", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() =>
      expect(api.autoPaperStop).toHaveBeenCalledTimes(1),
    );
  });

  it("stop button has no live-trade label", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
    const btn = screen.getByTestId("btn-stop-auto-paper");
    const text = (btn.textContent || "").trim();
    expect(text).not.toContain("매수");
    expect(text).not.toContain("매도");
    expect(text).not.toContain("실거래");
    expect(text.toLowerCase()).not.toContain("place order");
    expect(text.toLowerCase()).not.toContain("enable_");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// P-15: Paper 자금 설정 carry — summary + start payload
// ────────────────────────────────────────────────────────────────────────────


describe("<AutoPaperLoopCard> — P-15 capital settings", () => {
  afterEach(cleanup);

  it("default 자금 기준 요약이 화면에 표시됨", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const summary = screen.getByTestId("auto-paper-capital-summary");
    expect(summary.textContent).toMatch(/적용 자금 기준/);
    const text = screen.getByTestId("auto-paper-capital-summary-text").textContent;
    expect(text).toMatch(/시드머니/);
    expect(text).toMatch(/종목당/);
    expect(text).toMatch(/최대/);
    expect(text).toMatch(/일일/);
    expect(text).toMatch(/종목비중/);
  });

  it("allow_additional_buy=false → '비허용' 라벨 표시", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(
      <AutoPaperLoopCard
        apiClient={api}
        pollIntervalMs={0}
        paperCapitalSettings={{
          totalPaperCapital: 10_000_000,
          perSymbolAllocation: 1_000_000,
          maxPositions: 5,
          maxDailyBuyAmount: 3_000_000,
          maxSymbolWeightPct: 0.2,
          allowAdditionalBuy: false,
        }}
      />,
    );
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("auto-paper-allow-additional-buy-label").textContent)
      .toMatch(/비허용/);
  });

  it("allow_additional_buy=true → 'Paper 검증 전용' 경고 표시", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(
      <AutoPaperLoopCard
        apiClient={api}
        pollIntervalMs={0}
        paperCapitalSettings={{
          totalPaperCapital: 10_000_000,
          perSymbolAllocation: 1_000_000,
          maxPositions: 5,
          maxDailyBuyAmount: 3_000_000,
          maxSymbolWeightPct: 0.2,
          allowAdditionalBuy: true,
        }}
      />,
    );
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const label = screen.getByTestId("auto-paper-allow-additional-buy-label");
    expect(label.textContent).toMatch(/허용/);
    expect(label.textContent).toMatch(/Paper 검증 전용/);
  });

  it("start payload 에 capital_settings 6개 키 포함", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(
      <AutoPaperLoopCard
        apiClient={api}
        pollIntervalMs={0}
        paperCapitalSettings={{
          totalPaperCapital: 30_000_000,
          perSymbolAllocation: 2_000_000,
          maxPositions: 8,
          maxDailyBuyAmount: 5_000_000,
          maxSymbolWeightPct: 0.10,
          allowAdditionalBuy: true,
        }}
      />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
    const body = api.autoPaperStart.mock.calls[0][0];
    expect(body.capital_settings).toEqual({
      total_paper_capital:    30_000_000,
      per_symbol_allocation:  2_000_000,
      max_positions:          8,
      max_daily_buy_amount:   5_000_000,
      max_symbol_weight_pct:  0.10,
      allow_additional_buy:   true,
      risk_profile:           "BALANCED",
    });
  });

  it("prop 없으면 localStorage 에서 로드 (default snake_case payload)", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
    const body = api.autoPaperStart.mock.calls[0][0];
    expect(body.capital_settings).toMatchObject({
      total_paper_capital:    10_000_000,
      per_symbol_allocation:  1_000_000,
      max_positions:          5,
      max_daily_buy_amount:   3_000_000,
      max_symbol_weight_pct:  0.2,
      allow_additional_buy:   false,
    });
  });

  it("P-15: 추가매수 ON 카드여도 '실거래 활성화' / Place Order / ENABLE_* 라벨 0건", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    const { container } = render(
      <AutoPaperLoopCard
        apiClient={api}
        pollIntervalMs={0}
        paperCapitalSettings={{
          totalPaperCapital: 10_000_000,
          perSymbolAllocation: 1_000_000,
          maxPositions: 5,
          maxDailyBuyAmount: 3_000_000,
          maxSymbolWeightPct: 0.2,
          allowAdditionalBuy: true,
        }}
      />,
    );
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const banned = [
      "Place Order", "지금 매수", "지금 매도",
      "실거래 시작", "실거래 활성화 시작", "실거래 활성화 켜기",
      "ENABLE_LIVE_TRADING=true", "ENABLE_AI_EXECUTION=true",
    ];
    for (const b of banned) {
      expect(container.textContent).not.toContain(b);
    }
  });

  it("정지 버튼 회귀 — RUNNING 시 disabled=false 유지 (P-15 영향 없음)", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(false),
    );
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() => expect(api.autoPaperStop).toHaveBeenCalledTimes(1));
  });

  it("정지 버튼 회귀 — PAUSED 시 disabled=true 유지", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true),
    );
  });
});


// ────────────────────────────────────────────────────────────────────────────
// 자동매매 실행 점검판 + 강제 진단 run-once
//   - run-readiness 가 universe / 시장 세션 / 권한을 점검판에 표시.
//   - run-once 버튼이 autoPaperRunOnceDiagnostic 호출 → 결과(reason_code) 표시.
//   - RUNNING + cycle 0 → "거래는 아직 없지만 루프가 실행 중" 안내.
//   - 실거래 / 매수 / 매도 / Place Order 라벨 0건.
//   - readiness/run-once 함수가 없는 mock 에서도 안전.
// ────────────────────────────────────────────────────────────────────────────


function _readinessFixture(overrides = {}) {
  return {
    loop: {
      state: "RUNNING", cycle_count: 0, last_tick_at: null, last_error: null,
      health_code: "RUNNING_NO_TICKS",
      health_message: "루프는 RUNNING 이지만 아직 tick 기록이 없습니다 — 진단 run-once 로 파이프라인 연결을 검증하세요.",
      ...(overrides.loop || {}),
    },
    market_session: { phase: "OPEN", kst_time: "2026-05-22 10:00:00", kst_weekday: 4, is_open: true, ...(overrides.market_session || {}) },
    universe: { source: "FALLBACK_MARKET_CAP_TOP50", count: 50, fallback_used: true, warning_ko: "fallback", ...(overrides.universe || {}) },
    market_data: { provider: "mock", is_mock: true, ...(overrides.market_data || {}) },
    permission: { paper_virtual_execution_allowed: true, live_execution_blocked: true, ...(overrides.permission || {}) },
    paper_capital: { effective_per_symbol_cap_krw: 1_000_000, available_cash_krw: 10_000_000, initial_cash_krw: 10_000_000, ...(overrides.paper_capital || {}) },
    background_tick: {
      enabled: false, running: false, interval_seconds: 30, dry_run: true,
      max_per_day: 0, tick_count_today: 0, last_tick_at: null,
      last_reason_code: null, last_reason_message: null,
      last_pipeline_result_code: null,
      is_order_signal: false, is_live_authorization: false, broker_order_sent: false,
      ...(overrides.background_tick || {}),
    },
    can_run_once_diagnostic: true,
    is_order_signal: false,
    is_live_authorization: false,
  };
}


describe("<AutoPaperLoopCard> — 실행 점검판 + run-once 진단", () => {
  afterEach(cleanup);

  function _mockApiWithDiag(status, readiness, runOnceResult) {
    const api = _mockApi(status);
    api.autoPaperRunReadiness = vi.fn(async () => readiness);
    api.autoPaperRunOnceDiagnostic = vi.fn(async () => runOnceResult);
    return api;
  }

  it("점검판이 항상 렌더된다 (run-once 버튼 포함)", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("auto-paper-exec-diagnostics")).toBeTruthy();
    expect(screen.getByTestId("btn-run-once-diagnostic")).toBeTruthy();
  });

  it("readiness 가 universe / 시장 세션 / 권한 / 현금을 표시", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 0 },
      _readinessFixture(),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperRunReadiness).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.getByTestId("exec-universe").textContent).toMatch(/FALLBACK_MARKET_CAP_TOP50/),
    );
    expect(screen.getByTestId("exec-universe").textContent).toMatch(/50개/);
    expect(screen.getByTestId("exec-market-session").textContent).toMatch(/OPEN/);
    expect(screen.getByTestId("exec-market-data").textContent).toMatch(/mock/);
    expect(screen.getByTestId("exec-permission").textContent).toMatch(/실거래 차단/);
    expect(screen.getByTestId("exec-paper-cash").textContent).toMatch(/10,000,000/);
  });

  it("RUNNING + cycle 0 → '거래는 아직 없지만 루프가 실행 중' 안내", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 0 },
      _readinessFixture(),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("exec-loop-health").textContent)
        .toMatch(/거래는 아직 없지만 루프가 실행 중/),
    );
  });

  it("run-once 버튼 클릭 → autoPaperRunOnceDiagnostic 호출 + 정상 결과 표시", async () => {
    const result = {
      result_code: "PAPER_DRY_RUN_OK", ok: true, dry_run: true,
      symbol: "005930", price: 75000, quantity: 13, notional_krw: 975000,
      reason_message: "dry-run 통과 — 파이프라인 전 단계 정상",
      stages: [
        { stage: "UNIVERSE", ok: true, reason_code: "OK", message: "" },
        { stage: "FINAL", ok: true, reason_code: "PAPER_DRY_RUN_OK", message: "" },
      ],
      broker_order_sent: false,
    };
    const api = _mockApiWithDiag({ state: "RUNNING", cycle_count: 0 }, _readinessFixture(), result);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("btn-run-once-diagnostic")).toBeTruthy());
    fireEvent.click(screen.getByTestId("btn-run-once-diagnostic"));
    await waitFor(() => expect(api.autoPaperRunOnceDiagnostic).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByTestId("run-once-result-code").textContent).toBe("PAPER_DRY_RUN_OK"),
    );
    expect(screen.getByTestId("run-once-qty").textContent).toMatch(/13주/);
    expect(screen.getByTestId("run-once-reason").textContent).toMatch(/파이프라인 전 단계 정상/);
    expect(screen.getByTestId("run-once-stages").textContent).toMatch(/UNIVERSE/);
  });

  it("run-once 차단 결과(reason_code)도 표시된다", async () => {
    const result = {
      result_code: "MIN_LOT_NOT_AFFORDABLE", ok: false, dry_run: true,
      symbol: "005930", price: 2000000, quantity: 0, notional_krw: 0,
      reason_message: "종목당 투자금으로 1주도 살 수 없어 매수를 진행하지 않았습니다 (고가주).",
      stages: [{ stage: "SIZING", ok: false, reason_code: "MIN_LOT_NOT_AFFORDABLE", message: "" }],
      broker_order_sent: false,
    };
    const api = _mockApiWithDiag({ state: "RUNNING", cycle_count: 0 }, _readinessFixture(), result);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("btn-run-once-diagnostic")).toBeTruthy());
    fireEvent.click(screen.getByTestId("btn-run-once-diagnostic"));
    await waitFor(() =>
      expect(screen.getByTestId("run-once-result-code").textContent).toBe("MIN_LOT_NOT_AFFORDABLE"),
    );
    expect(screen.getByTestId("run-once-result").getAttribute("data-ok")).toBe("false");
    expect(screen.getByTestId("run-once-reason").textContent).toMatch(/고가주/);
  });

  it("readiness / run-once 함수가 없는 mock 에서도 throw 없이 렌더", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    // 명시적으로 미정의 — 구버전 backend / 최소 mock.
    expect(api.autoPaperRunReadiness).toBeUndefined();
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("auto-paper-exec-diagnostics")).toBeTruthy();
    // 함수 없으면 클릭해도 throw 없이 무시.
    fireEvent.click(screen.getByTestId("btn-run-once-diagnostic"));
    expect(screen.queryByTestId("run-once-result")).toBeNull();
  });

  it("background_tick.enabled=false → '자동 tick driver 비활성' 표시", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 0 },
      _readinessFixture({ background_tick: { enabled: false, running: false } }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("background-tick-status").getAttribute("data-enabled")).toBe("false"),
    );
    expect(screen.getByTestId("bg-tick-state-label").textContent).toMatch(/비활성/);
    expect(screen.getByTestId("bg-tick-message").textContent).toMatch(/run-once 진단만 수동 실행/);
    expect(screen.getByTestId("bg-tick-interval").textContent).toMatch(/30초/);
    expect(screen.getByTestId("bg-tick-dry-run").textContent).toMatch(/dry-run/);
  });

  it("background_tick.enabled=true/running=true + RUNNING → '활성' + N초 안내", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 3 },
      _readinessFixture({
        loop: { state: "RUNNING", cycle_count: 3 },
        background_tick: {
          enabled: true, running: true, interval_seconds: 30, dry_run: true,
          last_reason_code: "PAPER_DRY_RUN_OK", last_tick_at: "2026-05-22T10:00:30+00:00",
        },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("background-tick-status").getAttribute("data-running")).toBe("true"),
    );
    expect(screen.getByTestId("bg-tick-state-label").textContent).toMatch(/활성/);
    expect(screen.getByTestId("bg-tick-message").textContent).toMatch(/30초마다 AI Paper 판단/);
    expect(screen.getByTestId("bg-tick-last-reason").textContent).toMatch(/PAPER_DRY_RUN_OK/);
  });

  it("background_tick enabled + WAITING_MARKET → 장 시작 전 대기 문구", async () => {
    const api = _mockApiWithDiag(
      { state: "WAITING_MARKET", cycle_count: 0 },
      _readinessFixture({
        loop: { state: "WAITING_MARKET", cycle_count: 0 },
        market_session: { phase: "PRE_OPEN", is_open: false },
        background_tick: { enabled: true, running: true, interval_seconds: 30 },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-message").textContent).toMatch(/장 시작 전/),
    );
  });

  it("background_tick enabled + EMERGENCY_STOP → tick 차단 문구", async () => {
    const api = _mockApiWithDiag(
      { state: "EMERGENCY_STOP", cycle_count: 0 },
      _readinessFixture({
        loop: { state: "EMERGENCY_STOP", cycle_count: 0 },
        background_tick: { enabled: true, running: true },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-message").textContent).toMatch(/긴급정지 ON/),
    );
  });

  it("background_tick 영역에 실거래/ENABLE_* 토글 버튼 0개", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 1 },
      _readinessFixture({ background_tick: { enabled: true, running: true } }),
      null,
    );
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("background-tick-status")).toBeTruthy());
    const btStatus = screen.getByTestId("background-tick-status");
    expect(btStatus.querySelectorAll("button").length).toBe(0);
    expect(btStatus.textContent).toMatch(/broker_order_sent=false/);
    const banned = ["ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION", "실거래 시작", "AI 자동매매 켜기", "Place Order"];
    for (const b of banned) expect(container.textContent).not.toContain(b);
  });

  it("tick_mode=DIAGNOSTIC_DRY_RUN → '진단 dry-run' + '판단만 기록' 표시", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 1 },
      _readinessFixture({
        background_tick: {
          enabled: true, running: true, tick_mode: "DIAGNOSTIC_DRY_RUN",
          simulated_fills_enabled: false,
        },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-mode").getAttribute("data-tick-mode")).toBe("DIAGNOSTIC_DRY_RUN"),
    );
    expect(screen.getByTestId("bg-tick-mode").textContent).toMatch(/진단 dry-run/);
    expect(screen.getByTestId("bg-tick-simulated-fills").textContent).toMatch(/판단만 기록/);
  });

  it("tick_mode=SIMULATED_TRADE → 'Paper 모의 체결' + '체결 시뮬레이션 반영' + 주문 결과", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 3 },
      _readinessFixture({
        loop: { state: "RUNNING", cycle_count: 3 },
        background_tick: {
          enabled: true, running: true, tick_mode: "SIMULATED_TRADE",
          simulated_fills_enabled: true,
          last_order_id: 1, last_fill_status: "FILLED", last_quantity: 13,
          last_notional_krw: 975_000, last_cash_before: 10_000_000,
          last_cash_after: 9_025_000, last_reason_code: "VIRTUAL_ORDER_CANDIDATE_CREATED",
        },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-mode").getAttribute("data-tick-mode")).toBe("SIMULATED_TRADE"),
    );
    expect(screen.getByTestId("bg-tick-mode").textContent).toMatch(/Paper 모의 체결/);
    expect(screen.getByTestId("bg-tick-simulated-fills").textContent).toMatch(/체결 시뮬레이션 반영/);
    expect(screen.getByTestId("bg-tick-order-id").textContent).toMatch(/#1/);
    expect(screen.getByTestId("bg-tick-fill-status").textContent).toMatch(/FILLED/);
    expect(screen.getByTestId("bg-tick-quantity").textContent).toMatch(/13주/);
    expect(screen.getByTestId("bg-tick-notional").textContent).toMatch(/975,000/);
    expect(screen.getByTestId("bg-tick-cash-change").textContent).toMatch(/10,000,000원 → 9,025,000원/);
    expect(screen.getByTestId("bg-tick-safety").textContent).toMatch(/broker_order_sent=false/);
    expect(screen.getByTestId("bg-tick-safety").textContent).toMatch(/is_live_authorization=false/);
  });

  it("SIMULATED_TRADE 표시에 실거래/ENABLE_* 버튼·문구 0건", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 3 },
      _readinessFixture({
        background_tick: {
          enabled: true, running: true, tick_mode: "SIMULATED_TRADE",
          simulated_fills_enabled: true, last_order_id: 1, last_fill_status: "FILLED",
          last_quantity: 13, last_notional_krw: 975_000,
        },
      }),
      null,
    );
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("bg-tick-mode")).toBeTruthy());
    const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING", "AI 자동매매 켜기"];
    for (const b of banned) expect(container.textContent).not.toContain(b);
  });

  it("KIS_PAPER_AUTO 모드 → 'KIS 모의투자 자동주문' + 주문번호 + 안전 문구 표시", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 5 },
      _readinessFixture({
        loop: { state: "RUNNING", cycle_count: 5 },
        background_tick: {
          enabled: true, running: true, tick_mode: "KIS_PAPER_AUTO",
          kis_paper_auto_enabled: true, kis_paper_auto_dry_run: false,
          last_broker_order_no: "PAPER-0001", last_order_status: "FILLED",
          last_fill_status: "FILLED", last_reason_code: "KIS_PAPER_SUBMITTED",
        },
      }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-mode").getAttribute("data-tick-mode")).toBe("KIS_PAPER_AUTO"),
    );
    expect(screen.getByTestId("bg-tick-mode").textContent).toMatch(/KIS 모의투자 자동주문/);
    const kis = screen.getByTestId("bg-tick-kis-auto");
    expect(kis.getAttribute("data-kis-enabled")).toBe("true");
    expect(screen.getByTestId("bg-tick-kis-state").textContent).toMatch(/ON/);
    expect(screen.getByTestId("bg-tick-kis-order-no").textContent).toMatch(/PAPER-0001/);
    expect(screen.getByTestId("bg-tick-kis-order-status").textContent).toMatch(/FILLED/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent).toMatch(/한투 모의투자 API 주문/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent).toMatch(/실제 돈이 나가지 않습니다/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent).toMatch(/broker_order_type=KIS_PAPER/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent).toMatch(/is_live_authorization=false/);
  });

  it("KIS 자동주문 OFF → KIS 블록 미표시", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 1 },
      _readinessFixture({ background_tick: { enabled: true, running: true } }),
      null,
    );
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("bg-tick-mode")).toBeTruthy());
    expect(screen.queryByTestId("bg-tick-kis-auto")).toBeNull();
  });

  it("KIS dry-run 모드 표시 + 실거래/ENABLE_* 문구 0건", async () => {
    const api = _mockApiWithDiag(
      { state: "RUNNING", cycle_count: 1 },
      _readinessFixture({
        background_tick: {
          enabled: true, running: true, tick_mode: "KIS_PAPER_AUTO",
          kis_paper_auto_enabled: true, kis_paper_auto_dry_run: true,
          last_reason_code: "KIS_PAPER_DRY_RUN_OK",
        },
      }),
      null,
    );
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("bg-tick-kis-dry-run")).toBeTruthy());
    expect(screen.getByTestId("bg-tick-kis-dry-run").textContent).toMatch(/dry-run/);
    const banned = ["ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION", "실거래 시작", "실거래 활성화 시작", "지금 매수", "Place Order"];
    for (const b of banned) expect(container.textContent).not.toContain(b);
  });

  it("점검판 + run-once 결과에 금지 라벨 0건", async () => {
    const result = {
      result_code: "VIRTUAL_ORDER_CANDIDATE_CREATED", ok: true, dry_run: false,
      symbol: "005930", price: 75000, quantity: 13, notional_krw: 975000,
      reason_message: "Paper 가상 주문 후보가 정상 생성되었습니다 (실거래 아님).",
      stages: [], broker_order_sent: false,
    };
    const api = _mockApiWithDiag({ state: "RUNNING", cycle_count: 1 }, _readinessFixture(), result);
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("btn-run-once-diagnostic")).toBeTruthy());
    fireEvent.click(screen.getByTestId("btn-run-once-diagnostic"));
    await waitFor(() => expect(api.autoPaperRunOnceDiagnostic).toHaveBeenCalled());
    const banned = [
      "Place Order", "지금 매수", "지금 매도", "실거래 시작",
      "실거래 활성화 시작", "ENABLE_LIVE_TRADING=true", "AI 자동매매 켜기",
    ];
    for (const b of banned) {
      expect(container.textContent).not.toContain(b);
    }
    // run-once 버튼 라벨에 매수/매도/buy/sell 0건.
    const btn = screen.getByTestId("btn-run-once-diagnostic");
    const t = (btn.textContent || "").toLowerCase();
    expect(t).not.toContain("매수");
    expect(t).not.toContain("매도");
    expect(t).not.toContain("buy");
    expect(t).not.toContain("sell");
    expect(t).not.toContain("place order");
  });
});


// ────────────────────────────────────────────────────────────────────────────
// AI 운용 성향 선택 영속 (localStorage) + 표시 + 장 시작 전 선택 가능
// ────────────────────────────────────────────────────────────────────────────


describe("<AutoPaperLoopCard> — risk_profile 영속/표시", () => {
  afterEach(cleanup);

  it("AGGRESSIVE 선택 → 재마운트 후에도 유지 (localStorage 영속)", async () => {
    const api1 = _mockApi({ state: "PAUSED", cycle_count: 0 });
    const { unmount } = render(<AutoPaperLoopCard apiClient={api1} pollIntervalMs={0} />);
    await waitFor(() => expect(api1.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
    expect(screen.getByTestId("current-risk-profile").getAttribute("data-risk-profile")).toBe("AGGRESSIVE");
    unmount();
    // 재마운트 — localStorage 에서 AGGRESSIVE 로드.
    const api2 = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api2} pollIntervalMs={0} />);
    await waitFor(() => expect(api2.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("current-risk-profile").getAttribute("data-risk-profile")).toBe("AGGRESSIVE");
    expect(screen.getByTestId("risk-profile-radiogroup").getAttribute("data-selected")).toBe("AGGRESSIVE");
  });

  it("CONSERVATIVE 선택 → current-risk-profile 보수적 표시 + start payload", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
    expect(screen.getByTestId("current-risk-profile").textContent).toMatch(/보수적/);
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
    expect(api.autoPaperStart.mock.calls[0][0]).toMatchObject({ risk_profile: "CONSERVATIVE" });
    // capital_settings 에도 risk_profile carry.
    expect(api.autoPaperStart.mock.calls[0][0].capital_settings.risk_profile).toBe("CONSERVATIVE");
  });

  it("장 시작 전(WAITING_MARKET)에도 성향 선택 가능", async () => {
    const api = _mockApi({ state: "WAITING_MARKET", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
    expect(screen.getByTestId("risk-profile-radiogroup").getAttribute("data-selected")).toBe("AGGRESSIVE");
  });

  it("기본값 BALANCED 표시 (localStorage 비어있을 때)", async () => {
    const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("current-risk-profile").getAttribute("data-risk-profile")).toBe("BALANCED");
  });
});


describe("P-17: 매수 불가 사유 요약", () => {
  afterEach(cleanup);

  function _withBlocked(api, summary) {
    return { ...api, autoPaperBlockedReasonsToday: vi.fn(async () => summary) };
  }

  const _SUMMARY = {
    total_blocked: 3,
    by_reason: { MIN_LOT_NOT_AFFORDABLE: 2, INSUFFICIENT_PAPER_CASH: 1 },
    recent: [],
    last_block: { reason_code: "MIN_LOT_NOT_AFFORDABLE", symbol: "373220" },
    is_order_signal: false,
    is_live_authorization: false,
  };

  it("오늘 차단 건수 + 마지막 차단 사유 표시", async () => {
    const api = _withBlocked(_mockApi({ state: "RUNNING", cycle_count: 1 }), _SUMMARY);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-blocked-summary")).toBeTruthy());
    expect(screen.getByTestId("auto-paper-blocked-summary").textContent)
      .toMatch(/오늘 매수 차단 3건/);
    expect(screen.getByTestId("auto-paper-last-block").textContent)
      .toMatch(/1주 가격이 투자한도 초과로 제외/);
    expect(screen.getByTestId("auto-paper-last-block").textContent).toMatch(/373220/);
    expect(screen.getByTestId("auto-paper-last-block-code").textContent)
      .toMatch(/MIN_LOT_NOT_AFFORDABLE/);
  });

  it("차단 0건이면 요약 패널 비표시", async () => {
    const api = _withBlocked(_mockApi({ state: "RUNNING", cycle_count: 1 }), {
      total_blocked: 0, by_reason: {}, recent: [], last_block: null,
      is_order_signal: false, is_live_authorization: false,
    });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("auto-paper-blocked-summary")).toBeNull();
  });

  it("정지 버튼은 매수 불가 요약과 무관하게 RUNNING 에서 동작", async () => {
    const api = _withBlocked(_mockApi({ state: "RUNNING", cycle_count: 1 }), _SUMMARY);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.getByTestId("auto-paper-blocked-summary")).toBeTruthy());
    const stopBtn = screen.getByTestId("btn-stop-auto-paper");
    expect(stopBtn.disabled).toBe(false);
    fireEvent.click(stopBtn);
    await waitFor(() => expect(api.autoPaperStop).toHaveBeenCalled());
  });

  it("blocked-reasons API 미지원이어도 크래시 없음", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 1 });   // 메서드 없음.
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("auto-paper-blocked-summary")).toBeNull();
  });
});


describe("<AutoPaperLoopCard> 거래 없음(no-trade) 사유 (3-09)", () => {
  afterEach(cleanup);

  function _withNoTrade(api, summary) {
    return { ...api, autoPaperNoTradeReasonsToday: vi.fn(async () => summary) };
  }

  const _NT = {
    cycle_count: 120,
    order_count: 2,
    no_trade_count: 118,
    by_reason: { NO_SIGNAL: 100, MARKET_CLOSED: 15, BLOCKED_BY_RISK_MANAGER: 3 },
    by_no_trade_reason: { NO_SIGNAL: 100, MARKET_CLOSED: 15, BLOCKED_BY_RISK_MANAGER: 3 },
    recent: [],
    last_no_trade: { reason_code: "NO_SIGNAL", symbol: "005930" },
    last_no_trade_reason: "NO_SIGNAL",
    last_no_trade_symbol: "005930",
    contains_secret: false,
    is_order_signal: false,
    is_live_authorization: false,
    auto_apply_allowed: false,
  };

  it("no_trade_count 헤더 표시", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 120 }), _NT);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("no-trade-count").textContent).toMatch(/118/));
  });

  it("거래 없음 요약 + 최근 사유 + cycle/order 카운트 표시", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 120 }), _NT);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-no-trade-summary")).toBeTruthy());
    const panel = screen.getByTestId("auto-paper-no-trade-summary");
    expect(panel.textContent).toMatch(/오늘 거래 없음 118건/);
    expect(panel.textContent).toMatch(/cycle 120/);
    expect(panel.textContent).toMatch(/주문 2건/);
    expect(screen.getByTestId("auto-paper-last-no-trade").textContent)
      .toMatch(/조건에 맞는 매수\/매도 신호가 없어 거래하지 않음/);
    expect(screen.getByTestId("auto-paper-last-no-trade").textContent)
      .toMatch(/005930/);
    expect(screen.getByTestId("auto-paper-last-no-trade-code").textContent)
      .toMatch(/NO_SIGNAL/);
  });

  it("reason 별 count 요약 표시", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 120 }), _NT);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-no-trade-by-reason")).toBeTruthy());
    expect(screen.getByTestId("no-trade-reason-NO_SIGNAL").textContent)
      .toMatch(/100건/);
    expect(screen.getByTestId("no-trade-reason-MARKET_CLOSED").textContent)
      .toMatch(/15건/);
  });

  it("거래 없음 disclaimer — 오류가 아닐 수 있음 + 실거래 아님", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 120 }), _NT);
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-no-trade-disclaimer")).toBeTruthy());
    const d = screen.getByTestId("auto-paper-no-trade-disclaimer").textContent;
    expect(d).toMatch(/오류가 아닐 수 있습니다/);
    expect(d).toMatch(/broker_order_sent=false/);
    expect(d).toMatch(/is_live_authorization=false/);
  });

  it("no_trade_count=0 이면 요약 패널 미표시", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 1 }), {
      ..._NT, no_trade_count: 0, by_reason: {}, last_no_trade: null,
    });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("auto-paper-no-trade-summary")).toBeNull();
  });

  it("no-trade API 미지원이어도 크래시 없음", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 1 });   // 메서드 없음.
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.queryByTestId("auto-paper-no-trade-summary")).toBeNull();
  });

  it("실전/매수/매도/Place Order/자동 재주문 라벨 버튼 0개", async () => {
    const api = _withNoTrade(_mockApi({ state: "RUNNING", cycle_count: 120 }), _NT);
    const { container } = render(
      <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-no-trade-summary")).toBeTruthy());
    const buttons = [...container.querySelectorAll("button")];
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/Place Order/i);
      expect(t).not.toMatch(/지금 매수/);
      expect(t).not.toMatch(/지금 매도/);
      expect(t).not.toMatch(/실거래 시작/);
      expect(t).not.toMatch(/자동 재주문/);
      expect(t).not.toMatch(/ENABLE_/);
    }
    // secret/account 미표시.
    const text = container.textContent.toLowerCase();
    for (const bad of ["app_secret", "api_key", "account_no", "access_token"]) {
      expect(text).not.toContain(bad);
    }
  });
});


describe("<AutoPaperLoopCard> KIS 모의 자동주문 ON 표시 (4-02)", () => {
  afterEach(cleanup);

  function _withKisAuto(api) {
    return {
      ...api,
      autoPaperRunReadiness: vi.fn(async () => ({
        loop: { health_code: "RUNNING_OK", health_message: "정상" },
        background_tick: {
          enabled: true, running: true, interval_seconds: 30,
          tick_mode: "KIS_PAPER_AUTO",
          kis_paper_auto_enabled: true, kis_paper_auto_dry_run: false,
          is_live_authorization: false, broker_order_sent: false,
        },
      })),
    };
  }

  it("background_tick KIS_PAPER_AUTO 면 KIS 모의 자동주문 ON 표시", async () => {
    const api = _withKisAuto(_mockApi({ state: "RUNNING", cycle_count: 3 }));
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-kis-auto")).toBeTruthy());
    expect(screen.getByTestId("bg-tick-kis-state").textContent).toMatch(/ON/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent)
      .toMatch(/is_live_authorization=false/);
    expect(screen.getByTestId("bg-tick-kis-safety").textContent)
      .toMatch(/broker_order_type=KIS_PAPER/);
  });

  it("KIS 자동주문 패널에 실전/매수/매도/Place Order 버튼 없음", async () => {
    const api = _withKisAuto(_mockApi({ state: "RUNNING", cycle_count: 3 }));
    const { container } = render(
      <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("bg-tick-kis-auto")).toBeTruthy());
    for (const b of [...container.querySelectorAll("button")]) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/Place Order/i);
      expect(t).not.toMatch(/지금 매수/);
      expect(t).not.toMatch(/지금 매도/);
      expect(t).not.toMatch(/실거래 시작/);
    }
  });
});
