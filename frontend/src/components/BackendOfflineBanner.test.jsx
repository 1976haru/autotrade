import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BackendOfflineBanner } from "./BackendOfflineBanner";


// 213: useBackendStatus는 useEffect로 fetch — 모킹해서 deterministic하게.
const _statusHook = {
  status: null,
  loading: false,
  error: "",
  baseUrl: "http://127.0.0.1:8000",
  viaFallback: false,
};
vi.mock("../store/useBackendStatus", () => ({
  useBackendStatus: () => _statusHook,
}));


function _set(overrides) {
  Object.assign(_statusHook, {
    status: null,
    loading: false,
    error: "",
    baseUrl: "http://127.0.0.1:8000",
    viaFallback: false,
  }, overrides);
}


describe("<BackendOfflineBanner>", () => {
  afterEach(cleanup);

  it("renders nothing while loading", () => {
    _set({ loading: true });
    const { queryByTestId } = render(<BackendOfflineBanner />);
    expect(queryByTestId("backend-offline-banner")).toBeNull();
  });

  it("renders nothing on success (default 8000 port)", () => {
    _set({ status: { default_mode: "SIMULATION" } });
    const { queryByTestId } = render(<BackendOfflineBanner />);
    expect(queryByTestId("backend-offline-banner")).toBeNull();
    expect(queryByTestId("backend-connected-fallback-banner")).toBeNull();
  });

  // fix/frontend-detects-fallback-backend-port: connected + fallback port
  // → 작은 초록 "✅ Backend 연결 완료: :8001" 배지.
  it("shows green connected banner when on fallback port 8001", () => {
    _set({
      status: { default_mode: "PAPER" },
      baseUrl: "http://127.0.0.1:8001",
      viaFallback: true,
    });
    const { getByTestId, queryByTestId } = render(<BackendOfflineBanner />);
    const banner = getByTestId("backend-connected-fallback-banner");
    expect(banner.getAttribute("data-port")).toBe("8001");
    expect(banner.textContent).toContain("Backend 연결 완료");
    expect(banner.textContent).toContain("8001");
    // 빨간 offline 배너 0건.
    expect(queryByTestId("backend-offline-banner")).toBeNull();
  });

  it("shows fallback banner for port 8002 too", () => {
    _set({
      status: { default_mode: "PAPER" },
      baseUrl: "http://127.0.0.1:8002",
      viaFallback: true,
    });
    const { getByTestId } = render(<BackendOfflineBanner />);
    expect(
      getByTestId("backend-connected-fallback-banner").getAttribute("data-port")
    ).toBe("8002");
  });

  it("connected fallback banner stays hidden when viaFallback=false", () => {
    _set({
      status: { default_mode: "PAPER" },
      baseUrl: "http://127.0.0.1:8000",
      viaFallback: false,
    });
    const { queryByTestId } = render(<BackendOfflineBanner />);
    expect(queryByTestId("backend-connected-fallback-banner")).toBeNull();
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

    it("shows backend sidecar log panel via Tauri invoke mock", async () => {
      _enableDesktop();
      window.__TAURI_INTERNALS__ = {
        invoke: vi.fn(async (cmd) => {
          if (cmd === "read_backend_log") {
            return "[1234567890] === Agent Trader sidecar startup ===\n" +
                   "[1234567891] STDOUT: INFO uvicorn running on 127.0.0.1:8000";
          }
          return "";
        }),
      };
      _set({ error: "Failed to fetch" });
      const { getByTestId, findByTestId } = render(<BackendOfflineBanner />);
      fireEvent.click(getByTestId("btn-show-connection-log"));
      expect(getByTestId("connection-log-panel")).toBeTruthy();
      const backendPanel = await findByTestId("backend-log-panel");
      expect(backendPanel).toBeTruthy();
      const content = await findByTestId("backend-log-content");
      expect(content.textContent).toContain("Agent Trader sidecar startup");
      expect(content.textContent).toContain("uvicorn running on 127.0.0.1:8000");
    });

    it("backend log panel sanitizes secret patterns", async () => {
      _enableDesktop();
      window.__TAURI_INTERNALS__ = {
        invoke: vi.fn(async () =>
          "STDOUT: KIS_APP_KEY=abcd1234efgh5678ijkl9999 loaded\n" +
          "STDOUT: ANTHROPIC_API_KEY=sk-ant-AAAAAAAAAAAAAAAA\n"
        ),
      };
      _set({ error: "Failed to fetch" });
      const { getByTestId, findByTestId } = render(<BackendOfflineBanner />);
      fireEvent.click(getByTestId("btn-show-connection-log"));
      const content = await findByTestId("backend-log-content");
      expect(content.textContent).toContain("KIS_APP_KEY=[REDACTED]");
      expect(content.textContent).toContain("ANTHROPIC_API_KEY=[REDACTED]");
      expect(content.textContent).not.toContain("abcd1234efgh5678ijkl9999");
      expect(content.textContent).not.toContain("sk-ant-AAAAAAAAAAAAAAAA");
    });
  });


  // ==========================================================
  // DB-preparing banner — fix/desktop-nonblocking-migration-health
  // ==========================================================
  describe("DB preparing banner (db_ready=false carry)", () => {
    it("shows DB preparing banner when status.db_ready=false (no error)", () => {
      _set({
        status: {
          default_mode: "SIMULATION",
          db_ready: false,
          migration_status: "running",
          migration_started_at: "2026-05-17T09:00:00+00:00",
        },
      });
      const { getByTestId, queryByTestId } = render(<BackendOfflineBanner />);
      const banner = getByTestId("backend-db-preparing-banner");
      expect(banner.getAttribute("data-migration-status")).toBe("running");
      expect(banner.textContent).toContain("초기 DB 준비 중");
      // *offline 배너 0건* — backend 가 살아있다는 표현.
      expect(queryByTestId("backend-offline-banner")).toBeNull();
      expect(queryByTestId("desktop-backend-launching-banner")).toBeNull();
    });

    it("hides DB preparing banner once db_ready=true", () => {
      _set({
        status: {
          default_mode: "PAPER",
          db_ready: true,
          migration_status: "completed",
        },
      });
      const { queryByTestId } = render(<BackendOfflineBanner />);
      expect(queryByTestId("backend-db-preparing-banner")).toBeNull();
    });

    it("hides DB preparing banner for old payload missing db_ready (backwards compat)", () => {
      // db_ready 필드 자체가 없는 옛 payload → *전혀 표시 안 함* (오해 차단).
      _set({ status: { default_mode: "PAPER" } });
      const { queryByTestId } = render(<BackendOfflineBanner />);
      expect(queryByTestId("backend-db-preparing-banner")).toBeNull();
    });

    it("DB preparing banner has no banned phrases (invariant)", () => {
      _set({
        status: { db_ready: false, migration_status: "running" },
      });
      const { container } = render(<BackendOfflineBanner />);
      const text = container.textContent;
      // 실거래 / 주문 트리거 라벨이 본 배너에 들어가면 안 됨.
      const banned = ["Place Order", "지금 매수", "지금 매도", "실거래 시작",
                      "ENABLE_LIVE_TRADING"];
      for (const b of banned) {
        expect(text).not.toContain(b);
      }
    });

    it("DB preparing banner does NOT mask underlying error (error overrides)", () => {
      // backend 가 *완전히 죽었으면* db_ready 가 옛 값 false 일 수 있지만,
      // /api/status 가 fetch 실패하면 error 가 set 됨 — 본 케이스는 평소처럼
      // offline 배너 / desktop 배너 흐름을 보여야 함 (db_ready 분기에 빠지지
      // 않음).
      _set({
        status: { db_ready: false, migration_status: "running" },
        error: "Failed to fetch",
      });
      const { queryByTestId } = render(<BackendOfflineBanner />);
      expect(queryByTestId("backend-db-preparing-banner")).toBeNull();
      // 비-desktop 환경에서는 빨간 backend-offline-banner 가 떠야 함.
      expect(queryByTestId("backend-offline-banner")).toBeTruthy();
    });
  });
});
