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

vi.mock("./services/backend/client", () => ({
  backendApi: new Proxy({}, {
    get: (_t, prop) => (...args) => _offlineApi[prop](...args),
  }),
  formatBackendErrorDetail: (s) => (typeof s === "string" ? s : ""),
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

  it("Dashboard renders Hero summary + Operator panel + Agent decision hero", async () => {
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("hero-summary")).toBeTruthy();
    });
    // 핵심 카드들 — 백엔드 offline에서도 Empty/Error 상태로 표시.
    expect(view.queryByTestId("agent-decision-hero")).toBeTruthy();
    expect(view.queryByTestId("status-pin-bot")).toBeTruthy();
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
    let view;
    await act(async () => { view = render(<App />); });
    await waitFor(() => {
      expect(view.getByTestId("hero-summary")).toBeTruthy();
    });
    // 핵심 surface (Hero / OperatingLoop / Reconciliation / AgentDecision Hero)
    // 는 friendlyErrorMessage / ErrorState로 변환된다. 일부 보조 카드(예:
    // OperatorPanel '데이터 일부 조회 실패: ...')는 다음 phase backlog.
    const hero       = view.getByTestId("hero-summary");
    expect(hero.textContent).not.toContain("Failed to fetch");
    const agentHero  = view.queryByTestId("agent-decision-hero");
    if (agentHero) expect(agentHero.textContent).not.toContain("Failed to fetch");
  });
});
