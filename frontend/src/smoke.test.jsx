/**
 * 236 (UI-008): UI smoke tests — 핵심 탭이 backend offline + demo 데이터에서도
 * ErrorBoundary fallback 없이 렌더되는지 잠금. App.test.jsx의 같은 시나리오를
 * 더 명시적으로 분리해 manual checklist와 1:1 매핑되도록.
 *
 * 본 파일은 *수동* checklist (docs/ui_smoke_test_report.md)의 기계 판독 가능
 * 미러 — backend 미연결 / Pages demo 환경에서 이 테스트가 통과하면 사용자가
 * 페이지를 열었을 때 흰 화면을 보지 않을 가능성이 매우 높다.
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App.jsx";


// 모든 backend 호출은 Pages 데모처럼 reject — friendlyErrorMessage / ErrorState
// 가 어떻게 동작하는지 동시 검증.
const _offlineApi = new Proxy({}, {
  get: () => () => Promise.reject(new Error("Failed to fetch")),
});

// fix/frontend-app-test-backend-client-mock: useBackendStatus 가 client 에서
// getBackendBaseUrl / discoverBackendBaseUrl / setBackendBaseUrl 을 import 한다.
// mock 에 누락되면 App 전체가 ErrorBoundary 로 떨어진다.
vi.mock("./services/backend/client", () => ({
  backendApi: new Proxy({}, {
    get: (_t, prop) => (...args) => _offlineApi[prop](...args),
  }),
  formatBackendErrorDetail: (s) => (typeof s === "string" ? s : ""),
  getBackendBaseUrl: () => "http://127.0.0.1:8000",
  setBackendBaseUrl: () => {},
  resetBackendBaseUrl: () => {},
  discoverBackendBaseUrl: async () => ({
    ok: true,
    baseUrl: "http://127.0.0.1:8000",
    port: 8000,
    viaHealth: false,
  }),
  backendFetch: async () => null,
}));


describe("UI smoke (backend offline)", () => {
  afterEach(cleanup);

  it("App renders shell with both navs visible (TopNav 데스크톱 + BottomNav 모바일)", async () => {
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("top-nav")).toBeTruthy();
    });
    // TopNav 11 tabs + BottomNav 11 tabs = 22 nav buttons + 다른 버튼들.
    expect(view.queryAllByRole("button").length).toBeGreaterThan(15);
    // ErrorBoundary fallback이 등장하면 안 됨.
    expect(view.queryByTestId("error-boundary")).toBeNull();
  });

  it("기본 홈(ReferenceHome) 핵심 영역이 offline에서도 렌더된다", async () => {
    // V7: 기본 'dash' 탭은 전문가 Dashboard(hero-summary)가 아니라 ReferenceHome.
    //   hero-summary 등 전문가 surface는 '전문가 보기'로 이동(아래 별도 테스트가 검증).
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("reference-home")).toBeTruthy();
    });
    // 에이전트 영역 + 계좌정보 영역이 offline에서도 Empty/Error 상태로 표시.
    expect(view.queryByTestId("refhome-agent")).toBeTruthy();
  });

  it.each([
    ["strat",   "전략·리스크"],
    ["approve", "승인 대기"],
    ["audit",   "감사 로그"],
    ["signal",  "AI"],
    ["config",  "설정"],
  ])(
    "tab %s renders without ErrorBoundary fallback",
    async (tabId, _label) => {
      let view;
      await act(async () => { view = render(<App />); });
      await waitFor(() => {
        expect(view.getByTestId("top-nav")).toBeTruthy();
      });
      const btn = view.getByTestId(`top-nav-${tabId}`);
      await act(async () => { fireEvent.click(btn); });
      // 어떤 탭으로 이동해도 흰 화면 / ErrorBoundary가 나타나면 안 된다.
      expect(view.queryByTestId("error-boundary")).toBeNull();
    },
  );

  it("user-facing primary surfaces hide raw 'Failed to fetch'", async () => {
    // UI 개편: 기본 홈이 ReferenceHome(관제판)으로 교체됨. 기존 Dashboard
    // surface(hero-summary 등)는 "전문가 보기"로 이동. 본 스모크는 *기본 홈*이
    // 백엔드 offline 에서도 raw 'Failed to fetch'를 노출하지 않음을 검증한다
    // (ReferenceHome 은 Promise.allSettled + graceful empty 로 처리).
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("reference-home")).toBeTruthy();
    });
    const home = view.getByTestId("reference-home");
    expect(home.textContent).not.toContain("Failed to fetch");
  });
});
