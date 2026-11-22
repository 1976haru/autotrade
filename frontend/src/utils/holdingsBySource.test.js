import { describe, it, expect } from "vitest";
import { splitHoldingsBySource } from "./holdingsBySource";

describe("splitHoldingsBySource (설계 B 조각 1)", () => {
  it("BOT/MANUAL 종목을 각 섹션으로 분리 + 섹션별 수익률", () => {
    const positions = [
      { symbol: "005930", quantity: 10, avg_price: 70000, market_price: 77000, source: "BOT", bot_qty: 10, manual_qty: 0 },
      { symbol: "033780", quantity: 5, avg_price: 80000, market_price: 84000, source: "MANUAL", bot_qty: 0, manual_qty: 5 },
    ];
    const { bot, manual } = splitHoldingsBySource(positions);
    expect(bot.count).toBe(1);
    expect(bot.eval_pnl_krw).toBe((77000 - 70000) * 10);
    expect(bot.return_pct).toBe(10);
    expect(manual.count).toBe(1);
    expect(manual.eval_pnl_krw).toBe((84000 - 80000) * 5);
    expect(manual.return_pct).toBe(5);
  });

  it("MIXED 종목은 봇/직접 lot 으로 양쪽 분할", () => {
    const positions = [
      { symbol: "005930", quantity: 100, avg_price: 70000, market_price: 77000, source: "MIXED", bot_qty: 60, manual_qty: 40 },
    ];
    const { bot, manual } = splitHoldingsBySource(positions);
    expect(bot.rows[0]._qty).toBe(60);
    expect(manual.rows[0]._qty).toBe(40);
    expect(bot.eval_pnl_krw).toBe((77000 - 70000) * 60);
    expect(manual.eval_pnl_krw).toBe((77000 - 70000) * 40);
  });

  it("UNTAGGED/데이터부족은 보수적으로 직접 보유 측", () => {
    const positions = [{ symbol: "x", quantity: 7, avg_price: 0, market_price: 0, source: "UNTAGGED", bot_qty: 0, manual_qty: 7 }];
    const { bot, manual } = splitHoldingsBySource(positions);
    expect(bot.count).toBe(0);
    expect(manual.count).toBe(1);
    expect(manual.rows[0]._qty).toBe(7);
  });
});
