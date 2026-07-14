import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backendApi } from "../services/backend/client";
import {
  KS_ACTIVE_POLL_MS,
  KS_HIDDEN_POLL_MS,
  KS_IDLE_POLL_MS,
  computeKillSwitchPollIntervalMs,
  useKillSwitchStatus,
} from "./useKillSwitchStatus";


vi.mock("../services/backend/client", () => ({
  backendApi: {
    emergencyStopStatus: vi.fn(),
    setEmergencyStop:    vi.fn(),
  },
}));


function _status(overrides = {}) {
  return {
    level: "OFF", emergency_stop: false, reason_code: null,
    decided_by: null, note: null, active_since: null,
    cancel_candidate_count: 0, liquidation_candidate_count: 0,
    ...overrides,
  };
}


describe("computeKillSwitchPollIntervalMs", () => {
  it("polls fast when a level is active", () => {
    expect(computeKillSwitchPollIntervalMs({ level: "LEVEL_1" })).toBe(KS_ACTIVE_POLL_MS);
  });
  it("polls slow when OFF", () => {
    expect(computeKillSwitchPollIntervalMs({ level: "OFF" })).toBe(KS_IDLE_POLL_MS);
  });
  it("polls slowest when the tab is hidden, regardless of level", () => {
    expect(computeKillSwitchPollIntervalMs({ level: "LEVEL_1", hidden: true })).toBe(KS_HIDDEN_POLL_MS);
  });
});


describe("useKillSwitchStatus", () => {
  // vi.useFakeTimers 환경에선 testing-library의 waitFor가 못 돈다 (useApprovals.test.js
  // 와 동일한 이유) — microtask chain을 직접 풀어 scheduleNext()까지 도달시킨다.
  const _flush = async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve();
  };
  const _mount = async () => {
    let r;
    await act(async () => {
      r = renderHook(() => useKillSwitchStatus());
      await _flush();
    });
    return r;
  };

  beforeEach(() => {
    backendApi.emergencyStopStatus.mockReset();
    backendApi.setEmergencyStop.mockReset();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches status on mount", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { result } = await _mount();

    expect(result.current.loading).toBe(false);
    expect(result.current.status.level).toBe("LEVEL_1");
  });

  it("re-polls on the fast cadence while a level is active", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    vi.useFakeTimers();
    const r = await _mount();
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(KS_ACTIVE_POLL_MS); await _flush(); });
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(2);

    r.unmount();
  });

  it("does not re-poll before the idle cadence elapses when OFF", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "OFF" }));
    vi.useFakeTimers();
    const r = await _mount();
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(1);

    await act(async () => { vi.advanceTimersByTime(KS_ACTIVE_POLL_MS); await _flush(); });
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(KS_IDLE_POLL_MS - KS_ACTIVE_POLL_MS);
      await _flush();
    });
    expect(backendApi.emergencyStopStatus).toHaveBeenCalledTimes(2);

    r.unmount();
  });

  it("disable() calls setEmergencyStop(false, ...) then refreshes", async () => {
    backendApi.emergencyStopStatus
      .mockResolvedValueOnce(_status({ level: "LEVEL_1", decided_by: "operator" }))
      .mockResolvedValueOnce(_status({ level: "OFF" }));
    backendApi.setEmergencyStop.mockResolvedValueOnce({ emergency_stop: false, level: "OFF" });

    const { result } = await _mount();
    expect(result.current.status.level).toBe("LEVEL_1");

    let outcome;
    await act(async () => {
      outcome = await result.current.disable({ decided_by: "user-via-claude", note: "banner" });
    });

    expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(
      false, { decided_by: "user-via-claude", note: "banner" },
    );
    expect(outcome).toEqual({ ok: true });
    expect(result.current.status.level).toBe("OFF");
  });

  it("surfaces disable() failures without throwing", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    backendApi.setEmergencyStop.mockRejectedValueOnce(new Error("network down"));

    const { result } = await _mount();
    expect(result.current.status.level).toBe("LEVEL_1");

    let outcome;
    await act(async () => {
      outcome = await result.current.disable();
    });
    expect(outcome).toEqual({ ok: false, message: "network down" });
    expect(result.current.error).toBe("network down");
  });
});
