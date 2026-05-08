import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AiPermissionCard } from "./AiPermissionCard";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    aiPermissionStatus: vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _status(overrides = {}) {
  return {
    mode: "SIMULATION",
    level: "RECOMMEND_ONLY",
    allowed_actions: ["RECOMMEND"],
    blocked_actions: ["SUBMIT_FOR_APPROVAL", "VIRTUAL_EXECUTE", "LIVE_EXECUTE", "FUTURES_LIVE_EXECUTE"],
    requires_human_approval: false,
    virtual_only: false,
    live_execution_disabled: true,
    futures_live_disabled: true,
    flags: {
      enable_live_trading: false,
      enable_ai_execution: false,
      enable_futures_live_trading: false,
      emergency_stop: false,
      disable_ai_orders: false,
    },
    matrix: {},
    notice: "AI API Key는 주문 권한이 아닙니다.",
    ...overrides,
  };
}


describe("<AiPermissionCard>", () => {
  beforeEach(() => {
    backendApi.aiPermissionStatus.mockReset();
  });
  afterEach(cleanup);

  it("renders loading then card", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<AiPermissionCard />);
    expect(getByTestId("ai-permission-card-loading")).toBeTruthy();
    await waitFor(() => getByTestId("ai-permission-card"));
  });

  it("renders RECOMMEND_ONLY state", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(getByTestId("ai-permission-card-level-badge").textContent).toContain("추천");
    expect(getByTestId("ai-permission-card-allowed").textContent).toContain("추천");
    expect(getByTestId("ai-permission-card-blocked").textContent).toContain("실거래 실행");
  });

  it("renders LIMITED_LIVE_EXECUTION when both flags ON", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status({
      mode: "LIVE_AI_EXECUTION",
      level: "LIMITED_LIVE_EXECUTION",
      allowed_actions: ["RECOMMEND", "SUBMIT_FOR_APPROVAL", "VIRTUAL_EXECUTE", "LIVE_EXECUTE", "FUTURES_LIVE_EXECUTE"],
      blocked_actions: [],
      live_execution_disabled: false,
      flags: { enable_live_trading: true, enable_ai_execution: true,
               enable_futures_live_trading: false, emergency_stop: false,
               disable_ai_orders: false },
    }));
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(getByTestId("ai-permission-card-level-badge").textContent).toContain("제한적");
    // live disabled badge 미표시
  });

  it("renders FULL_STOP when emergency_stop", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status({
      level: "FULL_STOP",
      allowed_actions: [],
      blocked_actions: ["RECOMMEND", "SUBMIT_FOR_APPROVAL", "VIRTUAL_EXECUTE", "LIVE_EXECUTE", "FUTURES_LIVE_EXECUTE"],
      flags: { enable_live_trading: false, enable_ai_execution: false,
               enable_futures_live_trading: false, emergency_stop: true,
               disable_ai_orders: false },
    }));
    const { getByTestId, queryByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(getByTestId("ai-permission-card-level-badge").textContent).toContain("FULL_STOP");
    expect(queryByTestId("ai-permission-card-allowed")).toBeNull();
  });

  it("displays the API key vs permission notice", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(getByTestId("ai-permission-card-notice").textContent)
      .toContain("AI API Key는 주문 권한이 아닙니다");
  });

  it("does NOT render any AI execution toggle / live button", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status());
    const { container } = render(<AiPermissionCard />);
    await waitFor(() =>
      expect(container.querySelector('[data-testid="ai-permission-card"]')).toBeTruthy(),
    );
    // 권한 행사 버튼 절대 없음
    expect(container.querySelector('button[data-testid*="enable-ai"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="enable-live"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="execute"]')).toBeNull();
    expect(container.querySelector('button[data-testid*="approve-ai"]')).toBeNull();
  });

  it("does not expose raw 'Failed to fetch'", async () => {
    backendApi.aiPermissionStatus.mockRejectedValue(
      Object.assign(new Error("Failed to fetch"), {}),
    );
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card-error"));
    expect(getByTestId("ai-permission-card-error").textContent)
      .toContain("백엔드 서버에 연결할 수 없습니다");
    expect(getByTestId("ai-permission-card-error").textContent)
      .not.toContain("Failed to fetch");
  });

  it("refresh re-fetches", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status());
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(backendApi.aiPermissionStatus).toHaveBeenCalledTimes(1);
    fireEvent.click(getByTestId("ai-permission-card-refresh"));
    await waitFor(() => expect(backendApi.aiPermissionStatus).toHaveBeenCalledTimes(2));
  });

  it("shows requires_human_approval and virtual_only badges when applicable", async () => {
    backendApi.aiPermissionStatus.mockResolvedValue(_status({
      level: "APPROVAL_REQUIRED",
      requires_human_approval: true,
    }));
    const { getByTestId } = render(<AiPermissionCard />);
    await waitFor(() => getByTestId("ai-permission-card"));
    expect(getByTestId("ai-permission-card-needs-approval")).toBeTruthy();
  });
});
