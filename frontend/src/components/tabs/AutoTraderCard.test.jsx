/**
 * 체크리스트 #60: AutoTraderCard 테스트.
 *
 * 검증 invariant:
 * - "즉시 매수" / "Place Order" / "지금 주문" 같은 LIVE 발주 버튼 없음.
 * - 모의매매 안내 banner 노출.
 * - decision / portfolio / risk checks 정상 렌더.
 * - Emergency Stop 토글이 backendApi 호출과 후속 reload 트리거.
 */

import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AutoTraderCard } from "./AutoTraderCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    autoTraderStatus:        vi.fn(),
    autoTraderPortfolio:     vi.fn(),
    autoTraderEmergencyStop: vi.fn(),
  },
}));

const NOW_ISO = "2026-05-13T09:00:00.000Z";

function _statusBody({ action = "BUY", emergencyStop = false, enableLive = false } = {}) {
  return {
    paperStatus: {
      mode: "SIMULATION",
      is_paper_mode: true,
      paper_broker_kind: "MOCK",
      kis_is_paper: true,
      enable_live_trading: enableLive,
      enable_ai_execution: false,
      enable_futures_live_trading: false,
      fill_polling_enabled: false,
    },
    lastReport: {
      mode: "SIMULATION",
      emergencyStop,
      startedAt:  NOW_ISO,
      finishedAt: NOW_ISO,
      notice: "본 결과는 모의매매 검증용입니다.",
      summary: { total: 1, buy: action === "BUY" ? 1 : 0,
                 sell: action === "SELL" ? 1 : 0,
                 hold: action === "HOLD" ? 1 : 0,
                 executed: 1, blocked: 0, rejected: 0 },
      plans: [
        {
          symbol: "005930",
          strategySignals: [{
            strategyId: "sma_crossover", signal: action,
            confidence: 80, reason: "신호 r1", indicators: {},
          }],
          decision: {
            action,
            symbol: "005930",
            confidence: 80,
            positionSize: 1,
            reason: "1개 전략이 BUY 신호",
            usedStrategies: ["sma_crossover"],
            riskChecks: {
              maxPositionOk: true,
              dailyLossLimitOk: true,
              cooldownOk: true,
              cashAvailableOk: true,
            },
            createdAt: NOW_ISO,
            isOrderIntent: false,
          },
          routingDecision: "APPROVED",
          routingReasons: [],
          auditId: 12,
          executed: true,
          fillQuantity: 1,
          fillPrice: 60_000,
          blockedBy: null,
          error: null,
        },
      ],
      portfolio: {
        cash: 4_940_000, equity: 5_000_000, buyingPower: 4_940_000,
        positions: [{ symbol: "005930", quantity: 1,
                      avg_price: 60_000, market_price: 60_000 }],
      },
    },
    recentReportCount: 1,
    emergencyStop,
    enableLiveTrading: enableLive,
    enableAiExecution: false,
  };
}

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.autoTraderStatus.mockResolvedValue(_statusBody());
  backendApi.autoTraderPortfolio.mockResolvedValue({
    cash: 4_940_000, equity: 5_000_000, buyingPower: 4_940_000,
    positions: [{ symbol: "005930", quantity: 1, avg_price: 60_000,
                   market_price: 60_000 }],
  });
  backendApi.autoTraderEmergencyStop.mockResolvedValue({
    enabled: true, note: null, updatedAt: NOW_ISO,
    notice: "in-memory 상태만 변경",
  });
});


describe("<AutoTraderCard>", () => {
  it("renders paper banner and decision when status arrives", async () => {
    const { findByTestId, getByTestId } = render(<AutoTraderCard />);
    await findByTestId("autotrader-paper-banner");
    await waitFor(() => {
      expect(getByTestId("autotrader-action").textContent).toBe("BUY");
    });
    await findByTestId("autotrader-portfolio");
    await findByTestId("autotrader-risk-checks");
    await findByTestId("autotrader-strategy-sma_crossover");
  });

  it("uses paper-safe banner color when no LIVE flag", async () => {
    const { findByTestId } = render(<AutoTraderCard />);
    const banner = await findByTestId("autotrader-paper-banner");
    expect(banner.textContent).toMatch(/모의매매 모드/);
  });

  it("warns when LIVE flag is active", async () => {
    backendApi.autoTraderStatus.mockResolvedValueOnce(_statusBody({ enableLive: true }));
    const { findByTestId } = render(<AutoTraderCard />);
    const banner = await findByTestId("autotrader-paper-banner");
    expect(banner.textContent).toMatch(/LIVE flag 활성/);
  });

  it("emergency stop button toggles via backend", async () => {
    const { findByText } = render(<AutoTraderCard />);
    const btn = await findByText(/긴급 정지/);
    fireEvent.click(btn);
    await waitFor(() => {
      expect(backendApi.autoTraderEmergencyStop).toHaveBeenCalledWith(true);
    });
  });

  it("never renders LIVE-execution buttons (invariant lock)", async () => {
    const { findByTestId, queryByText } = render(<AutoTraderCard />);
    await findByTestId("autotrader-decision");
    // 절대 금지 라벨: "즉시 매수", "Place Order", "지금 주문", "실거래".
    expect(queryByText(/즉시 매수/)).toBeNull();
    expect(queryByText(/Place Order/i)).toBeNull();
    expect(queryByText(/지금 주문/)).toBeNull();
    expect(queryByText(/실거래/)).toBeNull();
  });

  it("shows empty hint when lastReport is null", async () => {
    backendApi.autoTraderStatus.mockResolvedValueOnce({
      paperStatus: { mode: "SIMULATION", enable_live_trading: false },
      lastReport: null,
      recentReportCount: 0,
      emergencyStop: false,
      enableLiveTrading: false,
      enableAiExecution: false,
    });
    const { findByTestId } = render(<AutoTraderCard />);
    await findByTestId("autotrader-empty");
  });

  it("surfaces error when backend fails", async () => {
    backendApi.autoTraderStatus.mockRejectedValueOnce(new Error("backend down"));
    const { findByTestId } = render(<AutoTraderCard />);
    await findByTestId("autotrader-error");
  });
});
