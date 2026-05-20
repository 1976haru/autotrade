/**
 * Tests for `src/utils/marketHours.js` (fix/market-closed-state-distinction).
 *
 * 사용자 요청서 §5 — 4가지 시점 시나리오 매트릭스를 frontend client-side
 * 시계산으로도 동일하게 보장.
 *   - 평일 08:50 KST → PRE_OPEN
 *   - 평일 09:00 KST → OPEN
 *   - 평일 15:31 KST → CLOSED
 *   - 토 / 일       → WEEKEND
 *
 * 본 utility 는 운영자 PC 의 timezone 과 무관하게 동작해야 한다 — Date.UTC
 * 기반으로 시각을 만들어 검증.
 */

import { describe, expect, it } from "vitest";

import {
  MarketPhase,
  currentMarketPhase,
  isMarketClosed,
  isMarketOpen,
  marketClosedHeadline,
  marketPhaseLabel,
  marketPhaseReason,
  toKstParts,
} from "./marketHours";


/**
 * Build a JS Date that, when interpreted in KST, equals (year, month, day,
 * hour, minute). The conversion is deliberately explicit: KST = UTC + 9, so a
 * KST clock-time is built by subtracting 9h from the UTC components.
 */
function kstAsDate(year, month, day, hour, minute) {
  // Date.UTC(month is 0-indexed) → ms since epoch (UTC).
  // We pass the *KST* clock-time and adjust by -9h to get the equivalent UTC.
  const utcMs = Date.UTC(year, month - 1, day, hour - 9, minute);
  return new Date(utcMs);
}


describe("currentMarketPhase — 사용자 요청 매트릭스", () => {
  it("평일 08:50 KST → PRE_OPEN", () => {
    // 2026-05-18 = 월요일
    const d = kstAsDate(2026, 5, 18, 8, 50);
    expect(currentMarketPhase(d)).toBe(MarketPhase.PRE_OPEN);
  });

  it("평일 09:00 KST → OPEN", () => {
    const d = kstAsDate(2026, 5, 18, 9, 0);
    expect(currentMarketPhase(d)).toBe(MarketPhase.OPEN);
  });

  it("평일 15:31 KST → CLOSED", () => {
    const d = kstAsDate(2026, 5, 18, 15, 31);
    expect(currentMarketPhase(d)).toBe(MarketPhase.CLOSED);
  });

  it("토요일 → WEEKEND", () => {
    // 2026-05-23 = 토요일
    const d = kstAsDate(2026, 5, 23, 10, 0);
    expect(currentMarketPhase(d)).toBe(MarketPhase.WEEKEND);
  });

  it("일요일 → WEEKEND", () => {
    // 2026-05-24 = 일요일
    const d = kstAsDate(2026, 5, 24, 14, 0);
    expect(currentMarketPhase(d)).toBe(MarketPhase.WEEKEND);
  });
});


describe("currentMarketPhase — 경계 조건", () => {
  it("15:29 KST 는 OPEN (정규장 마지막 분)", () => {
    const d = kstAsDate(2026, 5, 18, 15, 29);
    expect(currentMarketPhase(d)).toBe(MarketPhase.OPEN);
  });

  it("15:30 KST 정각은 CLOSED (장 종료 boundary)", () => {
    const d = kstAsDate(2026, 5, 18, 15, 30);
    expect(currentMarketPhase(d)).toBe(MarketPhase.CLOSED);
  });

  it("08:59 KST 는 PRE_OPEN (장 시작 직전)", () => {
    const d = kstAsDate(2026, 5, 18, 8, 59);
    expect(currentMarketPhase(d)).toBe(MarketPhase.PRE_OPEN);
  });

  it("00:00 KST 는 PRE_OPEN (자정 직후)", () => {
    const d = kstAsDate(2026, 5, 18, 0, 0);
    expect(currentMarketPhase(d)).toBe(MarketPhase.PRE_OPEN);
  });

  it("23:59 KST 는 CLOSED (자정 직전)", () => {
    const d = kstAsDate(2026, 5, 18, 23, 59);
    expect(currentMarketPhase(d)).toBe(MarketPhase.CLOSED);
  });
});


describe("isMarketOpen / isMarketClosed", () => {
  it("OPEN phase → isMarketOpen true, isMarketClosed false", () => {
    const d = kstAsDate(2026, 5, 18, 12, 0);
    expect(isMarketOpen(d)).toBe(true);
    expect(isMarketClosed(d)).toBe(false);
  });

  it("CLOSED phase → isMarketOpen false, isMarketClosed true", () => {
    const d = kstAsDate(2026, 5, 18, 16, 0);
    expect(isMarketOpen(d)).toBe(false);
    expect(isMarketClosed(d)).toBe(true);
  });

  it("WEEKEND → isMarketClosed true", () => {
    const d = kstAsDate(2026, 5, 23, 10, 0);
    expect(isMarketClosed(d)).toBe(true);
  });

  it("PRE_OPEN → isMarketClosed true (장 시작 전도 '닫혀 있음'으로 간주)", () => {
    const d = kstAsDate(2026, 5, 18, 8, 0);
    expect(isMarketClosed(d)).toBe(true);
    expect(isMarketOpen(d)).toBe(false);
  });
});


describe("toKstParts", () => {
  it("KST 시각을 정확히 분해", () => {
    const d = kstAsDate(2026, 5, 18, 12, 34);
    const parts = toKstParts(d);
    expect(parts.year).toBe(2026);
    expect(parts.month).toBe(5);
    expect(parts.day).toBe(18);
    expect(parts.hour).toBe(12);
    expect(parts.minute).toBe(34);
    expect(parts.weekday).toBe(0); // 월요일 = 0
  });

  it("주말 weekday 계산 (토=5, 일=6)", () => {
    expect(toKstParts(kstAsDate(2026, 5, 23, 10, 0)).weekday).toBe(5);
    expect(toKstParts(kstAsDate(2026, 5, 24, 10, 0)).weekday).toBe(6);
  });
});


describe("UI label / reason helper — 사용자 요청 문구 매칭", () => {
  it("CLOSED → headline 에 '장 종료로 신규 판단 없음' 포함", () => {
    const headline = marketClosedHeadline(MarketPhase.CLOSED);
    expect(headline).toContain("CLOSED");
    expect(headline).toContain("장 종료로 신규 판단 없음");
  });

  it("PRE_OPEN → headline 에 '장 시작 전' 포함", () => {
    expect(marketClosedHeadline(MarketPhase.PRE_OPEN)).toContain("장 시작 전");
  });

  it("WEEKEND → headline 에 '주말 휴장' 포함", () => {
    expect(marketClosedHeadline(MarketPhase.WEEKEND)).toContain("주말 휴장");
  });

  it("OPEN → headline 은 빈 문자열 (정규장은 banner 안 띄움)", () => {
    expect(marketClosedHeadline(MarketPhase.OPEN)).toBe("");
  });

  it("label 매핑은 한국어", () => {
    expect(marketPhaseLabel(MarketPhase.OPEN)).toBe("정규장 열림");
    expect(marketPhaseLabel(MarketPhase.CLOSED)).toBe("장 종료");
    expect(marketPhaseLabel(MarketPhase.WEEKEND)).toBe("주말 휴장");
    expect(marketPhaseLabel(MarketPhase.PRE_OPEN)).toBe("장 시작 전");
  });

  it("reason 은 한 줄 설명", () => {
    expect(marketPhaseReason(MarketPhase.CLOSED)).toContain("신규 판단 없음");
    expect(marketPhaseReason(MarketPhase.OPEN)).toContain("정규장");
  });
});
