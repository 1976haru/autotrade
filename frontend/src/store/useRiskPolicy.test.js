import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import { useRiskPolicy } from "./useRiskPolicy";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    getRiskPolicy:        vi.fn(),
    setEmergencyStop:     vi.fn(),
    emergencyStopHistory: vi.fn(),
  },
}));


const _POLICY = {
  max_order_notional: 1_000_000, max_daily_loss: 200_000,
  max_positions: 5, max_symbol_exposure: 1_500_000,
  enable_live_trading: false, enable_ai_execution: false,
};


describe("useRiskPolicy", () => {
  beforeEach(() => {
    backendApi.getRiskPolicy.mockReset();
    backendApi.setEmergencyStop.mockReset();
    backendApi.emergencyStopHistory.mockReset();
    backendApi.emergencyStopHistory.mockResolvedValue([]);
  });

  it("fetches policy + history on mount", async () => {
    backendApi.getRiskPolicy.mockResolvedValue(_POLICY);
    backendApi.emergencyStopHistory.mockResolvedValue([
      { id: 1, enabled: true,  created_at: "2026-05-05T12:00:00+00:00" },
      { id: 2, enabled: false, created_at: "2026-05-05T12:05:00+00:00" },
    ]);

    const { result } = renderHook(() => useRiskPolicy());
    await waitFor(() => {
      expect(result.current.policy).toEqual(_POLICY);
      expect(result.current.history).toHaveLength(2);
    });
    expect(backendApi.emergencyStopHistory).toHaveBeenCalledTimes(1);
  });

  it("toggleEmergency forwards optional note and refreshes history", async () => {
    backendApi.getRiskPolicy.mockResolvedValue(_POLICY);
    backendApi.setEmergencyStop.mockResolvedValueOnce({ emergency_stop: true });
    backendApi.emergencyStopHistory.mockResolvedValue([
      { id: 1, enabled: true, note: "vol spike", created_at: "2026-05-05T12:00:00+00:00" },
    ]);

    const { result } = renderHook(() => useRiskPolicy());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.toggleEmergency("vol spike");
    });

    expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(true, { note: "vol spike" });
    // Mount + post-toggle = 2 history fetches
    expect(backendApi.emergencyStopHistory).toHaveBeenCalledTimes(2);
    expect(result.current.emergencyStop).toBe(true);
  });

  it("toggleEmergency without note passes null decision", async () => {
    backendApi.getRiskPolicy.mockResolvedValue(_POLICY);
    backendApi.setEmergencyStop.mockResolvedValue({ emergency_stop: true });

    const { result } = renderHook(() => useRiskPolicy());
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.toggleEmergency();
    });

    expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(true, null);
  });

  it("history fetch failure surfaces in error without breaking policy", async () => {
    backendApi.getRiskPolicy.mockResolvedValue(_POLICY);
    backendApi.emergencyStopHistory.mockRejectedValue(new Error("history-down"));

    const { result } = renderHook(() => useRiskPolicy());
    await waitFor(() => expect(result.current.error).toBe("history-down"));
    expect(result.current.policy).toEqual(_POLICY);
    expect(result.current.history).toEqual([]);
  });
});
