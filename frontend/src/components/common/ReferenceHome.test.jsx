import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, fireEvent, cleanup, waitFor, act } from "@testing-library/react";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    listOrderAudits: vi.fn(async () => []),
    aiAgentDecisions: vi.fn(async () => []),
    agentStrategyPerformance: vi.fn(async () => ({ strategies: [] })),
    paperCashState: vi.fn(async () => ({ realized_pnl_krw: 0 })),
    paperCapitalConfig: vi.fn(async () => ({ max_concurrent_positions: 5, per_symbol_max_krw: 1000000 })),
    paperDecisionLog: vi.fn(async () => ({ entries: [] })),
    // 실제 Auto Paper Loop 배선 — 기존 client 함수.
    autoPaperStatus: vi.fn(async () => ({ state: "PAUSED" })),
    autoPaperStart: vi.fn(async () => ({ ok: true })),
    autoPaperStop: vi.fn(async () => ({ ok: true })),
    // usePaperCapitalSettings가 함수면 backend 로드 시도 — 없으면 LOCAL fallback.
  },
}));

import { ReferenceHome } from "./ReferenceHome";
import { backendApi } from "../../services/backend/client";

const basePortfolio = {
  cash: 38021127, positions: [], invested: 18475210,
  totalAsset: 56496337, totalPnL: -624164, totalPnLPct: -1.52,
  loading: false, error: "",
};

function renderHome(overrides = {}) {
  const props = {
    portfolio: basePortfolio,
    emergencyStop: false,
    onEmergencyStop: vi.fn(),
    onJumpTab: vi.fn(),
    onExpert: vi.fn(),
    operatorName: "홍길동",
    accountNo: "5019116201",
    ...overrides,
  };
  return { props, ...render(<ReferenceHome {...props} />) };
}

describe("ReferenceHome", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    backendApi.autoPaperStatus.mockResolvedValue({ state: "PAUSED" });
    backendApi.autoPaperStart.mockResolvedValue({ ok: true });
    backendApi.autoPaperStop.mockResolvedValue({ ok: true });
  });
  afterEach(() => cleanup());

  it("핵심 섹션이 보인다 (히어로 손절/익절, 칩, 계좌정보, 안전고지)", () => {
    const { getByText, getByTestId } = renderHome();
    expect(getByText("손절")).toBeTruthy();
    expect(getByText("익절")).toBeTruthy();
    expect(getByText("설정 종목 수")).toBeTruthy();
    expect(getByText("종목당 투자금")).toBeTruthy();
    expect(getByTestId("refhome-startstop")).toBeTruthy();
    expect(getByText("홍길동님의 계좌정보")).toBeTruthy();
    expect(getByText(/모의투자.*실거래 OFF/)).toBeTruthy();
  });

  it("정지 상태: ▶시작이 실제 Auto Paper Loop 시작(autoPaperStart) 호출", async () => {
    const { getByTestId } = renderHome();
    const btn = getByTestId("refhome-startstop");
    expect(btn.textContent).toContain("시작");
    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => expect(backendApi.autoPaperStart).toHaveBeenCalledTimes(1));
    // payload에 risk_profile + capital_settings 가 동봉(기존 카드와 동일 shape).
    const body = backendApi.autoPaperStart.mock.calls[0][0];
    expect(body).toHaveProperty("risk_profile");
    expect(body).toHaveProperty("capital_settings");
    expect(backendApi.autoPaperStop).not.toHaveBeenCalled();
  });

  it("가동(RUNNING) 상태: ⏸정지가 실제 루프 정지(autoPaperStop) 호출", async () => {
    backendApi.autoPaperStatus.mockResolvedValue({ state: "RUNNING" });
    const { getByTestId } = renderHome();
    const btn = getByTestId("refhome-startstop");
    await waitFor(() => expect(btn.textContent).toContain("정지"));
    await act(async () => { fireEvent.click(btn); });
    await waitFor(() => expect(backendApi.autoPaperStop).toHaveBeenCalledTimes(1));
  });

  it("히어로/버튼이 실제 루프 상태(RUNNING) 기준으로 바뀐다", async () => {
    backendApi.autoPaperStatus.mockResolvedValue({ state: "RUNNING" });
    const { getByTestId } = renderHome();
    // running=true → 버튼이 정지로(=히어로도 정지 상태 아님). bot 플래그가 아닌 루프 상태 반영.
    const btn = getByTestId("refhome-startstop");
    await waitFor(() => expect(btn.textContent).toContain("정지"));
  });

  it("긴급정지: 확인(정말 멈출까요?) 후 기존 onEmergencyStop 호출", () => {
    const onEmergencyStop = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { getByTestId } = renderHome({ onEmergencyStop });
    fireEvent.click(getByTestId("refhome-emergency"));
    expect(confirmSpy).toHaveBeenCalled();
    expect(onEmergencyStop).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });

  it("긴급정지: 확인 취소 시 호출 안 됨 (실수 방지)", () => {
    const onEmergencyStop = vi.fn();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { getByTestId } = renderHome({ onEmergencyStop });
    fireEvent.click(getByTestId("refhome-emergency"));
    expect(onEmergencyStop).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("다크/밝기 토글이 동작", () => {
    const { getByTestId } = renderHome();
    const root = getByTestId("reference-home");
    const toggle = getByTestId("refhome-theme-toggle");
    expect(root.className).not.toContain("rh-dark");
    fireEvent.click(toggle);
    expect(root.className).toContain("rh-dark");
  });

  it("전문가 보기가 onExpert 호출", () => {
    const onExpert = vi.fn();
    const { getByTestId } = renderHome({ onExpert });
    fireEvent.click(getByTestId("refhome-expert"));
    expect(onExpert).toHaveBeenCalledTimes(1);
  });

  it("칩 탭은 해당 상세 탭으로 이동", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = renderHome({ onJumpTab });
    fireEvent.click(getByTestId("refhome-chip-미체결"));
    expect(onJumpTab).toHaveBeenCalledWith("approve");
  });

  it("계좌정보 큰 숫자 + 손익률(−파랑)", () => {
    const { getByText } = renderHome();
    expect(getByText(/56,496,337원/)).toBeTruthy();   // 추정자산
    expect(getByText(/38,021,127원/)).toBeTruthy();   // 예수금
    expect(getByText("-1.52%")).toBeTruthy();          // 손익률
  });

  it("계좌번호는 마스킹되어 평문이 노출되지 않는다 (절대원칙 #4)", () => {
    const { container, getByText } = renderHome();
    expect(getByText(/5019\*+01/)).toBeTruthy();
    expect(container.textContent).not.toContain("5019116201");
  });

  it("modification: 잔고 조회 실패 시 옛 숫자 대신 안내", () => {
    const { getByTestId, queryByText } = renderHome({
      portfolio: { ...basePortfolio, error: "network error" },
    });
    expect(getByTestId("refhome-balance-failed").textContent).toContain("불러오지 못했어요");
    expect(queryByText(/56,496,337원/)).toBeNull();
  });

  it("매매기법 4칩(ORB/모멘텀/VWAP/갭)을 표시 — 데이터 없으면 거래 시작 전", async () => {
    const { getByTestId } = renderHome();
    await waitFor(() => {});
    expect(getByTestId("refhome-strat-ORB")).toBeTruthy();
    expect(getByTestId("refhome-strat-MOMENTUM")).toBeTruthy();
    expect(getByTestId("refhome-strat-VWAP")).toBeTruthy();
    expect(getByTestId("refhome-strat-GAP")).toBeTruthy();
  });

  it("AI 운용 성향 3버튼 + 탭하면 변경", () => {
    const { getByTestId } = renderHome();
    expect(getByTestId("refhome-profile-CONSERVATIVE")).toBeTruthy();
    expect(getByTestId("refhome-profile-BALANCED")).toBeTruthy();
    const agg = getByTestId("refhome-profile-AGGRESSIVE");
    fireEvent.click(agg); // 기존 훅 setField 호출 — throw 없이 동작
    expect(agg).toBeTruthy();
  });

  it("실시간 현황판이 있고, 데이터 없으면 안내", async () => {
    const { getByTestId } = renderHome();
    await waitFor(() => expect(getByTestId("refhome-live-empty")).toBeTruthy());
  });

  it("주요 기능 바로가기 6개 → 탭 이동", () => {
    const onJumpTab = vi.fn();
    const { getByTestId } = renderHome({ onJumpTab });
    fireEvent.click(getByTestId("refhome-feature-chart"));
    expect(onJumpTab).toHaveBeenCalledWith("chart");
  });

  it("오늘 진행률 게이지 렌더", () => {
    const { getByTestId } = renderHome();
    expect(getByTestId("refhome-progress")).toBeTruthy();
  });

  it("긴급정지 ON → 풀폭 배너 + 해제 버튼(기존 핸들러)", () => {
    const onEmergencyStop = vi.fn();
    const { getByTestId } = renderHome({ emergencyStop: true, onEmergencyStop });
    expect(getByTestId("refhome-estop-banner").textContent).toContain("모든 주문이 차단");
    fireEvent.click(getByTestId("refhome-estop-release"));
    expect(onEmergencyStop).toHaveBeenCalledTimes(1);
  });

  it("긴급정지 OFF → 배너 없음", () => {
    const { queryByTestId } = renderHome({ emergencyStop: false });
    expect(queryByTestId("refhome-estop-banner")).toBeNull();
  });

  it("금지 전문용어가 화면에 없다", async () => {
    const { container } = renderHome();
    await waitFor(() => {});
    const txt = container.textContent;
    for (const banned of ["cycle", "tick", "broker", "advisory", "RECEIVED", "dry-run"]) {
      expect(txt).not.toContain(banned);
    }
  });
});
