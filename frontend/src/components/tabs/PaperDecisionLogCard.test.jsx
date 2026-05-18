/**
 * #4-10: PaperDecisionLogCard tests.
 *
 * Invariants (test로 lock):
 * - Paper 전용 / 실거래 아님 / 투자 조언 아님 / 주문 신호 아님 배지 영구.
 * - 실거래 시작 / 지금 매수 / 지금 매도 / Place Order / ENABLE_LIVE_TRADING /
 *   ENABLE_AI_EXECUTION 라벨 button 0개.
 * - BUY/SELL/EXIT 은 *label* 로만 표시 — 버튼 0개.
 * - secret 입력 form (input/textarea) 0개.
 * - 항목별 reason / risk_flags / risk_veto / confidence / position_size 표시.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import PaperDecisionLogCard from "./PaperDecisionLogCard";


afterEach(cleanup);


const _SAMPLE = [
  {
    decision_id:        "abc-1",
    timestamp:          "2026-05-18T01:00:00+00:00",
    agent_name:         "PaperDecisionBridge",
    strategy:           "sma_crossover",
    symbol:             "005930",
    mode:               "PAPER",
    decision_action:    "BUY",
    confidence:         75,
    reason:             "[추천] golden cross + regime TREND_UP",
    risk_flags:         [],
    market_regime:      "TREND_UP",
    overfit_flag:       false,
    risk_veto:          false,
    risk_veto_reasons:  [],
    risk_veto_severity: null,
    position_size:      5,
    sizing_verdict:     "SIZED",
    paper_order_id:     "PAPER-1",
    paper_fill_status:  "PAPER_FILLED",
    chain_id:           "chain-1",
    source_module:      "paper_decision_bridge",
    is_order_signal:       false,
    auto_apply_allowed:    false,
    is_live_authorization: false,
  },
  {
    decision_id:        "abc-2",
    timestamp:          "2026-05-18T01:01:00+00:00",
    agent_name:         "PaperDecisionBridge",
    strategy:           "rsi_reversion",
    symbol:             "035720",
    mode:               "PAPER",
    decision_action:    "HOLD",
    confidence:         null,
    reason:             "[추천] BUT stale_data risk veto",
    risk_flags:         ["stale_data"],
    market_regime:      "SIDEWAYS",
    overfit_flag:       false,
    risk_veto:          true,
    risk_veto_reasons:  ["STALE_DATA"],
    risk_veto_severity: "BLOCK_NEW_ENTRY",
    position_size:      0,
    sizing_verdict:     null,
    paper_order_id:     null,
    paper_fill_status:  "NA",
    chain_id:           "chain-1",
    source_module:      "paper_decision_bridge",
    is_order_signal:       false,
    auto_apply_allowed:    false,
    is_live_authorization: false,
  },
];

const _SUMMARY = {
  by_action: { BUY: 1, HOLD: 1 },
  veto_count: 1,
  sizing_reduced: 0,
};


describe("PaperDecisionLogCard — invariants", () => {
  it("renders permanent badges", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getByTestId("decision-log-paper-only-badge")).toBeTruthy();
    expect(screen.getByTestId("decision-log-disclaimer-not-advice")).toBeTruthy();
    expect(screen.getByTestId("decision-log-disclaimer-not-signal")).toBeTruthy();
    expect(screen.getByTestId("decision-log-disclaimer-not-live")).toBeTruthy();
  });

  it("paper-only badge text contains '실거래 아님'", () => {
    render(<PaperDecisionLogCard entries={[]} summary={{}} />);
    const b = screen.getByTestId("decision-log-paper-only-badge");
    expect(b.textContent).toContain("Paper 전용");
    expect(b.textContent).toContain("실거래 아님");
  });

  it("renders zero buttons and zero text inputs", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const buttons = screen.queryAllByRole("button");
    expect(buttons.length).toBe(0);
    const inputs = screen.queryAllByRole("textbox");
    expect(inputs.length).toBe(0);
  });

  it("does not contain forbidden order labels in DOM", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const card = screen.getByTestId("paper-decision-log-card");
    const text = card.textContent || "";
    const forbidden = [
      "지금 매수", "지금 매도", "Place Order",
      "실거래 시작", "실거래 활성화 시작",
      "ENABLE_LIVE_TRADING", "ENABLE_AI_EXECUTION",
      "ENABLE_FUTURES_LIVE_TRADING",
    ];
    for (const f of forbidden) {
      expect(text).not.toContain(f);
    }
  });
});


describe("PaperDecisionLogCard — entry rendering", () => {
  it("renders one row per entry", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getByTestId("decision-log-entry-abc-1")).toBeTruthy();
    expect(screen.getByTestId("decision-log-entry-abc-2")).toBeTruthy();
  });

  it("shows BUY action label only on the BUY entry", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getAllByTestId("decision-log-action-BUY").length).toBe(1);
    expect(screen.getAllByTestId("decision-log-action-HOLD").length).toBe(1);
  });

  it("BUY label says '매수 (로그)' — clarifies non-button", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const label = screen.getByTestId("decision-log-action-BUY");
    expect(label.textContent).toContain("매수");
    expect(label.textContent).toContain("로그");
    // 'BUY' label is a <span>, not a <button>.
    expect(label.tagName.toLowerCase()).toBe("span");
  });

  it("shows confidence when present", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getByTestId("decision-log-confidence-abc-1").textContent)
      .toContain("75");
  });

  it("shows position_size for sized BUY", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getByTestId("decision-log-position-abc-1").textContent)
      .toContain("5");
  });

  it("shows risk veto chip when risk_veto=true", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const chip = screen.getByTestId("decision-log-veto-abc-2");
    expect(chip.textContent).toContain("Risk veto");
    expect(chip.textContent).toContain("STALE_DATA");
  });

  it("shows reason text on each row", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const row1 = screen.getByTestId("decision-log-entry-abc-1");
    expect(row1.textContent).toContain("golden cross");
    const row2 = screen.getByTestId("decision-log-entry-abc-2");
    expect(row2.textContent).toContain("stale_data risk veto");
  });
});


describe("PaperDecisionLogCard — summary strip", () => {
  it("shows total and by-action counts", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    const total = screen.getByTestId("decision-log-total");
    expect(total.textContent).toContain("2");
    expect(screen.getByTestId("decision-log-by-action-BUY").textContent)
      .toContain("1");
    expect(screen.getByTestId("decision-log-by-action-HOLD").textContent)
      .toContain("1");
  });

  it("shows veto count when > 0", () => {
    render(<PaperDecisionLogCard entries={_SAMPLE} summary={_SUMMARY} />);
    expect(screen.getByTestId("decision-log-veto-count").textContent)
      .toContain("1");
  });
});


describe("PaperDecisionLogCard — empty / unloaded states", () => {
  it("shows uninit message when entries=null and autoload=false", () => {
    render(<PaperDecisionLogCard />);
    expect(screen.getByTestId("decision-log-empty-uninit")).toBeTruthy();
  });

  it("shows empty message when entries=[] and summary present", () => {
    render(<PaperDecisionLogCard entries={[]} summary={{ by_action: {}, veto_count: 0 }} />);
    expect(screen.getByTestId("decision-log-empty")).toBeTruthy();
  });
});


describe("PaperDecisionLogCard — autoload via injected API client", () => {
  it("fetches and renders entries when autoload=true", async () => {
    const fakeClient = {
      get: vi.fn(async () => ({
        entries: _SAMPLE,
        summary: _SUMMARY,
      })),
    };
    render(
      <PaperDecisionLogCard autoload={true} apiClient={fakeClient} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("decision-log-entry-abc-1")).toBeTruthy();
    });
    expect(fakeClient.get).toHaveBeenCalledWith(
      "/api/auto-paper/decision-log?limit=20",
    );
  });

  it("renders error banner when API throws", async () => {
    const fakeClient = {
      get: vi.fn(async () => { throw new Error("network_down"); }),
    };
    render(
      <PaperDecisionLogCard autoload={true} apiClient={fakeClient} />,
    );
    await waitFor(() => {
      expect(screen.getByTestId("decision-log-error")).toBeTruthy();
    });
    expect(screen.getByTestId("decision-log-error").textContent)
      .toContain("network_down");
  });
});


describe("PaperDecisionLogCard — footer note", () => {
  it("always shows mode=PAPER footer note with no order buttons", () => {
    render(<PaperDecisionLogCard entries={[]} summary={{}} />);
    const note = screen.getByTestId("decision-log-footer-note");
    expect(note.textContent).toContain("PAPER");
    expect(note.textContent).toContain("실거래 주문이 아니며");
    expect(note.textContent).toContain("0건");
  });
});
