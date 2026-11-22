import { describe, it, expect, vi, afterEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

import { RiskProfileSwitchCard } from "./RiskProfileSwitchCard";

afterEach(() => cleanup());

const rt = (over = {}) => ({
  active_profile: { value: "balanced", effective: { effective_min_confidence: 0.6, max_risk_flags: 1 } },
  profiles_effective: {
    conservative: { effective_min_confidence: 0.7, max_risk_flags: 0 },
    balanced: { effective_min_confidence: 0.6, max_risk_flags: 1 },
    aggressive: { effective_min_confidence: 0.6, max_risk_flags: 2 },
  },
  ...over,
});

describe("RiskProfileSwitchCard (S2/S6)", () => {
  it("활성 성향(안정형) 강조 + 클램프 적용 실효값 표시(확신 60%, 프리셋 45% 아님)", () => {
    const { getByTestId } = render(<RiskProfileSwitchCard rtConfig={rt()} botRunning={false} api={{}} />);
    expect(getByTestId("profile-tab-balanced").getAttribute("aria-pressed")).toBe("true");
    expect(getByTestId("profile-effective").textContent).toContain("확신 기준 60%");
  });

  it("봇 실행 중: 탭 안내 + PUT 미발사(선제 안내)", async () => {
    const put = vi.fn();
    const { getByTestId } = render(<RiskProfileSwitchCard rtConfig={rt()} botRunning api={{ runtimeProfilePut: put }} confirmFn={() => true} />);
    expect(getByTestId("profile-locked").textContent).toContain("자동매매를 멈추면");
    fireEvent.click(getByTestId("profile-tab-aggressive"));
    await waitFor(() => expect(getByTestId("profile-note").textContent).toContain("멈추면"));
    expect(put).not.toHaveBeenCalled();
  });

  it("정지 상태 전환: 다이얼로그 확정 → PUT → 서버 응답값 반영 + 적용 안내", async () => {
    const put = vi.fn(async () => ({ active_profile: { value: "aggressive" } }));
    const onChanged = vi.fn();
    const { getByTestId } = render(<RiskProfileSwitchCard rtConfig={rt()} botRunning={false} api={{ runtimeProfilePut: put }} onChanged={onChanged} confirmFn={() => true} />);
    fireEvent.click(getByTestId("profile-tab-aggressive"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("aggressive"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(getByTestId("profile-note").textContent).toContain("적용됐어요");
    expect(getByTestId("profile-note").textContent).toContain("다음 매매 판단부터");
  });

  it("다이얼로그 취소 → 전환 미발사", () => {
    const put = vi.fn();
    const { getByTestId } = render(<RiskProfileSwitchCard rtConfig={rt()} botRunning={false} api={{ runtimeProfilePut: put }} confirmFn={() => false} />);
    fireEvent.click(getByTestId("profile-tab-aggressive"));
    expect(put).not.toHaveBeenCalled();
  });

  it("전환 실패 → 정직 실패 표시", async () => {
    const put = vi.fn(async () => { throw { detail: "자동매매를 먼저 멈춘 뒤 바꿔주세요." }; });
    const { getByTestId } = render(<RiskProfileSwitchCard rtConfig={rt()} botRunning={false} api={{ runtimeProfilePut: put }} confirmFn={() => true} />);
    fireEvent.click(getByTestId("profile-tab-aggressive"));
    await waitFor(() => expect(getByTestId("profile-note").textContent).toContain("멈춘 뒤"));
  });
});
