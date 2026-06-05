import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { TechniqueScorecard } from "./TechniqueScorecard";

afterEach(() => cleanup());

const data = (over = {}) => ({
  no_data: false, small_sample: false, active_profile: "balanced",
  techniques: [
    { technique: "ORB", trade_count: 4, win_count: 3, loss_count: 1, win_rate: 0.75, net_contribution_krw: 12000 },
    { technique: "MOMENTUM", trade_count: 2, win_count: 1, loss_count: 1, win_rate: 0.5, net_contribution_krw: -3000 },
    { technique: "VWAP", trade_count: 0, win_count: 0, loss_count: 0, win_rate: null, net_contribution_krw: 0 },
    { technique: "GAP", trade_count: 0, win_count: 0, loss_count: 0, win_rate: null, net_contribution_krw: 0 },
  ],
  ...over,
});

describe("TechniqueScorecard (S5/S6)", () => {
  it("기법별 행 + 다중 귀속 안내 + 현재 성향 명시", async () => {
    const api = { performanceByTechnique: vi.fn(async () => data()) };
    const { getByTestId, getByText } = render(<TechniqueScorecard api={api} />);
    await waitFor(() => expect(getByTestId("technique-row-ORB").textContent).toContain("찬성 4건"));
    expect(getByTestId("technique-row-ORB").textContent).toContain("75%");
    expect(getByTestId("technique-row-ORB").textContent).toContain("+12,000원");
    expect(getByTestId("technique-note").textContent).toContain("여러 기법이 함께 찬성");
    expect(getByText(/현재 안정형 기준/)).toBeTruthy();
  });

  it("no_data: '거래가 쌓이면 표시돼요' (가짜 0 금지)", async () => {
    const api = { performanceByTechnique: vi.fn(async () => data({ no_data: true, techniques: [] })) };
    const { getByTestId } = render(<TechniqueScorecard api={api} />);
    await waitFor(() => expect(getByTestId("technique-nodata").textContent).toContain("거래가 쌓이면"));
  });

  it("small_sample 뱃지", async () => {
    const api = { performanceByTechnique: vi.fn(async () => data({ small_sample: true })) };
    const { getByTestId } = render(<TechniqueScorecard api={api} />);
    await waitFor(() => expect(getByTestId("technique-smallsample")).toBeTruthy());
  });

  it("탭 전환 시 해당 period 재조회 (폴링 아님)", async () => {
    const api = { performanceByTechnique: vi.fn(async () => data()) };
    const { getByTestId } = render(<TechniqueScorecard api={api} />);
    await waitFor(() => expect(api.performanceByTechnique).toHaveBeenCalledWith({ period: "daily" }));
    fireEvent.click(getByTestId("technique-tab-weekly"));
    await waitFor(() => expect(api.performanceByTechnique).toHaveBeenCalledWith({ period: "weekly" }));
  });

  it("조회 실패 → 정직 표시", async () => {
    const api = { performanceByTechnique: vi.fn(async () => { throw new Error("x"); }) };
    const { getByTestId } = render(<TechniqueScorecard api={api} />);
    await waitFor(() => expect(getByTestId("technique-fail")).toBeTruthy());
  });
});
