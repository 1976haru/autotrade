import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BackendDataSourceBanner,
  DataSourceBanner,
  DemoModeBadge,
  resolveDataSource,
} from "./DataSourceBanner";


// Mock isDemoBuild in *both* sources — DataSourceBanner imports from
// BackendOfflineBanner, but friendlyErrorMessage (utils/errorMessage.js) has
// its own copy. Tests must override both to flip demo flag deterministically.
vi.mock("../BackendOfflineBanner", async () => {
  const actual = await vi.importActual("../BackendOfflineBanner");
  return { ...actual, isDemoBuild: vi.fn(() => false) };
});
vi.mock("../../utils/errorMessage", async () => {
  const actual = await vi.importActual("../../utils/errorMessage");
  return { ...actual, isDemoBuild: vi.fn(() => false) };
});

import { isDemoBuild } from "../BackendOfflineBanner";
import { isDemoBuild as isDemoBuildErr } from "../../utils/errorMessage";


afterEach(() => {
  cleanup();
  vi.mocked(isDemoBuild).mockReturnValue(false);
  vi.mocked(isDemoBuildErr).mockReturnValue(false);
});


function _setDemo(on) {
  vi.mocked(isDemoBuild).mockReturnValue(on);
  vi.mocked(isDemoBuildErr).mockReturnValue(on);
}


describe("resolveDataSource", () => {
  it("returns 'mock-virtual' when explicit", () => {
    expect(resolveDataSource({ loading: false, error: "", mode: "mock-virtual" }))
      .toBe("mock-virtual");
  });

  it("returns mode when no error", () => {
    expect(resolveDataSource({ loading: false, error: "", mode: "backend" }))
      .toBe("backend");
  });

  it("returns 'offline' when error and not demo", () => {
    _setDemo(false);
    expect(resolveDataSource({ loading: false, error: "Failed to fetch", mode: "backend" }))
      .toBe("offline");
  });

  it("returns 'demo' when error and demo build", () => {
    _setDemo(true);
    expect(resolveDataSource({ loading: false, error: "Failed to fetch", mode: "backend" }))
      .toBe("demo");
  });

  it("returns mode while loading (no premature offline)", () => {
    expect(resolveDataSource({ loading: true, error: "", mode: "backend" }))
      .toBe("backend");
  });
});


describe("<DataSourceBanner>", () => {
  it("renders backend label when mode=backend", () => {
    const { getByTestId } = render(<DataSourceBanner mode="backend" />);
    const el = getByTestId("data-source-banner");
    expect(el.textContent).toContain("백엔드 연결됨");
  });

  it("renders demo label when mode=demo", () => {
    const { getByTestId } = render(<DataSourceBanner mode="demo" />);
    expect(getByTestId("data-source-banner").textContent).toMatch(/Demo Mode/);
  });

  it("renders offline label when mode=offline", () => {
    const { getByTestId } = render(<DataSourceBanner mode="offline" />);
    expect(getByTestId("data-source-banner").textContent).toMatch(/백엔드 연결 대기/);
  });

  it("renders mock-virtual label", () => {
    const { getByTestId } = render(<DataSourceBanner mode="mock-virtual" />);
    expect(getByTestId("data-source-banner").textContent).toMatch(/Mock|Virtual/i);
  });

  it("does NOT expose 'Failed to fetch' raw error — converts via friendlyErrorMessage", () => {
    _setDemo(false);
    const { container } = render(
      <DataSourceBanner mode="offline" error="Failed to fetch" />,
    );
    expect(container.textContent).not.toContain("Failed to fetch");
    expect(container.textContent).toMatch(/uvicorn|연결이 끊겼/);
  });

  it("hides raw 'Failed to fetch' even in demo mode", () => {
    _setDemo(true);
    const { container } = render(
      <DataSourceBanner mode="demo" error="Failed to fetch" />,
    );
    // 핵심 invariant: raw 'Failed to fetch'가 사용자에게 노출되지 않는다.
    // (demo vs offline 분기 테스트는 errorMessage.test.js에서 별도 검증.)
    expect(container.textContent).not.toContain("Failed to fetch");
    // 변환된 친절한 안내 문구는 errorMessage.js의 두 분기 중 하나여야 한다.
    expect(container.textContent).toMatch(/uvicorn|GitHub Pages|mock 데이터|연결이 끊겼/);
  });

  it("renders hint when provided", () => {
    const { getByTestId } = render(
      <DataSourceBanner mode="backend" hint="추가 안내" testId="ds-hint-test" />,
    );
    expect(getByTestId("ds-hint-test-hint").textContent).toBe("추가 안내");
  });

  it("compact mode renders inline chip only", () => {
    const { container, getByTestId } = render(
      <DataSourceBanner mode="demo" compact />,
    );
    const el = getByTestId("data-source-banner");
    expect(el.tagName).toBe("SPAN");
    // Compact 모드는 hint / friendly-error 박스를 렌더하지 않음.
    expect(container.querySelector('[data-testid$="-hint"]')).toBeNull();
  });

  it("does NOT render BUY/SELL/HOLD or order buttons", () => {
    const { container } = render(
      <DataSourceBanner mode="offline" error="Failed to fetch" />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/BUY|SELL|HOLD|매수|매도|즉시 주문|Place Order/);
    }
  });
});


describe("<DemoModeBadge>", () => {
  it("returns null when mode=backend (no visual noise)", () => {
    const { container } = render(<DemoModeBadge mode="backend" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders chip when mode=demo", () => {
    const { getByTestId } = render(<DemoModeBadge mode="demo" />);
    expect(getByTestId("demo-mode-badge").textContent).toMatch(/Demo/);
  });

  it("renders chip when mode=mock-virtual", () => {
    const { getByTestId } = render(<DemoModeBadge mode="mock-virtual" />);
    expect(getByTestId("demo-mode-badge").textContent).toMatch(/Mock|Virtual/i);
  });

  it("renders chip when mode=offline", () => {
    const { getByTestId } = render(<DemoModeBadge mode="offline" />);
    expect(getByTestId("demo-mode-badge").textContent).toMatch(/연결 대기/);
  });
});


describe("<BackendDataSourceBanner>", () => {
  it("auto-resolves to backend when no error", () => {
    const { getByTestId } = render(
      <BackendDataSourceBanner loading={false} error="" mode="backend" />,
    );
    expect(getByTestId("data-source-banner").textContent).toMatch(/백엔드 연결됨/);
  });

  it("auto-resolves to offline when error and not demo", () => {
    _setDemo(false);
    const { getByTestId } = render(
      <BackendDataSourceBanner loading={false} error="Failed to fetch" mode="backend" />,
    );
    expect(getByTestId("data-source-banner").textContent).toMatch(/연결 대기/);
  });

  it("auto-resolves to demo when error and demo build", () => {
    _setDemo(true);
    const { getByTestId } = render(
      <BackendDataSourceBanner loading={false} error="Failed to fetch" mode="backend" />,
    );
    expect(getByTestId("data-source-banner").textContent).toMatch(/Demo Mode/);
  });

  it("compact mode returns chip only when not backend", () => {
    _setDemo(false);
    const { getByTestId, container } = render(
      <BackendDataSourceBanner loading={false} error="boom"
                                  mode="backend" compact />,
    );
    const el = getByTestId("demo-mode-badge");
    expect(el.tagName).toBe("SPAN");
    // No raw error
    expect(container.textContent).not.toContain("boom");
  });

  it("compact mode renders nothing when backend (no chip)", () => {
    const { container } = render(
      <BackendDataSourceBanner loading={false} error=""
                                  mode="backend" compact />,
    );
    expect(container.firstChild).toBeNull();
  });
});
