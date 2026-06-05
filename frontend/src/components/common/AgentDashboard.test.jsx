import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { AgentDashboard } from "./AgentDashboard";

afterEach(() => cleanup());

const full = {
  agentFunnel: vi.fn(async () => ({
    no_data: false,
    stages: [
      { key: "signal", label: "신호 발생", count: 10 },
      { key: "council", label: "council 통과", count: 6 },
      { key: "submitted", label: "주문 제출", count: 4 },
      { key: "filled", label: "체결", count: 3 },
    ],
    drops: [
      { from: "signal", to: "council", count: 4, reasons: [{ reason_code: "LOW_CONFIDENCE", count: 4 }] },
      { from: "council", to: "submitted", count: 2, reasons: [{ reason_code: "KIS_PAPER_ORDER_LIMIT_EXCEEDED", count: 2 }] },
      { from: "submitted", to: "filled", count: 1, reasons: [] },
    ],
  })),
  performanceByTechnique: vi.fn(async () => ({
    no_data: false, small_sample: true,
    techniques: [{ technique: "ORB", trade_count: 4, win_rate: 0.75, net_contribution_krw: 12000 }],
  })),
  agentCalibration: vi.fn(async () => ({
    no_data: false, buckets: [{ bucket: "0.7~0.8", trade_count: 6, win_rate: 0.5, small_sample: false }],
  })),
  agentShadow: vi.fn(async () => ({
    no_data: false, completed_count: 12, correct_rejection_count: 9, correct_rate: 0.75,
    avoided_loss_krw: 30000, missed_gain_krw: 5000, tracking_count: 3,
  })),
  agentLearning: vi.fn(async () => ({
    observations: [{ code: "GOOD_REJECTION", text: "council의 기각이 손실을 잘 걸러내고 있어요", evidence: {} }],
    footer: "이 관찰을 바탕으로 한 설정 변경은 운영자 승인으로 진행돼요.",
  })),
};

const empty = {
  agentFunnel: vi.fn(async () => ({ no_data: true, stages: [], drops: [] })),
  performanceByTechnique: vi.fn(async () => ({ no_data: true, techniques: [] })),
  agentCalibration: vi.fn(async () => ({ no_data: true, buckets: [] })),
  agentShadow: vi.fn(async () => ({ no_data: true, tracking_count: 2, track_days: 5 })),
  agentLearning: vi.fn(async () => ({ observations: [{ code: "INSUFFICIENT_SAMPLE", text: "아직 표본이 부족해요 — 판단 보류" }], footer: "이 관찰을 바탕으로 한 설정 변경은 운영자 승인으로 진행돼요." })),
};

describe("AgentDashboard (AG6/AG7)", () => {
  it("F3: shadow 조회 실패(null) → 빈 템플릿(숫자 빠진 문장) 미생성", async () => {
    const api = { ...empty, agentShadow: vi.fn(async () => { throw new Error("fail"); }) };
    const { getByTestId, queryByTestId } = render(<AgentDashboard api={api} />);
    await waitFor(() => expect(getByTestId("agent-shadow-empty")).toBeTruthy());
    expect(queryByTestId("agent-shadow-summary")).toBeNull();
  });

  it("5개 섹션 + 깔때기 단계·감소 사유 + 그림자 요약 + 학습 관찰", async () => {
    const { getByTestId } = render(<AgentDashboard api={full} />);
    await waitFor(() => expect(getByTestId("agent-funnel").textContent).toContain("신호 발생"));
    // 깔때기 감소 사유 한국어 매핑.
    expect(getByTestId("agent-funnel").textContent).toContain("확신 부족");
    // S4 재사용 기법 기여.
    expect(getByTestId("agent-tech-ORB").textContent).toContain("찬성 4");
    // 보정 구간.
    expect(getByTestId("agent-cal-0.7~0.8").textContent).toContain("실제 50%");
    // 그림자 추적.
    expect(getByTestId("agent-shadow-summary").textContent).toContain("기각 12건 중 9건은 안 사길 잘했어요");
    expect(getByTestId("agent-shadow").textContent).toContain("회피 손실");
    // 학습 + 푸터(운영자 승인).
    expect(getByTestId("agent-obs-GOOD_REJECTION")).toBeTruthy();
    expect(getByTestId("agent-learning-footer").textContent).toContain("운영자 승인");
  });

  it("no_data: 각 섹션 '쌓이는 중' (가짜 0 금지), 그림자는 추적 중 K건 표기", async () => {
    const { getByTestId } = render(<AgentDashboard api={empty} />);
    await waitFor(() => expect(getByTestId("agent-funnel-empty")).toBeTruthy());
    expect(getByTestId("agent-cal-empty")).toBeTruthy();
    expect(getByTestId("agent-shadow-empty").textContent).toContain("추적 중 2건");
  });

  it("탭 전환 시 해당 period로 재조회 (폴링 아님)", async () => {
    const { getByTestId } = render(<AgentDashboard api={full} />);
    await waitFor(() => expect(full.agentFunnel).toHaveBeenCalledWith({ period: "daily" }));
    fireEvent.click(getByTestId("agent-tab-weekly"));
    await waitFor(() => expect(full.agentFunnel).toHaveBeenCalledWith({ period: "weekly" }));
  });
});
