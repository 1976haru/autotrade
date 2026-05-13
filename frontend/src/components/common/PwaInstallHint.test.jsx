/**
 * 체크리스트 #63: PwaInstallHint 테스트.
 *
 * invariant:
 *   - standalone (이미 설치됨) 시 노출 안 함
 *   - desktop Chrome without beforeinstallprompt + non-iOS → 노출 안 함
 *   - beforeinstallprompt 이벤트 후 install / dismiss 버튼 노출
 *   - iOS Safari fallback 안내
 *   - "푸시 알림" 부재 안내
 *   - sessionStorage dismiss key가 저장됨
 */

import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DISMISS_KEY, PwaInstallHint } from "./PwaInstallHint";


// jsdom에는 matchMedia가 없으니 가짜로 채운다 — display-mode standalone false 가정.
function _stubMatchMedia(standalone) {
  window.matchMedia = vi.fn().mockImplementation((q) => ({
    matches:  q === "(display-mode: standalone)" ? standalone : false,
    media:    q,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }));
}


const _origUserAgent = window.navigator.userAgent;
const _origTouch     = window.navigator.maxTouchPoints;
const _origStandalone = window.navigator.standalone;


function _setUserAgent(ua, maxTouchPoints = 0) {
  Object.defineProperty(window.navigator, "userAgent", {
    configurable: true, get() { return ua; },
  });
  Object.defineProperty(window.navigator, "maxTouchPoints", {
    configurable: true, get() { return maxTouchPoints; },
  });
}


function _setIosStandalone(value) {
  Object.defineProperty(window.navigator, "standalone", {
    configurable: true, get() { return value; },
  });
}


afterEach(() => {
  cleanup();
  _setUserAgent(_origUserAgent);
  _setIosStandalone(_origStandalone);
  sessionStorage.removeItem(DISMISS_KEY);
});


beforeEach(() => {
  _stubMatchMedia(false);
  _setUserAgent("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120");
  _setIosStandalone(undefined);
  sessionStorage.removeItem(DISMISS_KEY);
});


describe("<PwaInstallHint>", () => {
  it("renders nothing when standalone (already installed)", () => {
    _stubMatchMedia(true);
    const { container } = render(<PwaInstallHint />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing on desktop Chrome without beforeinstallprompt + non-iOS", () => {
    const { container } = render(<PwaInstallHint />);
    expect(container.firstChild).toBeNull();
  });

  it("renders iOS fallback hint when iOS Safari", () => {
    _setUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  + "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1");
    const { getByTestId } = render(<PwaInstallHint />);
    expect(getByTestId("pwa-install-hint")).toBeTruthy();
    expect(getByTestId("pwa-install-hint-ios-hint").textContent)
      .toMatch(/홈 화면에 추가/);
  });

  it("renders install button after beforeinstallprompt event", async () => {
    const { queryByTestId, findByTestId } = render(<PwaInstallHint />);
    expect(queryByTestId("pwa-install-hint")).toBeNull();

    const fakeEvent = {
      preventDefault: vi.fn(),
      prompt: vi.fn().mockResolvedValue({ outcome: "accepted" }),
    };
    await act(async () => {
      const e = new Event("beforeinstallprompt");
      Object.assign(e, fakeEvent);
      window.dispatchEvent(e);
    });
    expect(await findByTestId("pwa-install-hint-install-btn")).toBeTruthy();
    expect(await findByTestId("pwa-install-hint-dismiss-btn")).toBeTruthy();
  });

  it("always renders '푸시 알림은 보안 검토' invariant in install card", async () => {
    _setUserAgent("Mozilla/5.0 (iPhone) Safari");
    const { getByTestId } = render(<PwaInstallHint />);
    expect(getByTestId("pwa-install-hint-push-hint").textContent)
      .toMatch(/푸시 알림.*보안 검토/);
  });

  it("dismiss button persists in sessionStorage", async () => {
    _setUserAgent("Mozilla/5.0 (iPhone) Safari");
    const { getByTestId, queryByTestId } = render(<PwaInstallHint />);
    fireEvent.click(getByTestId("pwa-install-hint-dismiss-btn"));
    expect(sessionStorage.getItem(DISMISS_KEY)).toBe("1");
    expect(queryByTestId("pwa-install-hint")).toBeNull();
  });

  it("calls prompt() when install button clicked", async () => {
    const fakeEvent = {
      preventDefault: vi.fn(),
      prompt: vi.fn().mockResolvedValue({ outcome: "accepted" }),
    };
    const { findByTestId } = render(<PwaInstallHint />);
    await act(async () => {
      const e = new Event("beforeinstallprompt");
      Object.assign(e, fakeEvent);
      window.dispatchEvent(e);
    });
    const installBtn = await findByTestId("pwa-install-hint-install-btn");
    await act(async () => {
      fireEvent.click(installBtn);
    });
    expect(fakeEvent.prompt).toHaveBeenCalled();
    expect(sessionStorage.getItem(DISMISS_KEY)).toBe("1");
  });

  it("does not render again in same session after dismiss", () => {
    sessionStorage.setItem(DISMISS_KEY, "1");
    _setUserAgent("Mozilla/5.0 (iPhone) Safari");
    const { container } = render(<PwaInstallHint />);
    expect(container.firstChild).toBeNull();
  });

  it("never includes LIVE order / push notification buttons (invariant)", async () => {
    _setUserAgent("Mozilla/5.0 (iPhone) Safari");
    const { queryByText } = render(<PwaInstallHint />);
    expect(queryByText(/즉시 매수/)).toBeNull();
    expect(queryByText(/Place Order/i)).toBeNull();
    expect(queryByText(/알림 켜기/)).toBeNull();
    expect(queryByText(/Push 알림 활성/)).toBeNull();
    expect(queryByText(/Enable Push/i)).toBeNull();
  });
});
