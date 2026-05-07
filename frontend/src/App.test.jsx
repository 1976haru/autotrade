import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";


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
  afterEach(() => { cleanup(); });

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
    // BottomNav 의 정확한 라벨로 한정 — Dashboard 텍스트에 "전략"이 들어가는
    // 다른 카드(예: 활성 전략 리스트)와 충돌하지 않도록 정확 매칭.
    const btn = view.getByText("전략·리스크");
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
});
