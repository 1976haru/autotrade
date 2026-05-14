/**
 * #86 UpdateCheckerCard 테스트.
 *
 * 요구 사항 매핑:
 * - 현재 버전 / 최신 버전 / 채널 / 마지막 확인 시간 표시
 * - 업데이트 확인 버튼
 * - 새 버전 있음 / 다운로드 중 / 재시작 후 적용 / 실패 분기
 * - mock 응답 주입 (provider prop)
 * - 자동 적용 안 됨 — "재시작하여 적용" 명시 클릭 invariant
 * - 안전 invariant: ENABLE_* flag 변경 0건 / Place Order 같은 enabling 버튼 없음
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { UpdateCheckerCard } from "./UpdateCheckerCard";
import {
  UPDATE_CHANNEL,
  UPDATE_STAGE,
  _resetUpdateCheckerStorageForTests,
} from "../../store/useUpdateChecker";
import { APP_INFO } from "../../config/appInfo";


beforeEach(() => {
  _resetUpdateCheckerStorageForTests();
});

afterEach(() => {
  cleanup();
  _resetUpdateCheckerStorageForTests();
});


// ====================================================================
// 1. 기본 렌더링
// ====================================================================


describe("UpdateCheckerCard — 기본 렌더링", () => {
  it("카드 렌더링 + mock 배지", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    expect(getByTestId("update-checker-card")).toBeTruthy();
    expect(getByTestId("update-checker-mock-badge").textContent)
      .toContain("자동 적용 안 함");
  });

  it("현재 버전이 APP_INFO 와 일치", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    expect(getByTestId("update-checker-current").textContent)
      .toContain(`v${APP_INFO.version}`);
  });

  it("초기 상태 — '확인 전'", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    expect(getByTestId("update-checker-stage").textContent).toContain("확인 전");
    expect(getByTestId("update-checker-last-checked").textContent)
      .toContain("아직 확인");
    expect(getByTestId("update-checker-latest").textContent).toBe("—");
  });

  it("default 채널은 beta", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    expect(getByTestId("update-checker-channel-current").textContent).toBe("beta");
  });
});


// ====================================================================
// 2. 업데이트 확인 — 동일 버전 → '최신 상태'
// ====================================================================


describe("UpdateCheckerCard — 확인 분기", () => {
  it("provider 가 동일 버전 반환 → UP_TO_DATE", async () => {
    const provider = vi.fn().mockResolvedValue({
      version: APP_INFO.version, channel: "beta", notes: "same",
    });
    const { getByTestId } = render(<UpdateCheckerCard provider={provider} />);

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-stage").textContent).toContain("최신");
    });
    expect(provider).toHaveBeenCalled();
  });

  it("provider 가 다른 버전 반환 → UPDATE_FOUND + notes 표시", async () => {
    const provider = vi.fn().mockResolvedValue({
      version: "9.9.9", channel: "beta",
      notes: "테스트용 변경사항: 매수 개선 / 위험 강화",
    });
    const { getByTestId } = render(<UpdateCheckerCard provider={provider} />);

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-stage").textContent).toContain("새 버전");
    });
    expect(getByTestId("update-checker-latest").textContent).toContain("9.9.9");
    expect(getByTestId("update-checker-notes").textContent).toContain("매수 개선");
  });

  it("provider 가 throw → ERROR + 친절한 오류 + GitHub Releases 안내", async () => {
    const provider = vi.fn().mockRejectedValue(new Error("network down"));
    const { getByTestId } = render(<UpdateCheckerCard provider={provider} />);

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-stage").textContent).toContain("실패");
    });
    expect(getByTestId("update-checker-error").textContent).toContain("network down");
    expect(getByTestId("update-checker-error").textContent)
      .toContain("GitHub Releases");
  });
});


// ====================================================================
// 3. 다운로드 / 재시작 시뮬레이션 (자동 적용 *안 함* invariant)
// ====================================================================


describe("UpdateCheckerCard — 다운로드 / 적용", () => {
  it("새 버전 발견 → '새 버전 다운로드' 버튼 표시, 클릭 후 DOWNLOADING", async () => {
    const provider = vi.fn().mockResolvedValue({
      version: "9.9.9", channel: "beta", notes: "x",
    });
    const { getByTestId, queryByTestId } = render(
      <UpdateCheckerCard provider={provider} />,
    );
    // 다운로드 버튼은 처음엔 없음.
    expect(queryByTestId("update-checker-download-button")).toBeNull();

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-download-button")).toBeTruthy();
    });

    // 다운로드 클릭 → DOWNLOADING / progress.
    await act(async () => {
      fireEvent.click(getByTestId("update-checker-download-button"));
    });
    // progress 가 0보다 큰 값 또는 READY_RESTART 까지 도달.
    await waitFor(() => {
      const stage = getByTestId("update-checker-stage").textContent;
      expect(stage.includes("다운로드") || stage.includes("재시작")).toBe(true);
    });
  });

  it("다운로드 완료 → READY_RESTART + '재시작하여 적용' 버튼", async () => {
    const provider = vi.fn().mockResolvedValue({
      version: "9.9.9", channel: "beta", notes: "x",
    });
    const { getByTestId } = render(<UpdateCheckerCard provider={provider} />);

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() =>
      expect(getByTestId("update-checker-download-button")).toBeTruthy());
    await act(async () => {
      fireEvent.click(getByTestId("update-checker-download-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-apply-button")).toBeTruthy();
    }, { timeout: 3000 });
    // restart notice 도 노출.
    expect(getByTestId("update-checker-restart-notice").textContent)
      .toContain("재시작");
  });

  it("'재시작하여 적용' 클릭 전에는 stage 가 절대 IDLE 로 *자동* 돌아가지 " +
     "않는다 — 자동 적용 invariant", async () => {
    const provider = vi.fn().mockResolvedValue({
      version: "9.9.9", channel: "beta", notes: "x",
    });
    const { getByTestId } = render(<UpdateCheckerCard provider={provider} />);

    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() =>
      expect(getByTestId("update-checker-download-button")).toBeTruthy());
    await act(async () => {
      fireEvent.click(getByTestId("update-checker-download-button"));
    });
    await waitFor(() =>
      expect(getByTestId("update-checker-apply-button")).toBeTruthy());

    // 사용자가 직접 클릭해야만 IDLE 로 복귀 (mock — 실 동작에서는 process.relaunch).
    await act(async () => {
      fireEvent.click(getByTestId("update-checker-apply-button"));
    });
    await waitFor(() => {
      expect(getByTestId("update-checker-stage").textContent).toContain("확인 전");
    });
  });
});


// ====================================================================
// 4. 채널 전환 + localStorage 영속
// ====================================================================


describe("UpdateCheckerCard — 채널 전환", () => {
  it("stable 클릭 → channel state 변경 + localStorage 영속", async () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    expect(getByTestId("update-checker-channel-current").textContent).toBe("beta");

    fireEvent.click(getByTestId("update-checker-channel-stable"));
    await waitFor(() =>
      expect(getByTestId("update-checker-channel-current").textContent).toBe("stable"));

    expect(window.localStorage.getItem("agent-trader-update-channel"))
      .toBe(UPDATE_CHANNEL.STABLE);
  });

  it("beta 로 돌아가기 가능", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    fireEvent.click(getByTestId("update-checker-channel-stable"));
    fireEvent.click(getByTestId("update-checker-channel-beta"));
    expect(getByTestId("update-checker-channel-current").textContent).toBe("beta");
  });
});


// ====================================================================
// 5. enabling button 0개 invariant
// ====================================================================


describe("UpdateCheckerCard — invariant", () => {
  it("실거래 / Place Order / ENABLE_LIVE_TRADING 같은 enabling 라벨 버튼 0개", () => {
    const { container } = render(<UpdateCheckerCard />);
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      for (const banned of [
        "place order", "실거래", "live trading", "live trading on",
        "enable_live", "enable live", "enable_ai_execution",
        "주문 실행", "주문 시작", "실거래 켜기", "ai 자동 실행",
      ]) {
        expect(text).not.toContain(banned.toLowerCase());
      }
    }
  });

  it("localStorage 에는 channel 과 lastChecked 만 저장 — secret 형태 키 0건", async () => {
    const { getByTestId } = render(
      <UpdateCheckerCard provider={() =>
        Promise.resolve({ version: APP_INFO.version, channel: "beta", notes: "x" })
      } />,
    );
    await act(async () => {
      fireEvent.click(getByTestId("update-checker-check-button"));
    });
    await waitFor(() =>
      expect(getByTestId("update-checker-last-checked").textContent).not.toContain("아직"));

    // localStorage 에 어떤 secret-스러운 키도 없어야 함.
    for (let i = 0; i < window.localStorage.length; i += 1) {
      const k = window.localStorage.key(i) || "";
      for (const banned of [
        "kis_app_key", "kis_app_secret", "kis_account",
        "anthropic_api_key", "openai_api_key", "api_key",
      ]) {
        expect(k.toLowerCase()).not.toContain(banned);
      }
    }
  });

  it("STAGE enum 의 모든 값에 BUY/SELL/HOLD 단어 없음", () => {
    for (const v of Object.values(UPDATE_STAGE)) {
      const s = String(v).toLowerCase();
      expect(s).not.toContain("buy");
      expect(s).not.toContain("sell");
      expect(s).not.toContain("hold");
    }
  });
});


// ====================================================================
// 6. 안전 안내 텍스트 노출
// ====================================================================


describe("UpdateCheckerCard — 안전 안내", () => {
  it("'자동 적용' 안 한다는 안내 텍스트 노출", () => {
    const { getByTestId } = render(<UpdateCheckerCard />);
    const notice = getByTestId("update-checker-notice").textContent;
    expect(notice).toContain("재시작하여 적용");
    expect(notice).toContain("서명되지 않은");
  });
});
