/**
 * AutoPaperLoopCard 단위 테스트.
 *
 * invariant 강제:
 * - "uvicorn" / "Place Order" / "지금 매수" / "지금 매도" / "실거래 시작" / "ENABLE_*" 라벨 0건
 * - 시작 / 정지 / 긴급정지 버튼이 정확한 API 를 호출
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AutoPaperLoopCard } from "./AutoPaperLoopCard";


function _mockApi(initialStatus = { state: "IDLE", cycle_count: 0 }) {
  return {
    autoPaperStatus: vi.fn(async () => initialStatus),
    autoPaperStart: vi.fn(async () => ({ state: "RUNNING", cycle_count: 0 })),
    autoPaperStop: vi.fn(async () => ({ state: "STOPPED", cycle_count: 5 })),
    autoPaperEmergencyStop: vi.fn(async () => ({ state: "EMERGENCY", cycle_count: 5 })),
    autoPaperReset: vi.fn(async () => ({ state: "IDLE", cycle_count: 0 })),
    desktopHealth: vi.fn(async () => ({
      ok: true,
      safety_flags: {
        enable_live_trading: false,
        enable_ai_execution: false,
        enable_futures_live_trading: false,
        kis_is_paper: true,
      },
      auto_paper: initialStatus,
    })),
  };
}


describe("<AutoPaperLoopCard>", () => {
  afterEach(cleanup);

  it("renders safety badges", async () => {
    const api = _mockApi();
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    expect(screen.getByTestId("badge-not-order-signal").textContent).toMatch(/모의 전용/);
    expect(screen.getByTestId("badge-paper-mode").textContent).toMatch(/KIS Paper ON/);
    expect(screen.getByTestId("badge-no-auto-apply").textContent).toMatch(/주문 신호 아님/);
  });

  it("shows live OFF flag when safety_flags.enable_live_trading=false", async () => {
    const api = _mockApi();
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("flag-live-off").textContent).toMatch(/OFF/)
    );
  });

  it("clicking 시작 button calls autoPaperStart", async () => {
    const api = _mockApi({ state: "IDLE", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("btn-start-auto-paper"));
    await waitFor(() => expect(api.autoPaperStart).toHaveBeenCalledTimes(1));
  });

  it("clicking 정지 button calls autoPaperStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("btn-stop-auto-paper"));
    await waitFor(() => expect(api.autoPaperStop).toHaveBeenCalledTimes(1));
  });

  it("clicking 긴급정지 button calls autoPaperEmergencyStop", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("btn-emergency-stop"));
    await waitFor(() => expect(api.autoPaperEmergencyStop).toHaveBeenCalledTimes(1));
  });

  it("start button disabled when RUNNING", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 3 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-start-auto-paper").disabled).toBe(true)
    );
  });

  it("stop button disabled when not RUNNING", async () => {
    const api = _mockApi({ state: "IDLE", cycle_count: 0 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("btn-stop-auto-paper").disabled).toBe(true)
    );
  });

  it("no forbidden labels in card text", async () => {
    const api = _mockApi();
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const text = container.textContent.toLowerCase();
    expect(text).not.toContain("uvicorn");
    expect(text).not.toContain("npm run dev");
    expect(text).not.toContain("place order");
    expect(text).not.toContain("실거래 시작");
    expect(text).not.toContain("enable_live_trading=true");
  });

  it("no buy/sell/place-order labeled buttons", async () => {
    const api = _mockApi();
    const { container } = render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() => expect(api.autoPaperStatus).toHaveBeenCalled());
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      expect(text).not.toContain("place order");
      expect(text).not.toContain("buy");
      expect(text).not.toContain("sell");
      expect(text).not.toContain("매수");
      expect(text).not.toContain("매도");
      expect(text).not.toContain("실거래 시작");
      expect(text).not.toContain("enable_live");
    }
  });

  it("displays cycle count from status", async () => {
    const api = _mockApi({ state: "RUNNING", cycle_count: 42 });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("cycle-count").textContent).toMatch(/42/)
    );
  });

  it("shows error banner when api fails", async () => {
    const api = _mockApi();
    api.autoPaperStatus = vi.fn(async () => {
      throw new Error("backend unreachable");
    });
    api.desktopHealth = vi.fn(async () => {
      throw new Error("backend unreachable");
    });
    render(<AutoPaperLoopCard apiClient={api} pollIntervalMs={0} />);
    await waitFor(() =>
      expect(screen.getByTestId("auto-paper-error").textContent).toMatch(/backend unreachable/)
    );
  });
});
