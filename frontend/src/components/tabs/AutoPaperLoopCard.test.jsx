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


function _mockApi(initialStatus = { state: "PAUSED", cycle_count: 0 }) {
  return {
    autoPaperStatus: vi.fn(async () => initialStatus),
    autoPaperStart: vi.fn(async () => ({ state: "RUNNING", cycle_count: 0 })),
    autoPaperStop: vi.fn(async () => ({ state: "STOPPED", cycle_count: 5 })),
    autoPaperEmergencyStop: vi.fn(async () => ({ state: "EMERGENCY_STOP", cycle_count: 5 })),
    autoPaperReset: vi.fn(async () => ({ state: "PAUSED", cycle_count: 0 })),
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
      // 첫 호출 인자 — pre_market 객체.
      const callArgs = api.autoPaperStart.mock.calls[0][0];
      expect(callArgs).toEqual({
        pre_market: {
          start_allowed:    true,
          verdict:          "READY_TO_START",
          blocking_reasons: [],
          warnings:         ["minor warn"],
        },
      });
    });

    it("preMarketCheckResult=null (legacy) → start enabled, no banner, no body", async () => {
      const api = _mockApi({ state: "PAUSED", cycle_count: 0 });
      const { getByTestId, queryByTestId } = render(
        <AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />
      );
      await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
      expect(queryByTestId("auto-paper-premarket-blocked-banner")).toBeNull();
      fireEvent.click(getByTestId("btn-start-auto-paper"));
      await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalled());
      // pre_market=null 이면 body=null 로 호출.
      expect(api.autoPaperStart.mock.calls[0][0]).toBeNull();
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
});
