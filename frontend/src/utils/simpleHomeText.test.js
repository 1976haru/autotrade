import { describe, it, expect } from "vitest";
import {
  translateStopReason,
  heroFromStatus,
  moneyFailureLine,
  aiOneLiner,
  timelineSentence,
  todaySummaryLine,
} from "./simpleHomeText";

describe("translateStopReason — 일상어, 코드 노출 0", () => {
  it("긴급정지는 '다시 시작' 안내까지 일상어로", () => {
    const t = translateStopReason("긴급정지");
    expect(t).toContain("긴급 정지");
    expect(t).toContain("다시 시작");
  });

  it("EMERGENCY_STOP 코드도 같은 일상어로", () => {
    expect(translateStopReason("EMERGENCY_STOP")).toContain("긴급 정지");
  });

  it("주문 한도 코드 → 일상어", () => {
    expect(translateStopReason("DAILY_ORDER_LIMIT_EXCEEDED")).toContain("한도");
  });

  it("모르는 영문 코드는 코드를 그대로 노출하지 않는다", () => {
    const t = translateStopReason("SOME_UNKNOWN_CODE");
    expect(t).not.toContain("SOME_UNKNOWN_CODE");
    expect(t).not.toMatch(/[A-Z_]{4,}/);
  });

  it("사유 없으면 안전 문구", () => {
    expect(translateStopReason(null)).toBe("잠시 멈춰 있어요");
  });
});

describe("heroFromStatus — 상태별 큰 글씨", () => {
  it("TRADING → 🟢 거래 중", () => {
    const h = heroFromStatus({ state: "TRADING" });
    expect(h.emoji).toBe("🟢");
    expect(h.big).toContain("거래 중");
  });

  it("WAITING 장 시작 전 → 곧 열려요", () => {
    const h = heroFromStatus({ state: "WAITING", reason: "장 시작 전" });
    expect(h.emoji).toBe("🟡");
    expect(h.big).toContain("곧 장이 열려요");
  });

  it("WAITING 장 마감 → 끝났어요", () => {
    const h = heroFromStatus({ state: "WAITING", reason: "장 마감" });
    expect(h.big).toContain("끝났어요");
  });

  it("BLOCKED → 🔴 + 일상어 사유", () => {
    const h = heroFromStatus({ state: "BLOCKED", reason: "긴급정지" });
    expect(h.emoji).toBe("🔴");
    expect(h.sub).toContain("긴급 정지");
  });

  it("STOPPED → ▶ 안내", () => {
    const h = heroFromStatus({ state: "STOPPED" });
    expect(h.sub).toContain("▶");
  });

  it("어떤 문구에도 영문 상태코드가 새지 않는다", () => {
    for (const st of ["TRADING", "WAITING", "STOPPED", "BLOCKED"]) {
      const h = heroFromStatus({ state: st, reason: "장 마감" });
      expect(`${h.big} ${h.sub}`).not.toMatch(/TRADING|WAITING|STOPPED|BLOCKED|cycle|tick/);
    }
  });
});

describe("moneyFailureLine (modification #1)", () => {
  it("마지막 확인 시각 포함", () => {
    expect(moneyFailureLine("14:32")).toBe(
      "잔고를 불러오지 못했어요 (마지막 확인: 14:32)",
    );
  });
  it("한 번도 못 불러왔으면 안내", () => {
    expect(moneyFailureLine(null)).toContain("아직 한 번도");
  });
});

describe("aiOneLiner", () => {
  it("BUY + confidence", () => {
    const s = aiOneLiner({ decision: "BUY", confidence: 65 });
    expect(s).toContain("사는 게");
    expect(s).toContain("65%");
  });
  it("HOLD", () => {
    expect(aiOneLiner({ decision: "HOLD", confidence: 50 })).toContain("지켜보");
  });
  it("판단 없으면 안내", () => {
    expect(aiOneLiner(null)).toContain("아직 판단 전");
  });
  it("영문 액션코드를 그대로 노출하지 않는다", () => {
    expect(aiOneLiner({ decision: "BUY", confidence: 65 })).not.toContain("BUY");
  });
});

describe("timelineSentence — 주문 한 줄을 문장으로", () => {
  const nameOf = (s) => ({ "005930": "삼성전자" }[s] || s);

  it("매수 주문 제출", () => {
    expect(timelineSentence({ symbol: "005930", side: "BUY", quantity: 2 }, nameOf))
      .toBe("삼성전자 2주 사려고 주문을 넣었어요");
  });
  it("거부", () => {
    expect(timelineSentence({ symbol: "005930", side: "BUY", decision: "REJECTED" }, nameOf))
      .toContain("받아들여지지 않았어요");
  });
  it("체결", () => {
    expect(timelineSentence({ symbol: "005930", side: "BUY", quantity: 1, filled_quantity: 1 }, nameOf))
      .toContain("샀어요");
  });
  it("승인 대기", () => {
    expect(timelineSentence({ symbol: "005930", side: "SELL", quantity: 3, decision: "NEEDS_APPROVAL" }, nameOf))
      .toContain("승인 대기");
  });
});

describe("todaySummaryLine", () => {
  it("거래 있으면 건수", () => {
    expect(todaySummaryLine({ orderCount: 15, filledCount: 0, rejectedCount: 4 }))
      .toBe("오늘 주문 15건 · 체결 0건 · 거부 4건");
  });
  it("거래 없으면 안내", () => {
    expect(todaySummaryLine({ orderCount: 0 })).toContain("아직 주문을 낸 적이");
  });
});
