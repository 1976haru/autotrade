import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RiskAuditorCard, useRiskAuditorReport } from "./RiskAuditorCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { riskAuditorReport: vi.fn() },
}));


const _GREEN_REPORT = {
  audit_level: "GREEN",
  risk_score: 0,
  events: [],
  pause_trading_recommended: false,
  emergency_stop_recommended: false,
  recommended_stop_reason: null,
  summary_lines: [
    "리스크 감사: 이상 없음.",
    "감사 row 12건, 이벤트 0건.",
    "본 리포트는 *주문 신호가 아닙니다*. 안전 advisory 전용.",
  ],
  total_audit_rows_inspected: 12,
  total_emergency_events_inspected: 0,
  total_agent_decisions_inspected: 5,
  is_order_signal: false,
  created_at: "2026-05-09T12:00:00+00:00",
};

const _RED_REPORT = {
  audit_level: "RED",
  risk_score: 88,
  events: [
    {
      type: "daily_loss_breach",
      severity: "CRITICAL",
      summary: "일일 손실 한도 100% 초과 (-1,200,000원 / -1,000,000원)",
      recommended_action: "EMERGENCY_STOP_RECOMMENDED",
      detail: { realized: -1200000, max_loss: -1000000 },
    },
    {
      type: "duplicate_order_burst",
      severity: "HIGH",
      summary: "중복 주문 8건 감지 (window 3600s)",
      recommended_action: "PAUSE_TRADING_RECOMMENDED",
      detail: { count: 8 },
    },
  ],
  pause_trading_recommended: true,
  emergency_stop_recommended: true,
  recommended_stop_reason: "MANUAL_OPERATOR",
  summary_lines: [
    "리스크 감사: 긴급. score=88.",
    "주요: 일일 손실 한도 초과, 중복 주문 다수.",
    "EMERGENCY_STOP 권고 — 운영자 확인 필요.",
  ],
  total_audit_rows_inspected: 56,
  total_emergency_events_inspected: 1,
  total_agent_decisions_inspected: 9,
  is_order_signal: false,
  created_at: "2026-05-09T12:30:00+00:00",
};


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.riskAuditorReport.mockResolvedValue(_GREEN_REPORT);
});


describe("<RiskAuditorCard>", () => {
  it("renders '주문 신호 아님' badge prominently", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_GREEN_REPORT} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-not-order-badge").textContent)
      .toMatch(/주문 신호 아님|안전 리포트/);
  });

  it("renders summary lines", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_GREEN_REPORT} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-line-0").textContent)
      .toMatch(/리스크 감사/);
    expect(getByTestId("risk-auditor-line-2").textContent)
      .toMatch(/주문 신호가 아닙니다/);
  });

  it("renders GREEN level with green color and 정상 label", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_GREEN_REPORT} loading={false} error="" />,
    );
    const level = getByTestId("risk-auditor-level");
    expect(level.textContent).toContain("정상");
    expect(level.textContent).toContain("GREEN");
    // green = #22c55e
    expect(level.getAttribute("style") || "").toMatch(/22c55e|34, 197, 94/i);
  });

  it("renders RED level with red color and 긴급 label", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    const level = getByTestId("risk-auditor-level");
    expect(level.textContent).toContain("긴급");
    expect(level.textContent).toContain("RED");
    expect(level.getAttribute("style") || "").toMatch(/ef4444|239, 68, 68/i);
  });

  it("renders YELLOW and ORANGE levels distinctly", () => {
    const yellow = { ..._GREEN_REPORT, audit_level: "YELLOW", risk_score: 12 };
    const orange = { ..._GREEN_REPORT, audit_level: "ORANGE", risk_score: 45 };

    const { getByTestId, unmount } = render(
      <RiskAuditorCard report={yellow} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-level").textContent).toContain("경고");
    unmount();

    const r = render(
      <RiskAuditorCard report={orange} loading={false} error="" />,
    );
    expect(r.getByTestId("risk-auditor-level").textContent).toContain("주의");
  });

  it("renders risk score", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-score").textContent).toContain("88");
    expect(getByTestId("risk-auditor-score").textContent).toContain("100");
  });

  it("renders events with type, severity and summary", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    const events = getByTestId("risk-auditor-events");
    expect(events.textContent).toContain("daily_loss_breach");
    expect(events.textContent).toContain("CRITICAL");
    expect(events.textContent).toContain("일일 손실 한도");
    expect(events.textContent).toContain("duplicate_order_burst");
    expect(events.textContent).toContain("HIGH");
  });

  it("renders EMERGENCY_STOP_RECOMMENDED warning when set", () => {
    const { getByTestId, queryByTestId } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    const stop = getByTestId("risk-auditor-stop-recommendation");
    expect(stop.textContent).toContain("EMERGENCY_STOP_RECOMMENDED");
    expect(stop.textContent).toMatch(/Kill Switch|운영자.*수동/);
    // PAUSE banner는 STOP이 있을 때 중복 표시 안함.
    expect(queryByTestId("risk-auditor-pause-recommendation")).toBeNull();
  });

  it("renders PAUSE_TRADING_RECOMMENDED when STOP is False", () => {
    const pauseOnly = {
      ..._RED_REPORT,
      audit_level: "ORANGE",
      emergency_stop_recommended: false,
      recommended_stop_reason: null,
      pause_trading_recommended: true,
    };
    const { getByTestId, queryByTestId } = render(
      <RiskAuditorCard report={pauseOnly} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-pause-recommendation").textContent)
      .toContain("PAUSE_TRADING_RECOMMENDED");
    expect(queryByTestId("risk-auditor-stop-recommendation")).toBeNull();
  });

  it("does NOT render BUY/SELL/HOLD or 긴급정지 toggle buttons", () => {
    const { container } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      expect(text).not.toMatch(/BUY|SELL|HOLD|매수|매도/);
      // 본 카드는 emergency_stop을 *직접 토글하지 않는다*.
      expect(text).not.toMatch(/긴급정지 (실행|토글|시작|중지)/);
      expect(text).not.toMatch(/Emergency.{0,4}Stop.{0,4}(toggle|on|off)/i);
    }
  });

  it("renders disclaimer that this is NOT an order signal", () => {
    const { container } = render(
      <RiskAuditorCard report={_GREEN_REPORT} loading={false} error="" />,
    );
    expect(container.textContent).toMatch(/주문 신호가 아닙니다/);
  });

  it("renders recommended_stop_reason when EMERGENCY_STOP recommended", () => {
    const { getByTestId } = render(
      <RiskAuditorCard report={_RED_REPORT} loading={false} error="" />,
    );
    expect(getByTestId("risk-auditor-stop-recommendation").textContent)
      .toContain("MANUAL_OPERATOR");
  });

  it("shows loading state without report", () => {
    const { getByText } = render(
      <RiskAuditorCard report={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows friendly fallback on error", () => {
    const { getByTestId, container } = render(
      <RiskAuditorCard report={null} loading={false} error="boom" />,
    );
    const err = getByTestId("risk-auditor-error");
    expect(err.textContent).toMatch(/리스크 감사 데이터/);
    // raw "boom" 메시지를 그대로 보여주지 않음.
    expect(container.textContent).not.toContain("boom");
  });

  it("renders nothing when report is null and no loading/error", () => {
    const { container } = render(
      <RiskAuditorCard report={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getAllByText } = render(
      <RiskAuditorCard report={_GREEN_REPORT} loading={false} error=""
                         onRefresh={onRefresh} />,
    );
    fireEvent.click(getAllByText(/새로고침/)[0]);
    expect(onRefresh).toHaveBeenCalled();
  });
});


describe("useRiskAuditorReport integration", () => {
  it("calls API on mount and renders into card", async () => {
    function Probe() {
      const r = useRiskAuditorReport();
      return <RiskAuditorCard
        report={r.report} loading={r.loading} error={r.error}
        onRefresh={r.refresh}
      />;
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.riskAuditorReport).toHaveBeenCalled());
    const level = await findByTestId("risk-auditor-level");
    expect(level.textContent).toContain("정상");
  });
});
