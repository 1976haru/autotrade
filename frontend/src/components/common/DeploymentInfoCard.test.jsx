import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DeploymentInfoCard,
  _detectAccessModeForTest,
} from "./DeploymentInfoCard";


// Mock isDemoBuild — DataSourceBanner test pattern과 동일.
vi.mock("../BackendOfflineBanner", async () => {
  const actual = await vi.importActual("../BackendOfflineBanner");
  return { ...actual, isDemoBuild: vi.fn(() => false) };
});
vi.mock("../../store/useBackendStatus", () => ({
  useBackendStatus: vi.fn(() => ({
    status: { default_mode: "SIMULATION" },
    loading: false, error: "",
  })),
}));

import { isDemoBuild } from "../BackendOfflineBanner";
import { useBackendStatus } from "../../store/useBackendStatus";


afterEach(() => {
  cleanup();
  vi.mocked(isDemoBuild).mockReturnValue(false);
});


describe("_detectAccessMode", () => {
  it("returns 'local' for localhost / 127.0.0.1", () => {
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "localhost" })).toBe("local");
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "127.0.0.1" })).toBe("local");
  });

  it("returns 'lan' for private network ranges", () => {
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "192.168.0.49" })).toBe("lan");
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "10.0.0.5" })).toBe("lan");
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "172.16.5.1" })).toBe("lan");
  });

  it("returns 'tailscale' for 100.64.0.0/10 CGNAT range", () => {
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "100.64.0.5" })).toBe("tailscale");
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "100.100.50.1" })).toBe("tailscale");
  });

  it("returns 'pages-demo' when isDemoBuild=true", () => {
    expect(_detectAccessModeForTest({ isDemo: true, backendOk: false,
                                        hostname: "1976haru.github.io" }))
      .toBe("pages-demo");
  });

  it("returns 'offline' when backend not ok and not demo", () => {
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: false,
                                        hostname: "127.0.0.1" })).toBe("offline");
  });

  it("returns 'external' for public IPs", () => {
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "8.8.8.8" })).toBe("external");
    expect(_detectAccessModeForTest({ isDemo: false, backendOk: true,
                                        hostname: "example.com" })).toBe("external");
  });
});


describe("<DeploymentInfoCard>", () => {
  it("renders deployment policy list with 5 absolute rules", () => {
    const { getByTestId } = render(<DeploymentInfoCard />);
    const list = getByTestId("deployment-policy-list");
    expect(list.textContent).toMatch(/포트포워딩.*금지/);
    expect(list.textContent).toMatch(/Tailscale/);
    expect(list.textContent).toMatch(/API key.*Secret.*계좌번호.*\.env/);
    expect(list.textContent).toMatch(/베타테스터.*\.env 공유 금지|각자 자기 자격증명/);
    expect(list.textContent).toMatch(/실거래.*LIVE.*옵트인/);
  });

  it("shows mode badge based on hostname (jsdom uses localhost)", () => {
    const { getByTestId } = render(<DeploymentInfoCard />);
    const badge = getByTestId("deployment-mode-badge");
    // jsdom default hostname은 'localhost' — local 모드 표시 예상
    expect(badge.textContent).toMatch(/로컬|LAN|GitHub Pages|연결 대기/);
  });

  it("shows pages-demo when isDemoBuild=true", () => {
    vi.mocked(isDemoBuild).mockReturnValue(true);
    const { getByTestId } = render(<DeploymentInfoCard />);
    expect(getByTestId("deployment-mode-badge").textContent)
      .toMatch(/GitHub Pages Demo/);
  });

  it("shows offline state when backend status has error", () => {
    vi.mocked(useBackendStatus).mockReturnValue({
      status: null, loading: false, error: "Failed to fetch",
    });
    const { getByTestId } = render(<DeploymentInfoCard />);
    expect(getByTestId("deployment-mode-badge").textContent)
      .toMatch(/연결 대기/);
  });

  it("does NOT contain BUY/SELL/HOLD or 즉시 주문 buttons", () => {
    const { container } = render(<DeploymentInfoCard />);
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const t = b.textContent || "";
      expect(t).not.toMatch(/BUY|SELL|HOLD|매수 실행|매도 실행|즉시 주문/);
    }
  });

  it("mentions update plan + future auto-update", () => {
    const { container } = render(<DeploymentInfoCard />);
    expect(container.textContent).toMatch(/업데이트 안내/);
    expect(container.textContent).toMatch(/자동 업데이트는 후속/);
  });

  it("references all 4 deployment docs", () => {
    const { container } = render(<DeploymentInfoCard />);
    const text = container.textContent;
    expect(text).toContain("deployment_strategy.md");
    expect(text).toContain("mobile_access_guide.md");
    expect(text).toContain("local_security_policy.md");
    expect(text).toContain("auto_update_plan.md");
  });

  it("displays 4 access modes hint (Local/LAN/Tailscale/Pages)", () => {
    const { container } = render(<DeploymentInfoCard />);
    const text = container.textContent;
    expect(text).toMatch(/Local/);
    expect(text).toMatch(/LAN/);
    expect(text).toMatch(/Tailscale/);
    expect(text).toMatch(/GitHub Pages|Pages Demo/);
  });
});
