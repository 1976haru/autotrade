import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  kstDate, ensurePaperSessionApproval, parseOrderPathBlockers, formatKoreanDate,
  orderBlockerMessage, ORDER_PATH_NOT_READY,
} from "./paperSessionApproval";

describe("paper session approval date", () => {
  it("uses the actual KST calendar date instead of a fixed legacy date", () => {
    expect(kstDate(new Date("2026-11-16T15:01:00Z"))).toBe("2026-11-17");
  });
});

describe("P1-A order path readiness before the approval popup", () => {
  let invoke;
  beforeEach(() => {
    invoke = vi.fn(async () => ({}));
    window.__TAURI_INTERNALS__ = { invoke };
  });
  afterEach(() => { delete window.__TAURI_INTERNALS__; });

  const baseApi = (over = {}) => ({
    autoPaperRunReadiness: vi.fn(async () => ({ order_blockers: [] })),
    autoPaperNextTradingDay: vi.fn(async () => ({ today: "2026-11-23", target_date: "2026-11-23" })),
    paperSessionApprovalGet: vi.fn(async () => ({ status: "NONE" })),
    ...over,
  });

  it("blockers → confirm NOT called, no approval invoke, reason carried", async () => {
    const confirmFn = vi.fn(() => true);
    const api = baseApi({
      autoPaperRunReadiness: vi.fn(async () => ({ order_blockers: ["MARKET_DATA_PROVIDER_NOT_KIS"] })),
    });
    const r = await ensurePaperSessionApproval({ api, capitalSettings: {}, confirmFn });
    expect(r).toEqual({ ok: false, reason: ORDER_PATH_NOT_READY, blockers: ["MARKET_DATA_PROVIDER_NOT_KIS"] });
    expect(confirmFn).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
  });

  it("server 409 ORDER_PATH_NOT_READY from invoke is mapped to blockers", async () => {
    invoke.mockRejectedValueOnce(
      'HTTP 409: {"detail":"ORDER_PATH_NOT_READY:BACKGROUND_TICK_DISABLED,FILL_POLLER_DISABLED"}');
    const r = await ensurePaperSessionApproval({ api: baseApi(), capitalSettings: {}, confirmFn: () => true });
    expect(r.ok).toBe(false);
    expect(r.reason).toBe(ORDER_PATH_NOT_READY);
    expect(r.blockers).toEqual(["BACKGROUND_TICK_DISABLED", "FILL_POLLER_DISABLED"]);
  });

  it("other invoke errors still throw", async () => {
    invoke.mockRejectedValueOnce('HTTP 409: {"detail":"EMERGENCY_STOP_ACTIVE"}');
    await expect(ensurePaperSessionApproval({ api: baseApi(), capitalSettings: {}, confirmFn: () => true }))
      .rejects.toBeTruthy();
  });

  it("ready → popup shown for today and approval invoked", async () => {
    const confirmFn = vi.fn(() => true);
    const r = await ensurePaperSessionApproval({ api: baseApi(), capitalSettings: {}, confirmFn });
    expect(confirmFn).toHaveBeenCalledTimes(1);
    expect(confirmFn.mock.calls[0][0]).toContain("오늘 장중");
    expect(invoke).toHaveBeenCalledWith("approve_paper_session", { targetDate: "2026-11-23", explicitAck: true });
    expect(r).toMatchObject({ ok: true, targetDate: "2026-11-23", isNextSession: false });
  });

  it("P2: target date from backend next trading day, popup says (다음 거래일)", async () => {
    const confirmFn = vi.fn(() => true);
    const api = baseApi({
      autoPaperNextTradingDay: vi.fn(async () => ({ today: "2026-11-28", target_date: "2026-11-30" })),
    });
    const r = await ensurePaperSessionApproval({ api, capitalSettings: {}, confirmFn });
    expect(confirmFn.mock.calls[0][0]).toContain("2026-11-30 (다음 거래일)");
    expect(confirmFn.mock.calls[0][0]).toContain("11월 30일(월)");
    expect(invoke).toHaveBeenCalledWith("approve_paper_session", { targetDate: "2026-11-30", explicitAck: true });
    expect(r.isNextSession).toBe(true);
  });

  it("requires explicit confirmation of a higher saved limit for an active session", async () => {
    const api = baseApi({
      paperSessionApprovalGet: vi.fn(async () => ({
        status: "ACTIVE", target_date: "2026-11-23",
        limits: { daily_buy_krw: 10_000_000, total_budget_krw: 10_000_000 },
      })),
      runtimeConfigGet: vi.fn(async () => ({
        daily_buy_limit_krw: { value: 50_000_000 },
        total_paper_budget_krw: { value: 8_000_000 },
      })),
    });
    const confirmFn = vi.fn().mockReturnValueOnce(false).mockReturnValueOnce(true);
    const args = { api, capitalSettings: {}, confirmFn };
    expect(await ensurePaperSessionApproval(args)).toMatchObject({ cancelled: true });
    expect(invoke).not.toHaveBeenCalled();
    expect(await ensurePaperSessionApproval(args)).toMatchObject({ ok: true, reused: false });
    expect(confirmFn.mock.calls[1][0]).toContain((50_000_000).toLocaleString());
    expect(invoke).toHaveBeenCalledWith("approve_paper_session", {
      targetDate: "2026-11-23", explicitAck: true,
      confirmedDailyBuyKrw: 50_000_000, confirmedTotalBudgetKrw: 8_000_000,
    });
  });

  it("readiness fetch failure does not block (server 409 is final)", async () => {
    const confirmFn = vi.fn(() => true);
    const api = baseApi({ autoPaperRunReadiness: vi.fn(async () => { throw new Error("down"); }) });
    const r = await ensurePaperSessionApproval({ api, capitalSettings: {}, confirmFn });
    expect(r.ok).toBe(true);
  });
});

describe("helpers", () => {
  it("parseOrderPathBlockers", () => {
    expect(parseOrderPathBlockers("nope")).toBeNull();
    expect(parseOrderPathBlockers(new Error("ORDER_PATH_NOT_READY:KIS_CREDENTIALS_MISSING")))
      .toEqual(["KIS_CREDENTIALS_MISSING"]);
  });
  it("formatKoreanDate", () => {
    expect(formatKoreanDate("2026-12-28")).toBe("12월 28일(월)");
  });
  it("Korean messages for every blocker code", () => {
    expect(orderBlockerMessage("MARKET_DATA_PROVIDER_NOT_KIS")).toContain("MARKET_DATA_PROVIDER=kis");
    expect(orderBlockerMessage("BACKGROUND_TICK_DISABLED")).toContain("ENABLE_AI_PAPER_BACKGROUND_TICK=true");
    expect(orderBlockerMessage("KIS_CREDENTIALS_MISSING")).toContain("앱키");
    expect(orderBlockerMessage("FILL_POLLER_DISABLED")).toContain("KIS_PAPER_FILL_POLLING=true");
    expect(orderBlockerMessage("KIS_PAPER_SMOKE_MODE_ON")).toContain("KIS_PAPER_SMOKE_MODE=false");
  });
});
