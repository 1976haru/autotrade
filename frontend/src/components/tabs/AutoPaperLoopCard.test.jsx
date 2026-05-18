/**
 * AutoPaperLoopCard 단위 테스트.
 *
 * invariant 강제:
 * - "uvicorn" / "Place Order" / "지금 매수" / "지금 매도" / "실거래 시작" / "ENABLE_*" 라벨 0건
 * - 시작 / 정지 / 긴급정지 버튼이 정확한 API 를 호출
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AutoPaperLoopCard } from "./AutoPaperLoopCard";


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
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalledTimes(1));
  });

  it("clicking 정지 button calls autoPaperStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() => expect(api.autoPaperStop).toHaveBeenCalledTimes(1));
  });

  it("clicking 긴급정지 button calls autoPaperEmergencyStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
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
      const callArgs = api.autoPaperStart.mock.calls[0][0];
      expect(callArgs).toEqual({
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
      // #4-RiskProfileUI: pre_market=null 이라도 body 는 risk_profile 만 포함.
      expect(api.autoPaperStart.mock.calls[0][0]).toEqual({
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
        .toEqual({ risk_profile: "BALANCED" });
    });

    it("CONSERVATIVE selection is forwarded to start payload", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-CONSERVATIVE"));
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      expect(api.autoPaperStart.mock.calls[0][0])
        .toEqual({ risk_profile: "CONSERVATIVE" });
    });

    it("AGGRESSIVE selection is forwarded to start payload", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      fireEvent.click(screen.getByTestId("risk-profile-card-AGGRESSIVE"));
      fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      expect(api.autoPaperStart.mock.calls[0][0])
        .toEqual({ risk_profile: "AGGRESSIVE" });
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

    it("selector disabled while RUNNING — clicks do not change selection", async () => {
      const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
      render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      const conservativeCard = screen.getByTestId(
        "risk-profile-card-CONSERVATIVE",
      );
      // disabled button click is a no-op; selection should stay BALANCED.
      fireEvent.click(conservativeCard);
      expect(screen.getByTestId("risk-profile-radiogroup")
        .getAttribute("data-selected")).toBe("BALANCED");
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
