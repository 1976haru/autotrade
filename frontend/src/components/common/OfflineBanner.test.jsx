/**
 * 체크리스트 #63: OfflineBanner 테스트.
 *
 * invariant:
 *   - online 시 노출 안 함
 *   - offline 시 "주문 / 승인 / 봇 시작 / Kill Switch" 비활성 명시
 *   - 푸시 알림 부재 안내
 *   - online/offline 이벤트로 toggle
 */

import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { OfflineBanner } from "./OfflineBanner";


const _originalDescriptor = Object.getOwnPropertyDescriptor(
  window.navigator, "onLine",
);


function _setOnline(value) {
  Object.defineProperty(window.navigator, "onLine", {
    configurable: true,
    get() { return value; },
  });
}


function _restoreOnline() {
  if (_originalDescriptor) {
    Object.defineProperty(window.navigator, "onLine", _originalDescriptor);
  } else {
    Object.defineProperty(window.navigator, "onLine", {
      configurable: true, value: true, writable: true,
    });
  }
}


afterEach(() => { cleanup(); _restoreOnline(); });
beforeEach(() => { _setOnline(true); });


describe("<OfflineBanner>", () => {
  it("renders nothing while online", () => {
    _setOnline(true);
    const { container } = render(<OfflineBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("renders banner when initially offline", () => {
    _setOnline(false);
    const { getByTestId } = render(<OfflineBanner />);
    expect(getByTestId("offline-banner")).toBeTruthy();
    expect(getByTestId("offline-banner-title").textContent)
      .toMatch(/오프라인 상태/);
  });

  it("renders explicit '주문 / 승인 / Kill Switch 비활성' hint", () => {
    _setOnline(false);
    const { getByTestId } = render(<OfflineBanner />);
    const hint = getByTestId("offline-banner-disabled-hint");
    expect(hint.textContent).toMatch(/주문/);
    expect(hint.textContent).toMatch(/승인/);
    expect(hint.textContent).toMatch(/봇 시작/);
    expect(hint.textContent).toMatch(/Kill Switch/);
    expect(hint.textContent).toMatch(/동작하지 않습니다/);
  });

  it("renders '푸시 알림은 보안 검토 후' notice", () => {
    _setOnline(false);
    const { getByTestId } = render(<OfflineBanner />);
    expect(getByTestId("offline-banner-push-hint").textContent)
      .toMatch(/푸시 알림.*보안 검토/);
  });

  it("appears when offline event fires while mounted", async () => {
    _setOnline(true);
    const { queryByTestId } = render(<OfflineBanner />);
    expect(queryByTestId("offline-banner")).toBeNull();

    // navigator.onLine을 false로 바꾸고 offline 이벤트 발생.
    _setOnline(false);
    await act(async () => {
      window.dispatchEvent(new Event("offline"));
    });
    expect(queryByTestId("offline-banner")).toBeTruthy();
  });

  it("disappears when online event fires after offline", async () => {
    _setOnline(false);
    const { queryByTestId } = render(<OfflineBanner />);
    expect(queryByTestId("offline-banner")).toBeTruthy();

    _setOnline(true);
    await act(async () => {
      window.dispatchEvent(new Event("online"));
    });
    expect(queryByTestId("offline-banner")).toBeNull();
  });

  it("uses custom testId when provided", () => {
    _setOnline(false);
    const { getByTestId } = render(<OfflineBanner testId="custom-offline" />);
    expect(getByTestId("custom-offline")).toBeTruthy();
    expect(getByTestId("custom-offline-title")).toBeTruthy();
  });
});
