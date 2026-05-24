/**
 * 3-09: noTradeReasons formatter 단위 테스트.
 *
 * invariant:
 *  - reason_code 별 기본 한국어 title (거래 없음 관점).
 *  - alias / unknown / null / undefined 안전 처리.
 *  - 실거래 활성화 / 자동 재주문 문구 없음.
 */

import { describe, expect, it } from "vitest";

import {
  NO_TRADE_REASON_TITLES,
  formatNoTradeReason,
  normalizeNoTradeReasonCode,
} from "./noTradeReasons";


describe("formatNoTradeReason 필수 한국어 문구 (spec §8)", () => {
  it("NO_SIGNAL", () => {
    expect(formatNoTradeReason("NO_SIGNAL").title)
      .toBe("조건에 맞는 매수/매도 신호가 없어 거래하지 않음");
  });
  it("NO_MARKET_DATA", () => {
    expect(formatNoTradeReason("NO_MARKET_DATA").title)
      .toBe("시장 데이터가 없어 거래하지 않음");
  });
  it("MARKET_CLOSED", () => {
    expect(formatNoTradeReason("MARKET_CLOSED").title)
      .toBe("장 시간이 아니어서 거래하지 않음");
  });
  it("PRICE_STALE", () => {
    expect(formatNoTradeReason("PRICE_STALE").title)
      .toBe("현재가가 오래되어 거래하지 않음");
  });
  it("BLOCKED_BY_RISK_MANAGER", () => {
    expect(formatNoTradeReason("BLOCKED_BY_RISK_MANAGER").title)
      .toBe("리스크 조건으로 거래 차단");
  });
  it("BLOCKED_BY_PERMISSION_GATE", () => {
    expect(formatNoTradeReason("BLOCKED_BY_PERMISSION_GATE").title)
      .toBe("주문 권한 조건으로 거래 차단");
  });
  it("BLOCKED_BY_KIS_READINESS", () => {
    expect(formatNoTradeReason("BLOCKED_BY_KIS_READINESS").title)
      .toMatch(/KIS 모의투자 준비 상태/);
  });
  it("EXIT_PLAN_INVALID", () => {
    expect(formatNoTradeReason("EXIT_PLAN_INVALID").title)
      .toMatch(/손절\/익절 계획이 유효하지 않아/);
  });
  it("EXIT_PLAN_MISSING", () => {
    expect(formatNoTradeReason("EXIT_PLAN_MISSING").title)
      .toMatch(/손절\/익절 계획이 없어/);
  });
  it("RISK_FLAGS_EXCEEDED", () => {
    expect(formatNoTradeReason("RISK_FLAGS_EXCEEDED").title)
      .toMatch(/위험 플래그 초과/);
  });
});


describe("category / severity", () => {
  it("category 분류", () => {
    expect(formatNoTradeReason("NO_SIGNAL").category).toBe("strategy");
    expect(formatNoTradeReason("NO_MARKET_DATA").category).toBe("market");
    expect(formatNoTradeReason("BLOCKED_BY_RISK_MANAGER").category).toBe("risk");
    expect(formatNoTradeReason("BLOCKED_BY_KIS_READINESS").category).toBe("permission");
    expect(formatNoTradeReason("PRICE_STALE").category).toBe("price");
  });
  it("severity 분류", () => {
    expect(formatNoTradeReason("INVALID_PRICE").severity).toBe("danger");
    expect(formatNoTradeReason("BLOCKED_BY_RISK_MANAGER").severity).toBe("blocked");
    expect(formatNoTradeReason("NO_SIGNAL").severity).toBe("info");
  });
});


describe("정규화 + 안전 처리", () => {
  it("HOLD / NO_OP → NO_SIGNAL", () => {
    expect(normalizeNoTradeReasonCode("HOLD")).toBe("NO_SIGNAL");
    expect(normalizeNoTradeReasonCode("NO_OP")).toBe("NO_SIGNAL");
  });
  it("alias 정규화", () => {
    expect(normalizeNoTradeReasonCode("RISK_FLAGS_EXCEED")).toBe("RISK_FLAGS_EXCEEDED");
    expect(normalizeNoTradeReasonCode("EXIT_PLAN_REQUIRED")).toBe("EXIT_PLAN_MISSING");
    expect(normalizeNoTradeReasonCode("KIS_NOT_READY")).toBe("BLOCKED_BY_KIS_READINESS");
    expect(normalizeNoTradeReasonCode("KIS_PAPER_ORDER_LIMIT_EXCEEDED"))
      .toBe("DAILY_ORDER_LIMIT_EXCEEDED");
  });
  it("UNKNOWN / null / undefined 안전", () => {
    expect(formatNoTradeReason("SOMETHING_WEIRD").code).toBe("UNKNOWN");
    expect(formatNoTradeReason(null).code).toBe("UNKNOWN");
    expect(formatNoTradeReason(undefined).code).toBe("UNKNOWN");
    expect(formatNoTradeReason(null).title).toBeTruthy();
  });
  it("객체 입력 + backend title 우선", () => {
    const f = formatNoTradeReason({ reason_code: "NO_SIGNAL", title: "커스텀" });
    expect(f.title).toBe("커스텀");
  });
});


describe("실거래 활성화 / 자동 재주문 문구 없음 (invariant)", () => {
  it("어떤 title 에도 금지 문구 없음", () => {
    for (const title of Object.values(NO_TRADE_REASON_TITLES)) {
      expect(title).not.toMatch(/Place Order/i);
      expect(title).not.toMatch(/실거래 활성화/);
      expect(title).not.toMatch(/ENABLE_/);
      expect(title).not.toMatch(/지금 매수/);
      expect(title).not.toMatch(/지금 매도/);
      expect(title).not.toMatch(/자동 재주문/);
    }
  });
});
