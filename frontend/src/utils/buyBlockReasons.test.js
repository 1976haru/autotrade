/**
 * P-17: buyBlockReasons formatter 단위 테스트.
 *
 * invariant:
 *  - reason_code 별 기본 한국어 title.
 *  - details → 상세 문구.
 *  - unknown / null / undefined 안전 처리.
 *  - 실거래 활성화 문구 없음.
 */

import { describe, expect, it } from "vitest";

import {
  BUY_BLOCK_REASON_TITLES,
  buildBuyBlockDetail,
  buyBlockSeverityColor,
  formatBuyBlockReason,
  normalizeReasonCode,
} from "./buyBlockReasons";


describe("formatBuyBlockReason title 매핑", () => {
  it("MIN_LOT_NOT_AFFORDABLE", () => {
    expect(formatBuyBlockReason({ reason_code: "MIN_LOT_NOT_AFFORDABLE" }).title)
      .toBe("1주 가격이 투자한도 초과로 제외");
  });
  it("INSUFFICIENT_PAPER_CASH", () => {
    expect(formatBuyBlockReason({ reason_code: "INSUFFICIENT_PAPER_CASH" }).title)
      .toBe("남은 Paper 현금이 부족하여 매수 차단");
  });
  it("DAILY_BUY_LIMIT_EXCEEDED", () => {
    expect(formatBuyBlockReason({ reason_code: "DAILY_BUY_LIMIT_EXCEEDED" }).title)
      .toBe("일일 최대 매수금액을 초과하여 매수 차단");
  });
  it("SYMBOL_WEIGHT_LIMIT_EXCEEDED", () => {
    expect(formatBuyBlockReason({ reason_code: "SYMBOL_WEIGHT_LIMIT_EXCEEDED" }).title)
      .toBe("종목별 최대 비중을 초과하여 매수 차단");
  });
  it("DUPLICATE_POSITION_BUY_BLOCKED", () => {
    expect(formatBuyBlockReason({ reason_code: "DUPLICATE_POSITION_BUY_BLOCKED" }).title)
      .toBe("이미 보유 중인 종목이라 추가 매수 차단");
  });
  it("DAILY_ORDER_LIMIT_EXCEEDED (3-08)", () => {
    expect(formatBuyBlockReason({ reason_code: "DAILY_ORDER_LIMIT_EXCEEDED" }).title)
      .toBe("일일 최대 주문 횟수를 초과하여 매수 차단");
  });
  it("NOTIONAL_LIMIT_EXCEEDED (3-08)", () => {
    expect(formatBuyBlockReason({ reason_code: "NOTIONAL_LIMIT_EXCEEDED" }).title)
      .toBe("1회 주문금액 한도를 초과하여 매수 차단");
  });
  it("MAX_POSITIONS_REACHED (3-08)", () => {
    expect(formatBuyBlockReason({ reason_code: "MAX_POSITIONS_REACHED" }).title)
      .toBe("최대 보유 종목 수에 도달하여 매수 차단");
  });
  it("KIS_PAPER_ORDER_LIMIT_EXCEEDED alias → DAILY_ORDER_LIMIT_EXCEEDED (3-08)", () => {
    expect(formatBuyBlockReason({ reason_code: "KIS_PAPER_ORDER_LIMIT_EXCEEDED" }).title)
      .toBe("일일 최대 주문 횟수를 초과하여 매수 차단");
  });
  it("PRICE_STALE", () => {
    expect(formatBuyBlockReason({ reason_code: "PRICE_STALE" }).title)
      .toBe("현재가가 오래되어 매수 차단");
  });
  it("ABNORMAL_PRICE_MOVE", () => {
    expect(formatBuyBlockReason({ reason_code: "ABNORMAL_PRICE_MOVE" }).title)
      .toBe("가격 급등락이 감지되어 매수 차단");
  });
  it("INVALID_PRICE", () => {
    expect(formatBuyBlockReason({ reason_code: "INVALID_PRICE" }).title)
      .toBe("현재가가 비정상이라 매수 차단");
  });
  it("MARKET_CLOSED / NO_MARKET_DATA / NO_STRATEGY_SIGNAL / NO_CANDIDATE", () => {
    expect(formatBuyBlockReason("MARKET_CLOSED").title)
      .toMatch(/장 시간이 아니/);
    expect(formatBuyBlockReason("NO_MARKET_DATA").title)
      .toMatch(/시장 데이터가 없어/);
    expect(formatBuyBlockReason("NO_STRATEGY_SIGNAL").title)
      .toMatch(/전략 매수 신호가 없어/);
    expect(formatBuyBlockReason("NO_CANDIDATE").title)
      .toMatch(/매수 후보가 생성되지 않/);
  });
  it("BLOCKED_BY_PERMISSION_GATE / BLOCKED_BY_RISK_MANAGER / PAPER_EXECUTION_DISABLED", () => {
    expect(formatBuyBlockReason("BLOCKED_BY_PERMISSION_GATE").title)
      .toMatch(/PermissionGate/);
    expect(formatBuyBlockReason("BLOCKED_BY_RISK_MANAGER").title)
      .toMatch(/RiskManager/);
    expect(formatBuyBlockReason("PAPER_EXECUTION_DISABLED").title)
      .toMatch(/Paper 가상 실행/);
  });
});


describe("category / severity", () => {
  it("category 분류", () => {
    expect(formatBuyBlockReason("INSUFFICIENT_PAPER_CASH").category).toBe("capital");
    expect(formatBuyBlockReason("PRICE_STALE").category).toBe("price");
    expect(formatBuyBlockReason("BLOCKED_BY_RISK_MANAGER").category).toBe("risk");
    expect(formatBuyBlockReason("BLOCKED_BY_PERMISSION_GATE").category).toBe("permission");
    expect(formatBuyBlockReason("MARKET_CLOSED").category).toBe("market");
    expect(formatBuyBlockReason("NO_STRATEGY_SIGNAL").category).toBe("strategy");
  });
  it("severity 분류", () => {
    expect(formatBuyBlockReason("INVALID_PRICE").severity).toBe("danger");
    expect(formatBuyBlockReason("INSUFFICIENT_PAPER_CASH").severity).toBe("warning");
    expect(formatBuyBlockReason("BLOCKED_BY_PERMISSION_GATE").severity).toBe("blocked");
    expect(formatBuyBlockReason("MIN_LOT_NOT_AFFORDABLE").severity).toBe("info");
  });
  it("severity color 매핑", () => {
    expect(buyBlockSeverityColor("danger")).toBe("#ef4444");
    expect(buyBlockSeverityColor("warning")).toBe("#f59e0b");
    expect(buyBlockSeverityColor("unknown-x")).toBe("#64748b");
  });
});


describe("details → 상세 문구", () => {
  it("현금 부족 상세", () => {
    const f = formatBuyBlockReason({
      reason_code: "INSUFFICIENT_PAPER_CASH",
      details: { required_amount: 500000, remaining_cash: 300000 },
    });
    expect(f.detail).toBe("필요 금액 500,000원 > 남은 Paper 현금 300,000원");
  });
  it("1주 가격 상세", () => {
    const f = formatBuyBlockReason({
      reason_code: "MIN_LOT_NOT_AFFORDABLE",
      details: { price: 1500000, cap_krw: 1000000 },
    });
    expect(f.detail).toBe("1주 1,500,000원 > 종목당 투자한도 1,000,000원");
  });
  it("일일 한도 상세", () => {
    const f = formatBuyBlockReason({
      reason_code: "DAILY_BUY_LIMIT_EXCEEDED",
      details: { daily_buy_used: 2800000, daily_buy_limit: 3000000 },
    });
    expect(f.detail).toMatch(/오늘 매수 2,800,000원 \/ 한도 3,000,000원/);
  });
  it("종목 비중 상세 (0~1 비율)", () => {
    const f = formatBuyBlockReason({
      reason_code: "SYMBOL_WEIGHT_LIMIT_EXCEEDED",
      details: { weight_pct: 0.35, max_symbol_weight_pct: 0.2 },
    });
    expect(f.detail).toMatch(/35%/);
    expect(f.detail).toMatch(/20%/);
  });
  it("buildBuyBlockDetail 직접 호출 — 미지원 code 는 빈 문자열", () => {
    expect(buildBuyBlockDetail("MARKET_CLOSED", { foo: 1 })).toBe("");
  });
});


describe("정규화 + 안전 처리", () => {
  it("backend alias 정규화", () => {
    expect(normalizeReasonCode("INSUFFICIENT_CASH")).toBe("INSUFFICIENT_PAPER_CASH");
    expect(normalizeReasonCode("PAPER_GUARD_DUPLICATE")).toBe("DUPLICATE_POSITION_BUY_BLOCKED");
    expect(normalizeReasonCode("PRICE_OVER_CAP")).toBe("MIN_LOT_NOT_AFFORDABLE");
  });
  it("UNKNOWN reason 안전 표시", () => {
    const f = formatBuyBlockReason({ reason_code: "SOMETHING_WEIRD" });
    expect(f.code).toBe("UNKNOWN");
    expect(f.title).toBe(BUY_BLOCK_REASON_TITLES.UNKNOWN);
  });
  it("null / undefined 안전 처리", () => {
    expect(formatBuyBlockReason(null).code).toBe("UNKNOWN");
    expect(formatBuyBlockReason(undefined).code).toBe("UNKNOWN");
    expect(formatBuyBlockReason(null).title).toBeTruthy();
  });
  it("문자열 code 입력 허용", () => {
    expect(formatBuyBlockReason("PRICE_STALE").title).toBe("현재가가 오래되어 매수 차단");
  });
  it("backend title 우선 사용", () => {
    const f = formatBuyBlockReason({
      reason_code: "INSUFFICIENT_PAPER_CASH",
      title: "커스텀 제목",
    });
    expect(f.title).toBe("커스텀 제목");
  });
  it("reason_message 가 title 과 다르면 detail 로 노출", () => {
    const f = formatBuyBlockReason({
      reason_code: "NO_CANDIDATE",
      reason_message: "후보 종목이 없어 자동매매가 진행되지 않았습니다.",
    });
    expect(f.detail).toMatch(/후보 종목이 없어/);
  });
});


describe("실거래 활성화 문구 없음 (invariant)", () => {
  it("어떤 title 에도 실거래/Place Order/ENABLE_ 문구 없음", () => {
    for (const title of Object.values(BUY_BLOCK_REASON_TITLES)) {
      expect(title).not.toMatch(/Place Order/i);
      expect(title).not.toMatch(/실거래 활성화/);
      expect(title).not.toMatch(/ENABLE_/);
      expect(title).not.toMatch(/지금 매수/);
    }
  });
});
