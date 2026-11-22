import { describe, it, expect } from "vitest";
import {
  TRADING_STATE,
  computeTradingStatus,
  tradingStatusHeadline,
  summarizeTodayOrders,
  botFilledSymbolSet,
  classifyPositionSource,
} from "./tradingStatus";

describe("position source classification", () => {
  it("only FILLED bot orders count as '봇이 매수'", () => {
    const orders = [
      { symbol: "005930", filled_quantity: 0 },   // ordered, not filled
      { symbol: "005380", filled_quantity: 2 },   // filled by bot
    ];
    const set = botFilledSymbolSet(orders);
    expect(classifyPositionSource("005380", set)).toBe("BOT");
    expect(classifyPositionSource("005930", set)).toBe("EXISTING"); // ordered but unfilled
    expect(classifyPositionSource("207940", set)).toBe("EXISTING"); // never touched
  });

  it("when no bot order filled, every position is 기존 보유 (today's reality)", () => {
    const orders = [
      { symbol: "005380", filled_quantity: 0 },
      { symbol: "035420", filled_quantity: 0 },
    ];
    const set = botFilledSymbolSet(orders);
    for (const code of ["005380", "035420", "068270", "069500", "207940", "373220", "005935"]) {
      expect(classifyPositionSource(code, set)).toBe("EXISTING");
    }
  });
});

describe("computeTradingStatus — single source of truth", () => {
  it("emergency stop wins even if running", () => {
    const s = computeTradingStatus({ running: true, marketOpen: true, emergencyStop: true });
    expect(s.state).toBe(TRADING_STATE.BLOCKED);
    expect(s.reason).toBe("긴급정지");
    expect(s.icon).toBe("🔴");
  });

  it("not running => STOPPED (시작 버튼 필요)", () => {
    const s = computeTradingStatus({ running: false, marketOpen: true });
    expect(s.state).toBe(TRADING_STATE.STOPPED);
    expect(s.reason).toContain("시작");
    expect(s.icon).toBe("⏸");
  });

  it("running + account blocked => BLOCKED with reason", () => {
    const s = computeTradingStatus({ running: true, marketOpen: true, blockedReason: "계좌 주문불가" });
    expect(s.state).toBe(TRADING_STATE.BLOCKED);
    expect(s.reason).toBe("계좌 주문불가");
  });

  it("running but market closed => WAITING (장 마감)", () => {
    const s = computeTradingStatus({ running: true, marketOpen: false, marketPhase: "CLOSED" });
    expect(s.state).toBe(TRADING_STATE.WAITING);
    expect(s.reason).toBe("장 마감");
    expect(s.icon).toBe("🟡");
  });

  it("running, pre-open => WAITING (장 시작 전)", () => {
    const s = computeTradingStatus({ running: true, marketOpen: false, marketPhase: "PRE_OPEN" });
    expect(s.reason).toBe("장 시작 전");
  });

  it("running + market open + no block => TRADING with cycle", () => {
    const s = computeTradingStatus({ running: true, marketOpen: true, cycle: 12 });
    expect(s.state).toBe(TRADING_STATE.TRADING);
    expect(s.label).toBe("거래 중 (cycle 12)");
    expect(s.icon).toBe("🟢");
  });

  it("contradiction resolved: running flag true but loop-blocked shows one state, not both", () => {
    // running=true yet emergency on -> exactly one state (BLOCKED), never 'RUNNING+stopped'
    const s = computeTradingStatus({ running: true, emergencyStop: true, marketOpen: true });
    expect([TRADING_STATE.BLOCKED]).toContain(s.state);
  });

  it("headline formats icon + label + reason", () => {
    const s = computeTradingStatus({ running: true, marketOpen: true, cycle: 3 });
    expect(tradingStatusHeadline(s)).toBe("🟢 거래 중 (cycle 3)");
    const b = computeTradingStatus({ emergencyStop: true });
    expect(tradingStatusHeadline(b)).toBe("🔴 거래 불가 (긴급정지)");
  });
});

describe("summarizeTodayOrders — KST day", () => {
  // KST 2026-06-04 == UTC 2026-06-03 15:00 ~ 2026-06-04 14:59
  const now = new Date("2026-06-04T01:00:00Z"); // KST 10:00 on 06-04
  const rows = [
    { created_at: "2026-06-04T00:53:20", decision: "APPROVED", filled_quantity: 0, broker_status: "REJECTED" }, // today, rejected
    { created_at: "2026-06-04T01:00:00Z", decision: "APPROVED", filled_quantity: 2, broker_status: "FILLED" },  // today, filled
    { created_at: "2026-06-03T05:00:00Z", decision: "APPROVED", filled_quantity: 0 },                            // KST 06-03, excluded
  ];

  it("counts only today's KST rows; counts filled and rejected", () => {
    const s = summarizeTodayOrders(rows, now);
    expect(s.orderCount).toBe(2);
    expect(s.filledCount).toBe(1);
    expect(s.rejectedCount).toBe(1);
  });

  it("naive (no-tz) timestamps are treated as UTC", () => {
    // 2026-06-04T00:53:20 (naive) = KST 09:53 on 06-04 -> counted today
    const s = summarizeTodayOrders([rows[0]], now);
    expect(s.orderCount).toBe(1);
  });

  it("empty/undefined orders => zeros", () => {
    expect(summarizeTodayOrders(undefined, now)).toEqual({ orderCount: 0, filledCount: 0, rejectedCount: 0 });
  });
});
