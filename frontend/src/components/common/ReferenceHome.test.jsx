import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, fireEvent, cleanup, waitFor, act } from "@testing-library/react";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    listOrderAudits: vi.fn(async () => []),
    aiAgentDecisions: vi.fn(async () => []),
    agentStrategyPerformance: vi.fn(async () => ({ strategies: [] })),
    paperCashState: vi.fn(async () => ({ realized_pnl_krw: 0 })),
    paperCapitalConfig: vi.fn(async () => ({ max_concurrent_positions: 5, per_symbol_max_krw: 1000000 })),
    positionsLive: vi.fn(async () => ({ available: true, positions: [], fetched_at_kst: "09:05" })),
    positionSellAll: vi.fn(async () => ({ status: "SUBMITTED", broker_order_no: "X", submitted_at_kst: "09:06", message: "주문을 보냈어요" })),
    performanceGet: vi.fn(async () => ({
      no_data: true, small_sample: false, win_rate: null, payoff_ratio: null,
      net_pnl_krw: 0, win_count: 0, loss_count: 0, period_start_kst: "2026-06-05",
      market: { available: false, reason: "MARKET_NOT_FETCHED" }, comparison: null,
    })),
    runtimeConfigGet: vi.fn(async () => ({
      max_concurrent_positions: { value: 5, min: 1, max: 10 },
      per_stock_budget: { value: 1000000, min: 100000, max: 10000000 },
      daily_buy_limit_krw: 3000000,
      active_profile: { value: "balanced", source: "env", options: ["conservative", "balanced", "aggressive"],
        effective: { effective_min_confidence: 0.6, max_risk_flags: 1 } },
      profiles_effective: {
        conservative: { effective_min_confidence: 0.7, max_risk_flags: 0 },
        balanced: { effective_min_confidence: 0.6, max_risk_flags: 1 },
        aggressive: { effective_min_confidence: 0.6, max_risk_flags: 2 },
      },
    })),
    runtimeProfilePut: vi.fn(async () => ({ active_profile: { value: "aggressive" } })),
    performanceByTechnique: vi.fn(async () => ({ no_data: true, techniques: [], active_profile: "balanced" })),
    runtimeConfigPut: vi.fn(async (b) => ({
      max_concurrent_positions: { value: b.max_concurrent_positions ?? 5, min: 1, max: 10 },
      per_stock_budget: { value: b.per_stock_budget ?? 1000000, min: 100000, max: 10000000 },
      daily_buy_limit_krw: 3000000,
    })),
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
    const { getByText, getAllByText, getByTestId } = renderHome();
    expect(getByText("손절")).toBeTruthy();
    expect(getByText("익절")).toBeTruthy();
    expect(getByText("설정 종목 수")).toBeTruthy();
    // R3: 히어로 요약 + 런타임 설정 카드 둘 다 '종목당 투자금' 라벨을 가질 수 있음.
    expect(getAllByText("종목당 투자금").length).toBeGreaterThanOrEqual(1);
    expect(getByTestId("refhome-startstop")).toBeTruthy();
    expect(getByText("홍길동님의 계좌정보")).toBeTruthy();
    // U2: 푸터는 일상 한국어 — 환경변수/영문 플래그(KIS_IS_PAPER 등) 노출 금지.
    expect(getByText(/모의투자 모드 — 실제 돈이 나가지 않아요/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/KIS_IS_PAPER|실거래 OFF/);
  });

  it("D3: 전략 집계 없음 → '집계 전', 있음 → '신호 N개'(실제 0과 구분)", async () => {
    // 데이터 없음(episodes_analyzed 0/없음) → '집계 전' 안내 노출.
    const r1 = renderHome();
    expect(await r1.findByTestId("refhome-strat-nodata")).toBeTruthy();
    cleanup();
    // 데이터 있음 → 신호 N개, nodata 안내 사라짐.
    backendApi.agentStrategyPerformance.mockResolvedValueOnce({
      episodes_analyzed: 12,
      strategies: [{ strategy: "ORB", decision_count: 9, buy_vote_count: 3, sell_vote_count: 1, hold_vote_count: 5 }],
    });
    const r2 = renderHome();
    // U5: 신호 = buy(3)+sell(1) = 4 (decision_count 9 = 평가횟수 아님).
    await waitFor(() => expect(r2.getByTestId("refhome-strat-ORB").textContent).toContain("신호 4개"));
    expect(r2.queryByTestId("refhome-strat-nodata")).toBeNull();
  });

  it("U4: '설정 종목 수' = config 실효값(effective_max_concurrent_positions), 별도 store 값 아님", async () => {
    backendApi.paperCapitalConfig.mockResolvedValueOnce({
      max_concurrent_positions: 3,             // 별도 capital-config store(어긋남)
      effective_max_concurrent_positions: 5,   // 실행이 실제 강제하는 config 값
      per_symbol_max_krw: 1_000_000,
    });
    const { findByTestId } = renderHome();
    const cell = await findByTestId("refhome-max-symbols");
    await waitFor(() => expect(cell.textContent).toContain("5개"));
    expect(cell.textContent).not.toContain("3개");
  });

  it("S2: 성향 전환 카드가 렌더되고 활성 성향(안정형) 강조 — rtConfig 기준", async () => {
    const { findByTestId } = renderHome();
    expect(await findByTestId("profile-switch")).toBeTruthy();
    const bal = await findByTestId("profile-tab-balanced");
    await waitFor(() => expect(bal.getAttribute("aria-pressed")).toBe("true"));
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

  it("S5: 기법 성적표 카드가 성향 카드 하단에 렌더된다", async () => {
    const { findByTestId } = renderHome();
    expect(await findByTestId("technique-card")).toBeTruthy();
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
