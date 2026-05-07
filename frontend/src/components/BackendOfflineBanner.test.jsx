import { cleanup, render } from "@testing-library/react";
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
    expect(banner.textContent).toContain("백엔드 연결 실패");
    expect(banner.textContent).toContain("uvicorn app.main:app");
    expect(banner.textContent).toContain("Failed to fetch");
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
});
