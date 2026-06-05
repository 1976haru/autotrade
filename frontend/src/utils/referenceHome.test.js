import { describe, it, expect } from "vitest";
import {
  maskAccountNo,
  strategyChips,
  openOrderCount,
  todayOpenOrderCount,
  livePanelLine,
  marketClosedLine,
  miniKpis,
  dailyProgress,
  FEATURE_SHORTCUTS,
  resolveSymbolName,
  todayStrategyChips,
  kstDayLabel,
} from "./referenceHome";

describe("maskAccountNo — 절대원칙 #4 (평문 금지)", () => {
  it("앞4 + 끝2만 노출, 가운데 별표", () => {
    expect(maskAccountNo("5019116201")).toBe("5019****01");
  });
  it("하이픈/공백 제거 후 마스킹", () => {
    expect(maskAccountNo("50191162-01")).toBe("5019****01");
  });
  it("값 없으면 null", () => {
    expect(maskAccountNo("")).toBeNull();
    expect(maskAccountNo(null)).toBeNull();
  });
  it("너무 짧으면 null", () => {
    expect(maskAccountNo("123")).toBeNull();
  });
  it("원본 가운데 자릿수가 그대로 노출되지 않는다", () => {
    const masked = maskAccountNo("5019116201");
    expect(masked).not.toContain("1162");
  });
});

describe("strategyChips — 항상 4개, 실제 카운트만", () => {
  it("빈 리포트 → 4개 모두 '거래 시작 전'", () => {
    const chips = strategyChips(null);
    expect(chips.map((c) => c.key)).toEqual(["ORB", "MOMENTUM", "VWAP", "GAP"]);
    expect(chips.every((c) => c.verdict === "거래 시작 전")).toBe(true);
    expect(chips.every((c) => c.signals === 0)).toBe(true);
  });

  it("신호 수 = decision_count, 우세 판단 = vote 비교", () => {
    const report = {
      strategies: [
        { strategy: "ORB", decision_count: 6, buy_vote_count: 4, sell_vote_count: 1, hold_vote_count: 1 },
        { strategy: "MOMENTUM", decision_count: 3, buy_vote_count: 0, sell_vote_count: 0, hold_vote_count: 3 },
        { strategy: "GAP", decision_count: 2, buy_vote_count: 0, sell_vote_count: 2, hold_vote_count: 0 },
      ],
    };
    const chips = strategyChips(report);
    const orb = chips.find((c) => c.key === "ORB");
    expect(orb.signals).toBe(6);
    expect(orb.verdict).toBe("매수 우세");
    expect(chips.find((c) => c.key === "MOMENTUM").verdict).toBe("관망");
    expect(chips.find((c) => c.key === "GAP").verdict).toBe("매도 우세");
    // 응답에 없는 VWAP은 거래 시작 전
    expect(chips.find((c) => c.key === "VWAP").verdict).toBe("거래 시작 전");
  });

  it("한글 라벨 매핑", () => {
    const labels = strategyChips(null).map((c) => c.label);
    expect(labels).toEqual(["ORB", "모멘텀", "VWAP", "갭"]);
  });
});

describe("openOrderCount — 미체결(실체결 기준)", () => {
  it("체결/거부 제외, 나머지만 카운트", () => {
    const orders = [
      { broker_status: "FILLED", filled_quantity: 2 },   // 체결 — 제외
      { broker_status: "REJECTED" },                      // 거부 — 제외
      { decision: "REJECTED" },                           // 거부 — 제외
      { broker_status: "RECEIVED", filled_quantity: 0 },  // 미체결 ✓
      { decision: "NEEDS_APPROVAL", filled_quantity: 0 }, // 미체결 ✓
    ];
    expect(openOrderCount(orders)).toBe(2);
  });
  it("빈 배열 → 0", () => {
    expect(openOrderCount([])).toBe(0);
    expect(openOrderCount(null)).toBe(0);
  });
});

describe("todayOpenOrderCount — D2 (오늘 기준 미체결)", () => {
  it("오늘 주문 − 체결 − 거부 (06-05: 15−14−0 = 1)", () => {
    expect(todayOpenOrderCount({ orderCount: 15, filledCount: 14, rejectedCount: 0 })).toBe(1);
  });
  it("음수 방지 + 누락 필드 0 처리", () => {
    expect(todayOpenOrderCount({ orderCount: 2, filledCount: 5, rejectedCount: 0 })).toBe(0);
    expect(todayOpenOrderCount({ orderCount: 3 })).toBe(3);
    expect(todayOpenOrderCount(null)).toBe(0);
    expect(todayOpenOrderCount(undefined)).toBe(0);
  });
});

describe("livePanelLine — 실시간 현황판 일상어", () => {
  const nameOf = (s) => ({ "005930": "삼성전자", "035420": "NAVER" }[s] || s);

  it("기법+매수 신호 → 샀어요 (시각은 UTC→KST 변환)", () => {
    // backend 는 naive UTC(00:32)를 emit — 화면엔 KST(+9=09:32)로 보여야 한다.
    const r = livePanelLine(
      { timestamp: "2026-06-04T00:32:10", symbol: "005930", strategy: "MOMENTUM", decision_action: "BUY", paper_order_id: "0000012345", paper_fill_status: "PAPER_FILLED" },
      nameOf,
    );
    expect(r.time).toBe("09:32");   // UTC 00:32 → KST 09:32
    expect(r.text).toContain("삼성전자");
    expect(r.text).toContain("모멘텀이 매수 신호");
    expect(r.text).toContain("샀어요");
  });

  it("★결정만 나고 주문 미전송(품질/한도 게이트) → '주문은 안 나갔어요'", () => {
    // decision_action=SELL 이지만 paper_order_id 없음 + 미체결 → '주문 보냈어요' 금지.
    const r = livePanelLine(
      { timestamp: "2026-06-05T03:25:00", symbol: "005930", strategy: "MOMENTUM", decision_action: "SELL", paper_order_id: null, paper_fill_status: null },
      nameOf,
    );
    expect(r.text).toContain("매도 신호");
    expect(r.text).toContain("주문은 안 나갔어요");
    expect(r.text).not.toContain("주문 보냈어요");
    expect(r.text).not.toContain("팔았어요");
  });

  it("보류 → 신호 약해서 보류", () => {
    const r = livePanelLine({ symbol: "035420", decision_action: "HOLD" }, nameOf);
    expect(r.text).toContain("NAVER");
    expect(r.text).toContain("보류했어요");
  });

  it("리스크 차단 → 막았어요", () => {
    const r = livePanelLine({ symbol: "005930", decision_action: "BUY", risk_veto: true }, nameOf);
    expect(r.text).toContain("리스크");
    expect(r.text).toContain("막았어요");
  });

  it("영문 코드가 새지 않는다", () => {
    const r = livePanelLine({ symbol: "005930", decision_action: "BUY", strategy: "VWAP", paper_fill_status: "PAPER_FILLED" }, nameOf);
    expect(r.text).not.toMatch(/BUY|SELL|HOLD|PAPER_|NO_OP/);
  });

  it("마감 안내 문구", () => {
    expect(marketClosedLine()).toContain("장이 닫혀 있어요");
  });
});

describe("miniKpis — 실체결 기준, 추정 금지", () => {
  it("cashState 없으면 실현손익/승률 모두 거래 시작 전", () => {
    const k = miniKpis({ cashState: null, today: { orderCount: 0, filledCount: 0 } });
    expect(k.realizedText).toBe("거래 시작 전");
    expect(k.winRateText).toBe("거래 시작 전");
    expect(k.fillRateText).toBe("거래 시작 전");
  });
  it("실현손익 음수 표시, 체결률 계산", () => {
    const k = miniKpis({ cashState: { realized_pnl_krw: -624164 }, today: { orderCount: 10, filledCount: 3 } });
    expect(k.realizedText).toContain("-624,164원");
    expect(k.fillRateText).toBe("30%");
    // ★체결이 있으면(filledCount>0) 승률은 '거래 시작 전'이 아니라 '청산 거래 없음'.
    expect(k.winRateText).toBe("청산 거래 없음");
  });

  it("★체결은 있으나 cashState 없음 → 실현손익 0원(거래 시작 전 아님), 승률 청산 거래 없음", () => {
    const k = miniKpis({ cashState: null, today: { orderCount: 15, filledCount: 14 } });
    expect(k.realizedText).toBe("0원");
    expect(k.winRateText).toBe("청산 거래 없음");
    expect(k.fillRateText).toBe("93%");
  });
});

describe("dailyProgress — 매수 사용금액 게이지", () => {
  it("BUY notional 합산, 거부 제외, % 계산", () => {
    const orders = [
      { side: "BUY", quantity: 2, price: 500000, broker_status: "FILLED", filled_quantity: 2 },
      { side: "BUY", quantity: 1, price: 200000, decision: "REJECTED" }, // 제외
      { side: "SELL", quantity: 1, price: 999999 },                       // 매도 제외
    ];
    const p = dailyProgress({ orders, today: { orderCount: 3 }, buyMaxKrw: 3_000_000 });
    expect(p.buyUsedKrw).toBe(1_000_000);
    expect(p.buyMaxKrw).toBe(3_000_000);
    expect(p.buyPct).toBe(33);
  });
  it("한도 0이면 기본값으로 보정", () => {
    expect(dailyProgress({ orders: [], today: {}, buyMaxKrw: 0 }).buyMaxKrw).toBe(3_000_000);
  });
});

describe("resolveSymbolName — 종목코드 노출 방지", () => {
  it("코드 → 한글명 (373220/068270)", () => {
    expect(resolveSymbolName("373220")).toBe("LG에너지솔루션");
    expect(resolveSymbolName("068270")).toBe("셀트리온");
    expect(resolveSymbolName("005930")).toBe("삼성전자");
  });
  it("모르는 코드는 코드 그대로(최후 fallback)", () => {
    expect(resolveSymbolName("999999")).toBe("999999");
  });
  it("런타임 extra 맵 우선", () => {
    expect(resolveSymbolName("999999", { "999999": "테스트종목" })).toBe("테스트종목");
  });
});

describe("livePanelLine — 기본 한글명 변환", () => {
  it("nameOf 없이도 코드가 한글명으로", () => {
    const r = livePanelLine({ symbol: "373220", decision_action: "BUY", strategy: "MOMENTUM", paper_fill_status: "PAPER_FILLED" });
    expect(r.text).toContain("LG에너지솔루션");
    expect(r.text).not.toContain("373220");
  });
});

describe("kstDayLabel", () => {
  const now = new Date("2026-06-04T01:00:00Z"); // KST 06-04 10:00
  it("오늘 → 빈 문자열", () => {
    expect(kstDayLabel("2026-06-04T00:32:00", now)).toBe("");
  });
  it("어제 → '어제'", () => {
    expect(kstDayLabel("2026-06-03T05:00:00", now)).toBe("어제");
  });
  it("그 외 → MM/DD", () => {
    expect(kstDayLabel("2026-06-01T05:00:00", now)).toBe("06/01");
  });
});

describe("todayStrategyChips — 오늘 신호 수 + 최근 판단", () => {
  const now = new Date("2026-06-04T01:00:00Z"); // KST 06-04
  it("오늘 entries만 카운트, 최근 판단 표시", () => {
    const entries = [
      { timestamp: "2026-06-04T00:50:00", strategy: "MOMENTUM", decision_action: "BUY" },   // 오늘, 최근
      { timestamp: "2026-06-04T00:20:00", strategy: "MOMENTUM", decision_action: "HOLD" },   // 오늘
      { timestamp: "2026-06-03T05:00:00", strategy: "MOMENTUM", decision_action: "SELL" },   // 어제 — 제외
      { timestamp: "2026-06-04T00:10:00", strategy: "ORB", decision_action: "HOLD" },        // 오늘
    ];
    const chips = todayStrategyChips(entries, now);
    const mom = chips.find((c) => c.key === "MOMENTUM");
    expect(mom.signals).toBe(2);           // 어제 제외
    expect(mom.verdict).toBe("매수");      // 가장 최근(첫 entry)
    expect(chips.find((c) => c.key === "ORB").signals).toBe(1);
    expect(chips.find((c) => c.key === "VWAP").signals).toBe(0);
    expect(chips.find((c) => c.key === "VWAP").verdict).toBe("오늘 신호 없음");
  });
  it("항상 4개 (ORB/모멘텀/VWAP/갭)", () => {
    expect(todayStrategyChips([], now).map((c) => c.key)).toEqual(["ORB", "MOMENTUM", "VWAP", "GAP"]);
  });
});

describe("FEATURE_SHORTCUTS", () => {
  it("기존 탭 6개로 이동", () => {
    expect(FEATURE_SHORTCUTS.map((f) => f.tab)).toEqual(
      ["approve", "strat", "chart", "backtest", "audit", "engine"],
    );
  });
});
