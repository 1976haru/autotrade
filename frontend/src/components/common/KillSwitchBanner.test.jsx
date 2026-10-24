import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatElapsedDuration, KillSwitchBanner } from "./KillSwitchBanner";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    emergencyStopStatus: vi.fn(),
    setEmergencyStop:    vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";


function _status(overrides = {}) {
  return {
    level: "OFF", emergency_stop: false, reason_code: null,
    decided_by: null, note: null, active_since: null,
    cancel_candidate_count: 0, liquidation_candidate_count: 0,
    ...overrides,
  };
}

// 2026-07-14 18:00 KST (화요일, 정규장 종료 후) — 시장시간 강조 분기가 꺼져
// 있는 기본 fixture 시각.
const _AFTER_HOURS_KST = new Date("2026-07-14T09:00:00.000Z").getTime();
// 2026-07-14 10:00 KST (화요일, 정규장 중) — 강조 분기 fixture.
const _MARKET_HOURS_KST = new Date("2026-07-14T01:00:00.000Z").getTime();


describe("formatElapsedDuration", () => {
  it("formats sub-minute as 방금 경과", () => {
    const since = new Date(Date.now() - 10_000).toISOString();
    expect(formatElapsedDuration(since, Date.now())).toBe("방금 경과");
  });
  it("formats hours", () => {
    const now = Date.now();
    const since = new Date(now - 5 * 60 * 60 * 1000).toISOString();
    expect(formatElapsedDuration(since, now)).toBe("5시간 경과");
  });
  it("formats multi-day with remaining hours", () => {
    const now = Date.now();
    const since = new Date(now - (26 * 60 * 60 * 1000)).toISOString();
    // 26h = 1일 2시간
    expect(formatElapsedDuration(since, now)).toBe("1일 2시간 경과");
  });
});


describe("<KillSwitchBanner>", () => {
  beforeEach(() => {
    backendApi.emergencyStopStatus.mockReset();
    backendApi.setEmergencyStop.mockReset();
  });
  afterEach(cleanup);

  it("renders nothing while loading", () => {
    backendApi.emergencyStopStatus.mockReturnValue(new Promise(() => {})); // never resolves
    const { queryByTestId } = render(<KillSwitchBanner />);
    expect(queryByTestId("kill-switch-banner")).toBeNull();
  });

  it("renders nothing when level is OFF", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "OFF" }));
    const { queryByTestId } = render(<KillSwitchBanner />);
    await waitFor(() => expect(backendApi.emergencyStopStatus).toHaveBeenCalled());
    expect(queryByTestId("kill-switch-banner")).toBeNull();
  });

  it("renders a prominent banner when LEVEL_1 is active, with activation time and elapsed duration", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_1",
      decided_by: "operator panel",
      active_since: new Date(_AFTER_HOURS_KST - 5 * 60 * 60 * 1000).toISOString(),
    }));
    const { getByTestId } = render(
      <KillSwitchBanner now={_AFTER_HOURS_KST} />
    );

    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner-title").textContent).toContain("긴급정지 활성 중");
    expect(getByTestId("kill-switch-banner-detail").textContent).toContain("LEVEL 1");
    expect(getByTestId("kill-switch-banner-since").textContent).toContain("5시간 경과");
    expect(getByTestId("kill-switch-banner-detail").textContent).toContain("operator panel");
  });

  it("marks market-hours emphasis via data-market-hours during regular trading hours", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { getByTestId } = render(<KillSwitchBanner now={_MARKET_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner").getAttribute("data-market-hours")).toBe("true");
  });

  it("does not mark market-hours emphasis after regular trading hours", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { getByTestId } = render(<KillSwitchBanner now={_AFTER_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner").getAttribute("data-market-hours")).toBe("false");
  });

  it("the disable button calls setEmergencyStop(false, ...) and the banner disappears once OFF", async () => {
    backendApi.emergencyStopStatus
      .mockResolvedValueOnce(_status({ level: "LEVEL_1" }))
      .mockResolvedValueOnce(_status({ level: "OFF" }));
    backendApi.setEmergencyStop.mockResolvedValueOnce({ emergency_stop: false, level: "OFF" });

    const { getByTestId, queryByTestId } = render(
      <KillSwitchBanner operatorName="tester" />
    );
    await waitFor(() => getByTestId("kill-switch-banner"));

    fireEvent.click(getByTestId("kill-switch-banner-disable-btn"));

    await waitFor(() => expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(
      false, { decided_by: "tester", note: "배너에서 해제" },
    ));
    await waitFor(() => expect(queryByTestId("kill-switch-banner")).toBeNull());
  });

  // P0-SAFETY-FIX-V1: 실제 허용 범위(order_policy)에 맞춘 제목 — 새 정책 L1 은 청산 계속.
  it("new-policy LEVEL_1 → '신규 매수 차단 (보유 청산…계속)' + 서버 안내 문구", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_1", emergency_stop: true,
      order_policy: { active: true, level: "LEVEL_1", legacy_strict: false,
                      allows_eod_forced_exit: true,
                      operator_action_ko: "신규 매수만 차단 — 보유 청산·장마감 강제청산·미체결 매수 취소는 계속됩니다." },
    }));
    const { getByTestId } = render(<KillSwitchBanner now={_AFTER_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner-title").textContent).toContain("신규 매수 차단");
    expect(getByTestId("kill-switch-banner-title").textContent).not.toContain("모든 주문 차단");
    expect(getByTestId("kill-switch-banner-policy").textContent).toContain("청산");
  });

  it("legacy(이전 정책) LEVEL_1 → '모든 주문 차단됨' + 재설정 안내", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({
      level: "LEVEL_1", emergency_stop: true,
      order_policy: { active: true, level: "LEVEL_1", legacy_strict: true,
                      allows_eod_forced_exit: false,
                      operator_action_ko: "모든 자동 주문·취소가 중단됐습니다. (이전 정책으로 켜진 긴급정지 — 청산을 허용하려면 LEVEL 1 을 다시 설정하세요.)" },
    }));
    const { getByTestId } = render(<KillSwitchBanner now={_AFTER_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner-title").textContent).toContain("모든 주문 차단됨");
    expect(getByTestId("kill-switch-banner-policy").textContent).toContain("다시 설정");
  });

  it("order_policy 없음(구버전 backend) → 가장 강한 의미로 표시", async () => {
    backendApi.emergencyStopStatus.mockResolvedValue(_status({ level: "LEVEL_1" }));
    const { getByTestId, queryByTestId } = render(<KillSwitchBanner now={_AFTER_HOURS_KST} />);
    await waitFor(() => getByTestId("kill-switch-banner"));
    expect(getByTestId("kill-switch-banner-title").textContent).toContain("모든 주문 차단됨");
    expect(queryByTestId("kill-switch-banner-policy")).toBeNull();
  });
});
