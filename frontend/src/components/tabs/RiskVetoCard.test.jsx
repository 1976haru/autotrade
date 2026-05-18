/**
 * #4-09: RiskVetoCard tests.
 *
 * Invariants (test로 lock):
 * - "Risk veto 우선 — Paper 주문 후보 생성 안 됨" 배지 영구 노출.
 * - "투자 조언 아님" / "실거래 활성화 아님" / "주문 신호 아님" 배지 영구.
 * - 실거래 시작 / 지금 매수 / 지금 매도 / Place Order / ENABLE_LIVE_TRADING /
 *   ENABLE_AI_EXECUTION 라벨 button 0개.
 * - global veto / per-entry veto 노출.
 */

import { afterEach, describe, it, expect } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";

import RiskVetoCard from "./RiskVetoCard";


afterEach(cleanup);


describe("RiskVetoCard — invariants", () => {
  it("renders without report and shows fallback empty state", () => {
    render(<RiskVetoCard />);
    expect(screen.getByTestId("risk-veto-card")).toBeTruthy();
    expect(screen.getByTestId("risk-veto-priority-badge")).toBeTruthy();
    expect(screen.getByTestId("risk-veto-disclaimer-not-advice")).toBeTruthy();
    expect(screen.getByTestId("risk-veto-disclaimer-not-live")).toBeTruthy();
    expect(screen.getByTestId("risk-veto-disclaimer-not-signal")).toBeTruthy();
    expect(screen.getByTestId("risk-veto-empty")).toBeTruthy();
  });

  it("shows priority badge text 'Risk veto 우선'", () => {
    render(<RiskVetoCard />);
    const badge = screen.getByTestId("risk-veto-priority-badge");
    expect(badge.textContent).toContain("Risk veto 우선");
    expect(badge.textContent).toContain("Paper 주문 후보 생성 안 됨");
  });

  it("has zero order buttons (no Place Order / 지금 매수 / 매도 / Live)", () => {
    render(
      <RiskVetoCard
        report={{
          has_global_veto: true,
          global_veto_reasons: ["EMERGENCY_STOP"],
          global_severity: "BLOCK",
          decisions: [
            {
              strategy: "sma_crossover", symbol: "005930",
              vetoed: true,
              reasons: ["EMERGENCY_STOP"],
              reasons_label_ko: ["긴급정지 활성"],
              severity: "BLOCK",
              allow_exit_if_holding: false,
              detail_lines: [],
            },
          ],
          summary: { EMERGENCY_STOP: 1 },
          vetoed_count: 1,
          decision_count: 1,
          headline: "Risk veto 우선",
        }}
      />,
    );
    const buttons = screen.queryAllByRole("button");
    // RiskVetoCard renders 0 buttons (read-only card).
    expect(buttons.length).toBe(0);

    const inputs = screen.queryAllByRole("textbox");
    expect(inputs.length).toBe(0);
  });

  it("does not contain forbidden order labels anywhere in DOM", () => {
    render(
      <RiskVetoCard
        report={{
          has_global_veto: false,
          global_veto_reasons: [],
          global_severity: "BLOCK_NEW_ENTRY",
          decisions: [
            {
              strategy: "sma_crossover", symbol: "005930",
              vetoed: true,
              reasons: ["STALE_DATA"],
              reasons_label_ko: ["시세 stale"],
              severity: "BLOCK_NEW_ENTRY",
              allow_exit_if_holding: true,
              detail_lines: [],
            },
          ],
          summary: { STALE_DATA: 1 },
          vetoed_count: 1,
          decision_count: 1,
          headline: "Risk veto 우선 — 1개 차단",
        }}
      />,
    );
    const text = screen.getByTestId("risk-veto-card").textContent || "";
    const forbidden = [
      "지금 매수", "지금 매도", "Place Order", "실거래 시작",
      "실거래 활성화 시작", "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
      "ENABLE_FUTURES_LIVE_TRADING", "Live 시작",
    ];
    for (const f of forbidden) {
      // disclaimers explicitly invert these phrases ("실거래 활성화 아님" /
      // "투자 조언 아님") — check that no *active enable* phrase appears.
      if (f === "실거래 활성화 시작" || f === "실거래 시작" || f === "Live 시작") {
        expect(text).not.toContain(f);
      } else if (f === "지금 매수" || f === "지금 매도" || f === "Place Order") {
        expect(text).not.toContain(f);
      } else {
        // ENABLE_* may not appear at all.
        expect(text).not.toContain(f);
      }
    }
  });
});


describe("RiskVetoCard — global veto display", () => {
  const _report = {
    has_global_veto: true,
    global_veto_reasons: ["EMERGENCY_STOP", "PRE_MARKET_BLOCK"],
    global_severity: "BLOCK",
    decisions: [
      {
        strategy: "sma_crossover", symbol: "005930",
        vetoed: true,
        reasons: ["EMERGENCY_STOP", "PRE_MARKET_BLOCK"],
        reasons_label_ko: ["긴급정지", "장 시작 전 점검"],
        severity: "BLOCK",
        allow_exit_if_holding: false,
        detail_lines: [],
      },
    ],
    summary: { EMERGENCY_STOP: 1, PRE_MARKET_BLOCK: 1 },
    vetoed_count: 1,
    decision_count: 1,
    headline: "Risk veto 우선 — 긴급정지 + 장 시작 전 점검 차단",
  };

  it("shows global veto banner with 'AI 추천은 있었지만 Risk가 차단' message", () => {
    render(<RiskVetoCard report={_report} />);
    const banner = screen.getByTestId("risk-veto-global-banner");
    expect(banner.textContent).toContain("AI 추천은 있었지만 Risk가 차단했습니다");
    // global reason codes listed.
    expect(within(banner).getByTestId("risk-veto-reasons-list").textContent)
      .toContain("EMERGENCY_STOP");
  });

  it("severity badge shows BLOCK for global emergency", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.getAllByTestId("risk-veto-severity-BLOCK").length)
      .toBeGreaterThan(0);
  });

  it("decision table shows the vetoed entry", () => {
    render(<RiskVetoCard report={_report} />);
    const table = screen.getByTestId("risk-veto-decision-table");
    expect(table.textContent).toContain("sma_crossover");
    expect(table.textContent).toContain("005930");
  });

  it("summary counts each reason", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.getByTestId("risk-veto-summary-EMERGENCY_STOP").textContent)
      .toContain("× 1");
    expect(screen.getByTestId("risk-veto-summary-PRE_MARKET_BLOCK").textContent)
      .toContain("× 1");
  });
});


describe("RiskVetoCard — per-entry BLOCK_NEW_ENTRY", () => {
  const _report = {
    has_global_veto: false,
    global_veto_reasons: [],
    global_severity: "NONE",
    decisions: [
      {
        strategy: "sma_crossover", symbol: "005930",
        vetoed: true,
        reasons: ["RISK_OFFICER_REJECT"],
        reasons_label_ko: ["RiskOfficer 거절"],
        severity: "BLOCK_NEW_ENTRY",
        allow_exit_if_holding: true,
        detail_lines: ["RiskOfficer: 낮은 신뢰도"],
      },
      {
        strategy: "rsi_reversion", symbol: "035720",
        vetoed: false,
        reasons: [],
        reasons_label_ko: [],
        severity: "NONE",
        allow_exit_if_holding: true,
        detail_lines: [],
      },
    ],
    summary: { RISK_OFFICER_REJECT: 1 },
    vetoed_count: 1,
    decision_count: 2,
    headline: "Risk veto 우선 — 1개 차단",
  };

  it("shows BLOCK_NEW_ENTRY badge", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.getAllByTestId("risk-veto-severity-BLOCK_NEW_ENTRY").length)
      .toBeGreaterThan(0);
  });

  it("only renders vetoed rows in decision table", () => {
    render(<RiskVetoCard report={_report} />);
    // rsi_reversion row not rendered (not vetoed).
    expect(screen.queryByTestId("risk-veto-row-rsi_reversion-035720"))
      .toBeNull();
    // sma_crossover row rendered.
    expect(screen.getByTestId("risk-veto-row-sma_crossover-005930"))
      .toBeTruthy();
  });

  it("stats show 1 of 2 vetoed", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.getByTestId("risk-veto-stats-vetoed").textContent)
      .toContain("1");
    expect(screen.getByTestId("risk-veto-stats-vetoed").textContent)
      .toContain("2");
  });
});


describe("RiskVetoCard — no veto", () => {
  const _report = {
    has_global_veto: false,
    global_veto_reasons: [],
    global_severity: "NONE",
    decisions: [
      {
        strategy: "sma_crossover", symbol: "005930",
        vetoed: false, reasons: [], reasons_label_ko: [],
        severity: "NONE", allow_exit_if_holding: true,
        detail_lines: [],
      },
    ],
    summary: {},
    vetoed_count: 0,
    decision_count: 1,
    headline: "Risk veto 없음 — AI 추천 흐름 진행",
  };

  it("shows 'no veto' fallback in decision table", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.getByTestId("risk-veto-no-decisions")).toBeTruthy();
  });

  it("global banner is absent when no global veto", () => {
    render(<RiskVetoCard report={_report} />);
    expect(screen.queryByTestId("risk-veto-global-banner")).toBeNull();
  });
});


describe("RiskVetoCard — footer disclaimer", () => {
  it("always shows EXIT exemption note", () => {
    render(<RiskVetoCard />);
    const note = screen.getByTestId("risk-veto-footer-note");
    expect(note.textContent).toContain("EXIT");
    expect(note.textContent).toContain("위험 축소");
  });
});
