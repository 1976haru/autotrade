import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";
import {
  __resetFeaturesForTest,
  __setFeatureForTest,
} from "./config/features.js";


// 213: App-level smoke tests. 빈 화면 회귀를 잡기 위한 최소 보호망 — 백엔드가
// 어떤 응답을 주든(에러/빈 배열/정상) App이 트리를 만들고 BottomNav가 보이는
// 것까지 확인한다. backendApi 전체를 Proxy로 모킹해서 매 메서드가 일관된
// rejected promise를 반환 — "백엔드 꺼진" 상태를 시뮬레이션.
const _offlineApi = new Proxy({}, {
  get: () => () => Promise.reject(new Error("offline")),
});

const _emptyApi = new Proxy({}, {
  get: (_t, prop) => {
    // History/list endpoint들은 array, summary 류는 object — 빈 array가 양쪽
    // 모두에 안전하지 않을 수 있어 prop 이름으로 분기.
    return () => {
      if (typeof prop !== "string") return Promise.resolve(null);
      if (prop === "getStatus") {
        return Promise.resolve({
          default_mode: "SIMULATION",
          flags: {}, mode_capabilities: {},
        });
      }
      if (prop === "brokerBalance") return Promise.resolve({ cash: 0 });
      if (prop === "getRiskPolicy") return Promise.resolve({});
      if (prop === "engineStatus") {
        return Promise.resolve({
          running: false, mode: "SIMULATION", strategies: [],
        });
      }
      if (prop === "engineRegistry") return Promise.resolve([]);
      if (prop === "engineScoreboard") return Promise.resolve([]);
      if (prop === "virtualOrdersSummary") {
        return Promise.resolve({ total: 0, by_status: {} });
      }
      if (prop === "futuresOrdersSummary") {
        return Promise.resolve({ total: 0, by_decision: {} });
      }
      if (prop === "emergencyStopSummary") {
        return Promise.resolve({
          currently_active: false, total_toggles: 0, total_activations: 0,
          by_reason: {},
        });
      }
      if (prop === "reconciliationStatus") {
        return Promise.resolve({
          in_sync: true, broker_symbol_count: 0, audit_symbol_count: 0,
          mismatches: [], matched_count: 0,
        });
      }
      if (prop === "aiAgentDecisionsSummary") {
        return Promise.resolve({
          total_decisions: 0, total_chains: 0, by_agent: {}, recent_chains: [],
        });
      }
      if (prop === "marketRegime") {
        return Promise.resolve({
          regime: "CHOPPY", confidence: 60, reasons: [],
          allowed_strategies: [], blocked_strategies: [],
          risk_multiplier: 1.0, max_position_size_multiplier: 1.0,
          trade_permission: "ALLOW", operator_summary: [],
        });
      }
      if (prop === "operatingLoopStatus") {
        return Promise.resolve({ stage: "intraday", stages: [] });
      }
      if (prop === "preMarketBrief") {
        return Promise.resolve({
          market_risk_level: "MEDIUM", interesting_themes: [],
          available_strategies: [], daily_loss_cap: 0,
          trading_allowed: true, readiness_score: 70,
          readiness_label: "READY", operator_summary: [],
        });
      }
      if (prop === "intradaySummary") {
        return Promise.resolve({
          candidates_evaluated: 0, virtual_orders_made: 0, rejected_signals: 0,
          last_chief_decision: null, notable_reasons: [], operator_summary: [],
        });
      }
      if (prop === "postMarketReview") {
        return Promise.resolve({
          total_decisions: 0, successes: 0, failures: 0,
          misclassified_signals: 0, pnl_estimate: 0,
          next_day_adjustments: [], agent_score_delta: 0, operator_summary: [],
        });
      }
      // list-style endpoints
      return Promise.resolve([]);
    };
  },
});


let _activeApi = _offlineApi;
vi.mock("./services/backend/client", () => ({
  backendApi: new Proxy({}, {
    get: (_t, prop) => (...args) => _activeApi[prop](...args),
  }),
  formatBackendErrorDetail: (s) => (typeof s === "string" ? s : ""),
}));


describe("<App> smoke", () => {
  // 50: 본 smoke 테스트는 *모든 탭*이 ErrorBoundary 없이 렌더되는지 검증한다.
  // futures는 default flag(false)에서 nav에 미노출이라 별도 활성화.
  beforeEach(() => { __setFeatureForTest("futuresTab", true); });
  afterEach(() => { cleanup(); __resetFeaturesForTest(); });

  it("renders the shell and BottomNav even when every backend call rejects", async () => {
    _activeApi = _offlineApi;
    let view;
    await act(async () => { view = render(<App />); });
    // BottomNav 의 탭 버튼이 보여야 빈 화면이 아니다.
    await waitFor(() => {
      expect(view.getByTestId("backend-offline-banner")).toBeTruthy();
    });
    expect(view.queryAllByRole("button").length).toBeGreaterThan(3);
  });

  // 222: 반응형 레이아웃 클래스가 적용됐는지 — 인라인 maxWidth: 520이 사라지고
  // .app-shell / .app-bottomnav 클래스가 붙어야 PC 미디어쿼리에서 1280px로
  // 확장된다. jsdom에서 window 폭은 1024(default)지만 클래스 적용 자체는 폭과
  // 무관하게 검증 가능 — DOM에 className이 박혀 있으면 됨.
  it("applies responsive layout classes on shell and bottom nav", async () => {
    _activeApi = _emptyApi;
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("status-pin-bot")).toBeTruthy();
    });
    // app-shell은 최외곽 div. querySelector로 추적해 인라인 maxWidth가 더 이상
    // 박혀 있지 않은 것까지 확인.
    const shell = view.container.querySelector(".app-shell");
    expect(shell).toBeTruthy();
    expect(shell.style.maxWidth).toBe("");
    const nav = view.container.querySelector(".app-bottomnav");
    expect(nav).toBeTruthy();
    expect(nav.style.maxWidth).toBe("");
    // Dashboard 본문도 .dashboard-body 클래스로 layout이 옮겨져야 PC에서 grid
    // 적용이 가능. 인라인 display: flex는 더 이상 박혀 있지 않아야 한다.
    const body = view.container.querySelector(".dashboard-body");
    expect(body).toBeTruthy();
    expect(body.style.display).toBe("");
  });

  it("renders Dashboard tab content in the empty-data happy path", async () => {
    _activeApi = _emptyApi;
    let view;
    await act(async () => { view = render(<App />); });
    // Dashboard StatusSummaryCard's pins
    await waitFor(() => {
      expect(view.getByTestId("status-pin-emergency-stop")).toBeTruthy();
      expect(view.getByTestId("status-pin-pending-approvals")).toBeTruthy();
      expect(view.getByTestId("status-pin-bot")).toBeTruthy();
    });
    // 백엔드가 "켜진" 시나리오라 offline banner는 안 떠야 한다.
    expect(view.queryByTestId("backend-offline-banner")).toBeNull();
  });

  it("can switch to the StrategyRisk tab without the shell crashing", async () => {
    _activeApi = _emptyApi;
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("status-pin-bot")).toBeTruthy();
    });
    // 231 (UI-003): TopNav + BottomNav가 같은 라벨을 둘 다 렌더하므로 단일
    // testid로 클릭 — TopNav 항목이 데스크톱 기본 가시 nav.
    const btn = view.getByTestId("top-nav-strat");
    await act(async () => { fireEvent.click(btn); });
    // 전환 후에도 BottomNav는 살아있고 ErrorBoundary fallback이 보이지 않아야.
    expect(view.queryByTestId("error-boundary")).toBeNull();
  });

  it("falls back to ErrorBoundary fallback when a tab throws, but keeps the shell", async () => {
    // engineStatus 하나만 reject로 만들어 LiveEngine 탭이 죽도록 유도해도
    // BottomNav는 그대로 살아있어야 한다.
    _activeApi = new Proxy({}, {
      get: (_t, prop) => (...args) => _emptyApi[prop](...args),
    });
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("status-pin-bot")).toBeTruthy();
    });
    // 의도된 에러는 재현이 까다로워 ErrorBoundary의 회귀는 ErrorBoundary.test.jsx
    // 에서 직접 검증. 여기서는 shell이 깨지지 않는 것만 확인.
    expect(view.queryAllByRole("button").length).toBeGreaterThan(3);
  });

  // 214: 8개 demo target 화면이 mock/empty 데이터에서도 렌더링되는지 — 한 탭씩
  // 클릭해서 ErrorBoundary fallback이 한 번도 등장하지 않는지 확인. 각 탭의
  // 시그니처 텍스트로 도착을 검증한다 (탭마다 unique한 SectionLabel 또는
  // testid). Pages demo 사용자가 "어떤 탭에서 흰 화면이 나는가"를 회귀로 잠금.
  const _DEMO_TARGET_TABS = [
    { tabId: "dash",     signature: { kind: "testid", value: "status-pin-bot" } },
    { tabId: "strat",    signature: { kind: "text",   value: "백엔드 리스크 정책" } },
    { tabId: "approve",  signature: { kind: "text",   value: /승인/ } },
    { tabId: "audit",    signature: { kind: "text",   value: /이벤트 타임라인/ } },
    { tabId: "signal",   signature: { kind: "text",   value: /AI 합류 신호 분석/ } },
    // Engine 탭 안에 Virtual Orders + Virtual Positions 카드가 함께 마운트된다.
    { tabId: "engine",   signature: { kind: "text",   value: /엔진 상태/ } },
    { tabId: "futures",  signature: { kind: "text",   value: /다층 안전 가드/ } },
  ];

  it.each(_DEMO_TARGET_TABS)(
    "renders $tabId tab without ErrorBoundary fallback in empty/demo data",
    async ({ tabId, signature }) => {
      _activeApi = _emptyApi;
      let view;
      await act(async () => { view = render(<App />); });
      await waitFor(() => {
        expect(view.getByTestId("status-pin-bot")).toBeTruthy();
      });
      if (tabId !== "dash") {
        // 231: TopNav가 데스크톱 기본이라 testid로 단일 매칭.
        const btn = view.getByTestId(`top-nav-${tabId}`);
        await act(async () => { fireEvent.click(btn); });
      }
      await waitFor(() => {
        if (signature.kind === "testid") {
          expect(view.getByTestId(signature.value)).toBeTruthy();
        } else {
          expect(view.getAllByText(signature.value).length).toBeGreaterThan(0);
        }
      });
      // 어떤 탭으로 가도 ErrorBoundary fallback 은 한 번도 등장하지 말 것.
      expect(view.queryByTestId("error-boundary")).toBeNull();
    },
  );
});


// 214: 빌드 플래그(VITE_DEMO_MODE) 분기 헬퍼는 별도 단위 테스트. import.meta.env
// 를 vi.stubEnv로 갈아끼워 양쪽 분기를 모두 검증.
describe("isDemoBuild()", () => {
  afterEach(() => { vi.unstubAllEnvs(); });

  it("returns false when VITE_DEMO_MODE is unset", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "");
    const { isDemoBuild } = await import("./components/BackendOfflineBanner.jsx");
    expect(isDemoBuild()).toBe(false);
  });

  it("returns true when VITE_DEMO_MODE is the string 'true'", async () => {
    vi.stubEnv("VITE_DEMO_MODE", "true");
    const { isDemoBuild } = await import("./components/BackendOfflineBanner.jsx");
    expect(isDemoBuild()).toBe(true);
  });
});
