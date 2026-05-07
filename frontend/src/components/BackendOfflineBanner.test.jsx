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
});
