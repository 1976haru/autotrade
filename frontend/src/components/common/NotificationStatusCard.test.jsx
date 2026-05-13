/**
 * 체크리스트 #64: NotificationStatusCard 테스트.
 *
 * invariant:
 *   - Token / chat_id 입력 input 0개
 *   - "Token은 backend .env에만 저장됩니다" 안내 항상 노출
 *   - 알림 종류 chip 노출 (긴급정지 / 손실한도 / 승인 대기 / 데이터 지연 등)
 *   - 활성 + telegram_configured일 때만 테스트 버튼 활성화
 *   - 테스트 버튼 클릭 시 backendApi.notificationsTest 호출
 *   - friendly error — raw "Failed to fetch" 미노출
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationStatusCard, ALERT_TYPES } from "./NotificationStatusCard";
import { backendApi } from "../../services/backend/client";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    notificationsStatus:    vi.fn(),
    notificationsTest:      vi.fn(),
    notificationsMockEvent: vi.fn(),
    // 절대 호출되면 안 되는 주문/리스크 API
    brokerOrder:         vi.fn(),
    approveApproval:     vi.fn(),
    cancelApproval:      vi.fn(),
    setEmergencyStop:    vi.fn(),
  },
}));


afterEach(() => { cleanup(); vi.clearAllMocks(); });


function _statusBody(overrides = {}) {
  return {
    enabled:               false,
    channel:               "noop",
    channel_configured:    false,
    telegram_configured:   false,
    min_severity:          10,
    min_severity_name:     "INFO",
    dedupe_window_seconds: 60,
    always_send_critical:  true,
    notice: "Telegram Bot Token / chat_id는 backend/.env에만 저장됩니다.",
    ...overrides,
  };
}


beforeEach(() => {
  backendApi.notificationsStatus.mockResolvedValue(_statusBody());
});


describe("<NotificationStatusCard>", () => {
  it("renders the card with secret-free fields", async () => {
    const { findByTestId } = render(<NotificationStatusCard />);
    await findByTestId("notification-status-card");
    // 보안 안내 항상 노출
    const notice = await findByTestId("notification-status-card-secret-notice");
    expect(notice.textContent).toMatch(/backend\/.env에만/);
    expect(notice.textContent).toMatch(/Secret도 저장되지 않습니다/);
    // 알림 종류 chip
    const types = await findByTestId("notification-status-card-alert-types");
    expect(types.textContent).toMatch(/긴급정지/);
    expect(types.textContent).toMatch(/손실한도/);
    expect(types.textContent).toMatch(/승인 대기/);
    expect(types.textContent).toMatch(/데이터 지연/);
    expect(types.textContent).toMatch(/API 장애/);
  });

  it("never renders a token / chat_id input field (invariant lock)", async () => {
    const { findByTestId, container } = render(<NotificationStatusCard />);
    await findByTestId("notification-status-card");
    // 어떤 input 또는 textarea도 없음 — 본 카드는 상태 표시 + 버튼만.
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
    expect(container.querySelectorAll("select").length).toBe(0);
  });

  it("disables test button when notifications_enabled=false", async () => {
    const { findByTestId } = render(<NotificationStatusCard />);
    const btn = await findByTestId("notification-status-card-test-btn");
    expect(btn.disabled).toBe(true);
    const hint = await findByTestId("notification-status-card-disabled-hint");
    expect(hint.textContent).toMatch(/NOTIFICATIONS_ENABLED=false/);
  });

  it("disables test button when telegram not configured", async () => {
    backendApi.notificationsStatus.mockResolvedValueOnce(_statusBody({
      enabled: true, telegram_configured: false, channel: "noop",
    }));
    const { findByTestId } = render(<NotificationStatusCard />);
    const btn = await findByTestId("notification-status-card-test-btn");
    expect(btn.disabled).toBe(true);
    const hint = await findByTestId("notification-status-card-disabled-hint");
    expect(hint.textContent).toMatch(/Telegram 미구성/);
  });

  it("enables test button + calls notificationsTest when both ready", async () => {
    backendApi.notificationsStatus.mockResolvedValueOnce(_statusBody({
      enabled: true, telegram_configured: true, channel: "telegram",
      channel_configured: true,
    }));
    backendApi.notificationsTest.mockResolvedValueOnce({
      ok: true, channel: "telegram", skipped_reason: null, error: null,
    });
    const { findByTestId } = render(<NotificationStatusCard />);
    const btn = await findByTestId("notification-status-card-test-btn");
    expect(btn.disabled).toBe(false);
    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => {
      expect(backendApi.notificationsTest).toHaveBeenCalled();
    });
    const result = await findByTestId("notification-status-card-test-result");
    expect(result.textContent).toMatch(/발송 성공/);
  });

  it("shows friendly error when status fetch fails (no raw 'Failed to fetch')", async () => {
    backendApi.notificationsStatus.mockRejectedValueOnce(new Error("Failed to fetch"));
    const { findByTestId } = render(<NotificationStatusCard />);
    const err = await findByTestId("notification-status-card-error");
    // raw 미노출
    expect(err.textContent).not.toBe("Failed to fetch");
    expect(err.textContent).toMatch(/백엔드|데모/);
  });

  it("does not call any forbidden order / broker / emergency API during lifecycle", async () => {
    const { findByTestId } = render(<NotificationStatusCard />);
    await findByTestId("notification-status-card");
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
    expect(backendApi.approveApproval).not.toHaveBeenCalled();
    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
    // notificationsStatus만 호출됨 — 테스트 버튼은 사용자가 클릭해야만
    expect(backendApi.notificationsStatus).toHaveBeenCalled();
    expect(backendApi.notificationsTest).not.toHaveBeenCalled();
  });

  it("ALERT_TYPES export contains the spec'd alert categories", () => {
    const labels = ALERT_TYPES.map((t) => t.label);
    expect(labels).toContain("긴급정지");
    expect(labels).toContain("손실한도 접근");
    expect(labels).toContain("승인 대기");
    expect(labels).toContain("데이터 지연");
    expect(labels).toContain("API 장애");
    expect(labels).toContain("선물 margin risk");
  });

  it("shows status fields (channel / Telegram configured / min severity / dedupe)", async () => {
    backendApi.notificationsStatus.mockResolvedValueOnce(_statusBody({
      channel: "telegram", channel_configured: true,
      telegram_configured: true, min_severity_name: "WARN",
      dedupe_window_seconds: 120,
    }));
    const { findByTestId } = render(<NotificationStatusCard />);
    const fields = await findByTestId("notification-status-card-fields");
    expect(fields.textContent).toMatch(/telegram/);
    expect(fields.textContent).toMatch(/구성됨/);
    expect(fields.textContent).toMatch(/WARN/);
    expect(fields.textContent).toMatch(/120/);
  });

  it("does not render the test result block before the user clicks", async () => {
    const { findByTestId, queryByTestId } = render(<NotificationStatusCard />);
    await findByTestId("notification-status-card");
    expect(queryByTestId("notification-status-card-test-result")).toBeNull();
  });
});
