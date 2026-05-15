import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendOfflineBanner } from "./BackendOfflineBanner";


// 213: useBackendStatus는 useEffect로 fetch — 모킹해서 deterministic하게.
const _statusHook = { status: null, loading: false, error: "" };
vi.mock("../store/useBackendStatus", () => ({
  useBackendStatus: () => _statusHook,
}));


function _set(overrides) {
  Object.assign(_statusHook, { status: null, loading: false, error: "" }, overrides);
}


describe("<BackendOfflineBanner>", () => {
  afterEach(cleanup);

  it("renders nothing while loading", () => {
    _set({ loading: true });
    const { queryByTestId } = render(<BackendOfflineBanner />);
    expect(queryByTestId("backend-offline-banner")).toBeNull();
  });

  it("renders nothing on success", () => {
    _set({ status: { default_mode: "SIMULATION" } });
    const { queryByTestId } = render(<BackendOfflineBanner />);
    expect(queryByTestId("backend-offline-banner")).toBeNull();
  });

  it("shows fallback banner with uvicorn hint on error", () => {
    _set({ error: "Failed to fetch" });
    const { getByTestId } = render(<BackendOfflineBanner />);
    const banner = getByTestId("backend-offline-banner");
    // 240 (Light-003): friendly copy — raw 'Failed to fetch'는 더 이상 노출 X.
    expect(banner.textContent).toContain("백엔드 연결 대기 중");
    expect(banner.textContent).toContain("uvicorn app.main:app");
    expect(banner.textContent).not.toContain("Failed to fetch");
  });

  // 214: VITE_DEMO_MODE=true 빌드(GitHub Pages용)에서는 같은 error 상황이라도
  // 빨간 "백엔드 연결 실패"가 아니라 시안색 "🧪 Demo Mode" 안내가 떠야 한다.
  it("renders the Demo Mode banner when VITE_DEMO_MODE='true'", () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    _set({ error: "Failed to fetch" });
    const { getByTestId, queryByTestId } = render(<BackendOfflineBanner />);
    const banner = getByTestId("demo-mode-banner");
    expect(banner.textContent).toContain("Demo Mode");
    expect(banner.textContent).toContain("UI 데모");
    expect(banner.textContent).toContain("mock");
    // 220: 빌드 태그가 화면에 노출되는지. 자동 갱신 회로의 round-trip 회귀 잠금.
    expect(banner.textContent).toContain("auto-update-220");
    // uvicorn 분기는 노출되지 말 것.
    expect(queryByTestId("backend-offline-banner")).toBeNull();
    vi.unstubAllEnvs();
  });

  // fix/desktop-backend-sidecar-autostart: EXE (Tauri) 모드에서는 uvicorn 안내
  // 대신 "백엔드 자동 실행 중" 친절한 안내 + 재시도 / 로그 보기 버튼.
  describe("Tauri desktop (EXE) mode", () => {
    function _enableDesktop() {
      window.__TAURI_INTERNALS__ = { dummy: true };
    }
    function _disableDesktop() {
      delete window.__TAURI_INTERNALS__;
    }

    afterEach(_disableDesktop);

    it("renders desktop launching banner with no uvicorn hint", () => {
      _enableDesktop();
      _set({ error: "Failed to fetch" });
      const { getByTestId, queryByTestId, container } = render(
        <BackendOfflineBanner />
      );
      const banner = getByTestId("desktop-backend-launching-banner");
      expect(banner.textContent).toContain("백엔드 자동 실행 중");
      expect(banner.textContent).toMatch(/실거래 OFF/);
      // 일반 (개발자) 배너 + uvicorn 단어 0건.
      expect(queryByTestId("backend-offline-banner")).toBeNull();
      expect(container.textContent.toLowerCase()).not.toContain("uvicorn");
      expect(container.textContent).not.toContain("cd backend");
    });

    it("has 재시도 + 로그 보기 buttons", () => {
      _enableDesktop();
      _set({ error: "Failed to fetch" });
      const { getByTestId } = render(<BackendOfflineBanner />);
      expect(getByTestId("btn-retry-connection")).toBeTruthy();
      expect(getByTestId("btn-show-connection-log")).toBeTruthy();
    });

    it("clicking 로그 보기 toggles the log panel", () => {
      _enableDesktop();
      _set({ error: "Failed to fetch" });
      const { getByTestId, queryByTestId } = render(<BackendOfflineBanner />);
      expect(queryByTestId("connection-log-panel")).toBeNull();
      fireEvent.click(getByTestId("btn-show-connection-log"));
      expect(getByTestId("connection-log-panel")).toBeTruthy();
      // 한 번 더 누르면 닫힘.
      fireEvent.click(getByTestId("btn-show-connection-log"));
      expect(queryByTestId("connection-log-panel")).toBeNull();
    });

    it("does not show any 'buy/sell/place-order' label in EXE mode", () => {
      _enableDesktop();
      _set({ error: "Failed to fetch" });
      const { container } = render(<BackendOfflineBanner />);
      const text = container.textContent.toLowerCase();
      expect(text).not.toContain("place order");
      expect(text).not.toContain("매수");
      expect(text).not.toContain("매도");
      expect(text).not.toContain("실거래 시작");
    });
  });
});
