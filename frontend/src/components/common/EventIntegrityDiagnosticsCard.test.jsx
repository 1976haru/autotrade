import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

import { EventIntegrityDiagnosticsCard } from "./EventIntegrityDiagnosticsCard";

const _SAMPLE = {
  report: {
    lookback_days: 7, total_episodes: 50, checked_episodes: 50,
    integrity_score: 91.5, safe_for_analysis: true, safe_for_paper_gate: true,
    issue_counts: { CRITICAL: 0, HIGH: 1, WARN: 3, INFO: 2 },
    by_category: { LINKAGE: 1, ORDER: 2, DATA_QUALITY: 1 },
    top_issues: [
      { severity: "HIGH", category: "LINKAGE", code: "decision_log_unlinked",
        message: "주문 episode 인데 AgentDecisionLog 미연결", episode_id: "ep-1",
        suggested_action: "chain_id 연결 점검" },
      { severity: "WARN", category: "ORDER", code: "fill_price_missing",
        message: "FILLED 인데 avg_fill_price 누락", episode_id: "ep-2",
        suggested_action: "체결가 기록 점검" },
    ],
    issues: [],
    contains_secret: false, is_live_authorization: false,
  },
  summary: {
    integrity_score: 91.5, safe_for_analysis: true, safe_for_paper_gate: true,
    issue_counts: { CRITICAL: 0, HIGH: 1, WARN: 3, INFO: 2 },
    contains_secret: false, is_live_authorization: false,
  },
};

function _api(payload = _SAMPLE) {
  return { eventIntegrityDiagnostics: vi.fn(async () => payload) };
}

describe("<EventIntegrityDiagnosticsCard>", () => {
  afterEach(cleanup);

  it("정합성 점수 + 안전 플래그 표시", async () => {
    render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("ei-score")).toBeTruthy());
    expect(screen.getByTestId("ei-score").textContent).toMatch(/91\.5/);
    expect(screen.getByTestId("ei-safe-analysis").textContent).toMatch(/분석 적합: 예/);
    expect(screen.getByTestId("ei-safe-paper-gate").textContent).toMatch(/Paper Gate 적합: 예/);
  });

  it("심각도별 issue count 표시", async () => {
    render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("ei-count-HIGH")).toBeTruthy());
    expect(screen.getByTestId("ei-count-CRITICAL").textContent).toMatch(/CRITICAL 0/);
    expect(screen.getByTestId("ei-count-HIGH").textContent).toMatch(/HIGH 1/);
    expect(screen.getByTestId("ei-count-WARN").textContent).toMatch(/WARN 3/);
  });

  it("주요 이슈 TOP + category별 이슈 표시", async () => {
    render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("ei-top-issues")).toBeTruthy());
    expect(screen.getByTestId("ei-issue-decision_log_unlinked").textContent)
      .toMatch(/AgentDecisionLog 미연결/);
    expect(screen.getByTestId("ei-cat-LINKAGE").textContent).toMatch(/LINKAGE: 1건/);
  });

  it("HIGH/CRITICAL 있으면 WARN 배너 표시", async () => {
    render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("ei-warn-banner")).toBeTruthy());
    expect(screen.getByTestId("ei-warn-banner").textContent).toMatch(/신뢰도가 낮아질 수 있습니다/);
  });

  it("진단 전용/자동 주문 중단 아님/실 계좌 미사용 문구", async () => {
    render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("ei-disclaimer")).toBeTruthy());
    const d = screen.getByTestId("ei-disclaimer").textContent;
    expect(d).toMatch(/주문 신호가 아닙니다/);
    expect(d).toMatch(/자동 주문 중단은 별도 정책/);
    expect(d).toMatch(/실제 계좌정보를 사용하지 않습니다/);
  });

  it("자동매매 중단/주문 재전송/실전/매수·매도 버튼 없음 + secret 표시 없음", async () => {
    const { container } = render(<EventIntegrityDiagnosticsCard apiClient={_api()} />);
    await waitFor(() => expect(screen.getByTestId("event-integrity-card")).toBeTruthy());
    expect(container.querySelectorAll("button").length).toBe(0);
    expect(container.querySelectorAll("input").length).toBe(0);
    for (const b of ["자동매매 중단", "주문 재전송", "실전 전환", "지금 매수", "지금 매도",
                     "app_secret", "account_no", "api_key"]) {
      expect(container.textContent).not.toContain(b);
    }
  });
});
