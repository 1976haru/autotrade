import { describe, it, expect } from "vitest";
import {
  BEGINNER_TERMS,
  toBeginnerTerm,
  dryRunLabel,
} from "./beginnerTerms";

describe("beginnerTerms (PART4-3 초보자 용어 변환)", () => {
  it("전문용어를 일상어로 바꾼다", () => {
    expect(toBeginnerTerm("WINDOW_CLOSED")).toBe("지금은 자동매매 시간이 아님");
    expect(toBeginnerTerm("EGW00201")).toContain("자동 대기");
    expect(toBeginnerTerm("dry_run")).toContain("점검만");
    expect(toBeginnerTerm("kis")).toBe("한투 실시간 시세");
  });

  it("모르는 코드는 원본을 그대로 반환 (정보 숨기지 않음)", () => {
    expect(toBeginnerTerm("SOME_UNKNOWN_CODE")).toBe("SOME_UNKNOWN_CODE");
  });

  it("null/undefined/빈문자 안전 처리", () => {
    expect(toBeginnerTerm(null)).toBe("");
    expect(toBeginnerTerm(undefined)).toBe("");
    expect(toBeginnerTerm("  ")).toBe("");
  });

  it("dryRunLabel 은 점검/모의주문을 구분", () => {
    expect(dryRunLabel(true)).toContain("점검만");
    expect(dryRunLabel(false)).toContain("모의주문");
  });

  it("실거래 활성화 문구를 만들지 않는다 (안전 invariant)", () => {
    // 어떤 라벨도 '실거래 시작 / 지금 매수 / Place Order' 같은 enabling 문구를
    // 포함하면 안 된다 — 본 모듈은 표시 전용.
    const all = Object.values(BEGINNER_TERMS).join(" ");
    expect(all).not.toMatch(/실거래 시작|지금 매수|지금 매도|Place Order|실거래 활성화/);
  });

  it("EGW00201/WINDOW_CLOSED 를 '정상' 맥락으로 안내", () => {
    // 이 둘은 오류가 아니라 정상 동작 — '정상' 또는 보호/대기 맥락이어야.
    expect(toBeginnerTerm("EGW00201")).toMatch(/정상|대기|보호/);
    expect(toBeginnerTerm("MARKET_CLOSED")).toContain("정상");
  });
});
