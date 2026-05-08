import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PaperModeStatusCard } from "./PaperModeStatusCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    paperStatus: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _status(overrides = {}) {
  return {
    mode: "SIMULATION",
    is_paper_mode: true,
    paper_broker_kind: "MOCK",
    kis_is_paper: true,
    enable_live_trading: false,
    enable_ai_execution: false,
    enable_futures_live_trading: false,
    fill_polling_enabled: false,
    notice: "Paper(모의투자) 체결 품질은 실제 체결과 다를 수 있습니다.",
    ...overrides,
  };
}


describe("<PaperModeStatusCard>", () => {
  beforeEach(() => {
    backendApi.paperStatus.mockReset();
  });
  afterEach(cleanup);

  it("renders loading then card", async () => {
    backendApi.paperStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<PaperModeStatusCard />);
    expect(getByTestId("paper-mode-status-card-loading")).toBeTruthy();
    await waitFor(() => getByTestId("paper-mode-status-card"));
  });

  it("renders SIMULATION mode badge", async () => {
    backendApi.paperStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(getByTestId("paper-mode-status-card-mode-badge").textContent).toContain("SIMULATION");
  });

  it("renders MOCK broker label", async () => {
    backendApi.paperStatus.mockResolvedValue(_status({ paper_broker_kind: "MOCK" }));
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(getByTestId("paper-mode-status-card-broker").textContent)
      .toContain("MockBroker");
  });

  it("renders KIS_PAPER broker label", async () => {
    backendApi.paperStatus.mockResolvedValue(_status({
      mode: "PAPER", paper_broker_kind: "KIS_PAPER",
    }));
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(getByTestId("paper-mode-status-card-broker").textContent)
      .toContain("KIS 모의투자");
  });

  it("flags enable_live_trading=ON as DANGER", async () => {
    backendApi.paperStatus.mockResolvedValue(_status({ enable_live_trading: true }));
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(getByTestId("paper-mode-status-card-flag-live-trading").textContent)
      .toContain("ON");
  });

  it("renders the paper fill quality notice", async () => {
    backendApi.paperStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(getByTestId("paper-mode-status-card-notice").textContent)
      .toContain("체결 품질");
  });

  it("does NOT render any test-order or live button", async () => {
    backendApi.paperStatus.mockResolvedValue(_status());
    const { container } = render(<PaperModeStatusCard />);
    await waitFor(() =>
      expect(container.querySelector('[data-testid="paper-mode-status-card"]')).toBeTruthy(),
    );
    expect(container.querySelector('button[data-testid*="place"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="test-order"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="live"]')).toBeNull();
  });

  it("does not expose 'Failed to fetch'", async () => {
    backendApi.paperStatus.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), {}),
    );
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card-error"));
    expect(getByTestId("paper-mode-status-card-error").textContent)
      .not.toContain("Failed to fetch");
  });

  it("refresh button re-fetches", async () => {
    backendApi.paperStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<PaperModeStatusCard />);
    await waitFor(() => getByTestId("paper-mode-status-card"));
    expect(backendApi.paperStatus).toHaveBeenCalledTimes(1);
    fireEvent.click(getByTestId("paper-mode-status-card-refresh"));
    await waitFor(() => expect(backendApi.paperStatus).toHaveBeenCalledTimes(2));
  });
});
