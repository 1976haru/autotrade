import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  StrategyResearcherCard,
  useStrategyResearcherRecent,
  useStrategyResearcherReport,
} from "./StrategyResearcherCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    strategyResearcherReport: vi.fn(),
    strategyResearcherRecent: vi.fn(),
  },
}));


const _HEALTHY_REPORT = {
  audit_level: "HEALTHY",
  findings: [],
  suggestions: [],
  required_next_tests: ["현재 결과 정상 — 정기 재검증 권고"],
  markdown_report: "# Strategy Research Report — sma_cross\n\n자동 반영 안 됨 / PR 검토 필요",
  summary_lines: [
    "전략 분석: sma_cross (run_id=1) — 단계 HEALTHY.",
    "트레이드 200건, PF 1.40, MDD 1,000,000원 — findings 0건, 제안 0건.",
    "주요 임계 통과 — 정기 walk-forward / Monte Carlo 재검증 권고.",
    "본 리포트는 *advisory*입니다. 자동 반영 안 됨.",
  ],
  strategy: "sma_cross",
  run_id: 1,
  auto_apply_allowed: false,
  is_order_signal: false,
  created_at: "2026-05-09T12:00:00+00:00",
};


const _CRITICAL_REPORT = {
  audit_level: "CRITICAL",
  findings: [
    {
      code: "low_profit_factor",
      severity: "CRITICAL",
      summary: "Profit Factor 0.85 — 1.0 미만(손실 우세).",
      metric_name: "profit_factor",
      metric_value: 0.85,
      threshold: 1.0,
      detail: {},
    },
    {
      code: "monte_carlo_ruin_high",
      severity: "CRITICAL",
      summary: "Risk of Ruin 15.0% — 임계(10%) 초과.",
      metric_name: "risk_of_ruin",
      metric_value: 0.15,
      threshold: 0.10,
      detail: {},
    },
  ],
  suggestions: [
    {
      category: "parameter_tune",
      severity: "CRITICAL",
      title: "진입 / 청산 임계 재검토",
      rationale: "PF 0.85 < 1.0",
      proposed_change: "손절 비율 축소 변종 백테스트.",
      required_validation: ["새 파라미터 백테스트", "walk-forward 재검증"],
      references: ["docs/strategy_researcher_agent.md §6"],
    },
    {
      category: "shrink_size",
      severity: "CRITICAL",
      title: "포지션 사이즈 축소",
      rationale: "ROR 15% 너무 높음",
      proposed_change: "quantity 50% 축소 변종 백테스트.",
      required_validation: ["축소된 사이즈 백테스트", "Monte Carlo ROR 재측정"],
      references: [],
    },
  ],
  required_next_tests: [
    "새 파라미터 백테스트", "walk-forward 재검증",
    "축소된 사이즈 백테스트", "Monte Carlo ROR 재측정",
    "운영자 검토 / 별도 PR",
  ],
  markdown_report: "# Strategy Research Report — bad_strategy\n\nFAIL details...",
  summary_lines: [
    "전략 분석: bad_strategy — 단계 CRITICAL.",
    "심각 — 운영자 수동 결정 + 별도 PR + 백테스트 재실행 필수.",
    "본 리포트는 *advisory*입니다. 자동 반영 안 됨.",
  ],
  strategy: "bad_strategy",
  run_id: 42,
  auto_apply_allowed: false,
  is_order_signal: false,
  created_at: "2026-05-09T12:30:00+00:00",
};


afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.strategyResearcherReport.mockResolvedValue(_HEALTHY_REPORT);
  backendApi.strategyResearcherRecent.mockResolvedValue({ items: [] });
});


describe("<StrategyResearcherCard>", () => {
  it("renders '자동 반영 안 됨 / PR 검토 필요' badge prominently", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    expect(getByTestId("strategy-researcher-not-auto-apply-badge").textContent)
      .toMatch(/자동 반영 안 됨|PR 검토 필요/);
  });

  it("renders summary lines", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    expect(getByTestId("strategy-researcher-line-0").textContent)
      .toMatch(/sma_cross|전략 분석/);
  });

  it("renders strategy name and run_id", () => {
    const { getByTestId, container } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    expect(getByTestId("strategy-researcher-strategy").textContent)
      .toContain("sma_cross");
    expect(container.textContent).toContain("#1");
  });

  it("renders HEALTHY level with green color", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    const level = getByTestId("strategy-researcher-level");
    expect(level.textContent).toContain("정상");
    expect(level.textContent).toContain("HEALTHY");
    expect(level.getAttribute("style") || "").toMatch(/22c55e|34, 197, 94/i);
  });

  it("renders CRITICAL level with red color", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    const level = getByTestId("strategy-researcher-level");
    expect(level.textContent).toContain("심각");
    expect(level.textContent).toContain("CRITICAL");
    expect(level.getAttribute("style") || "").toMatch(/ef4444|239, 68, 68/i);
  });

  it("renders findings with code, severity, summary", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    const findings = getByTestId("strategy-researcher-findings");
    expect(findings.textContent).toContain("low_profit_factor");
    expect(findings.textContent).toContain("CRITICAL");
    expect(findings.textContent).toContain("Profit Factor");
    expect(findings.textContent).toContain("monte_carlo_ruin_high");
  });

  it("renders suggestions with rationale, proposed change, required validation", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    const suggestions = getByTestId("strategy-researcher-suggestions");
    expect(suggestions.textContent).toContain("parameter_tune");
    expect(suggestions.textContent).toContain("진입 / 청산 임계 재검토");
    expect(suggestions.textContent).toContain("Why:");
    expect(suggestions.textContent).toContain("제안:");
    expect(suggestions.textContent).toContain("새 파라미터 백테스트");
    expect(suggestions.textContent).toContain("walk-forward 재검증");
  });

  it("renders required_next_tests prominently", () => {
    const { getByTestId } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    const tests = getByTestId("strategy-researcher-next-tests");
    expect(tests.textContent).toContain("운영자 검토 / 별도 PR");
    expect(tests.textContent).toContain("백테스트");
  });

  it("does NOT render BUY/SELL/HOLD or auto-apply / parameter-save buttons", () => {
    const { container } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = btn.textContent || "";
      // 주문 신호 buttons 금지
      expect(text).not.toMatch(/BUY|SELL|HOLD|매수|매도/);
      // 자동 반영 buttons 금지 — 이게 본 카드의 핵심 invariant.
      expect(text).not.toMatch(/자동 반영|자동 적용|파라미터 저장|코드 수정|코드 적용|지금 적용|바로 적용/);
      expect(text).not.toMatch(/Apply (parameter|change|config)/i);
    }
  });

  it("renders disclaimer that suggestions are NOT auto-applied", () => {
    const { container } = render(
      <StrategyResearcherCard report={_CRITICAL_REPORT}
                                loading={false} error="" />,
    );
    expect(container.textContent).toMatch(/자동으로 코드/);
    expect(container.textContent).toMatch(/반영되지 않/);
    expect(container.textContent).toMatch(/별도 PR/);
  });

  it("markdown preview toggle reveals markdown report", () => {
    const { getByText, queryByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    expect(queryByTestId("strategy-researcher-markdown")).toBeNull();
    fireEvent.click(getByText(/markdown 미리보기/));
    const md = queryByTestId("strategy-researcher-markdown");
    expect(md).not.toBeNull();
    expect(md.textContent).toContain("Strategy Research Report");
    expect(md.textContent).toContain("자동 반영 안 됨");
  });

  it("renders Backtest 다시 실행 button when onRerunBacktest provided", () => {
    const onRerun = vi.fn();
    const { getByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error=""
                                onRerunBacktest={onRerun} />,
    );
    const btn = getByTestId("strategy-researcher-rerun-backtest");
    expect(btn.textContent).toMatch(/Backtest 다시 실행/);
    fireEvent.click(btn);
    expect(onRerun).toHaveBeenCalled();
  });

  it("does not render Backtest button if callback omitted", () => {
    const { queryByTestId } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error="" />,
    );
    expect(queryByTestId("strategy-researcher-rerun-backtest")).toBeNull();
  });

  it("shows loading state without report", () => {
    const { getByText } = render(
      <StrategyResearcherCard report={null} loading={true} error="" />,
    );
    expect(getByText(/로딩 중/)).toBeTruthy();
  });

  it("shows friendly fallback on error", () => {
    const { getByTestId, container } = render(
      <StrategyResearcherCard report={null} loading={false} error="boom" />,
    );
    const err = getByTestId("strategy-researcher-error");
    expect(err.textContent).toMatch(/전략 연구 데이터/);
    expect(container.textContent).not.toContain("boom");
  });

  it("renders nothing when report is null and no loading/error", () => {
    const { container } = render(
      <StrategyResearcherCard report={null} loading={false} error="" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("refresh button triggers onRefresh", () => {
    const onRefresh = vi.fn();
    const { getAllByText } = render(
      <StrategyResearcherCard report={_HEALTHY_REPORT}
                                loading={false} error=""
                                onRefresh={onRefresh} />,
    );
    fireEvent.click(getAllByText(/새로고침/)[0]);
    expect(onRefresh).toHaveBeenCalled();
  });
});


describe("useStrategyResearcherReport integration", () => {
  it("calls API when runId is provided and renders into card", async () => {
    function Probe({ runId }) {
      const r = useStrategyResearcherReport(runId);
      return <StrategyResearcherCard
        report={r.report} loading={r.loading} error={r.error}
        onRefresh={r.refresh}
      />;
    }
    const { findByTestId } = render(<Probe runId={1} />);
    await waitFor(() => expect(backendApi.strategyResearcherReport)
                    .toHaveBeenCalledWith(1));
    const level = await findByTestId("strategy-researcher-level");
    expect(level.textContent).toContain("정상");
  });

  it("does NOT call API when runId is null", async () => {
    function Probe() {
      const r = useStrategyResearcherReport(null);
      return <div data-testid="probe-empty">empty: {String(r.report == null)}</div>;
    }
    render(<Probe />);
    // 잠깐 기다려도 호출되지 않아야 한다.
    await new Promise(r => setTimeout(r, 30));
    expect(backendApi.strategyResearcherReport).not.toHaveBeenCalled();
  });
});


describe("useStrategyResearcherRecent integration", () => {
  it("calls API on mount and returns items", async () => {
    backendApi.strategyResearcherRecent.mockResolvedValue({
      items: [
        { run_id: 1, strategy: "sma", created_at: "2026-05-09",
          audit_level: "HEALTHY", findings_count: 0,
          suggestions_count: 0, summary_line: "ok" },
      ],
    });
    function Probe() {
      const r = useStrategyResearcherRecent({ limit: 5 });
      return <div data-testid="probe-items">{r.items.length}</div>;
    }
    const { findByTestId } = render(<Probe />);
    await waitFor(() => expect(backendApi.strategyResearcherRecent)
                    .toHaveBeenCalled());
    const probe = await findByTestId("probe-items");
    expect(probe.textContent).toBe("1");
  });
});
