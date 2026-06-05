import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, fireEvent, cleanup, waitFor } from "@testing-library/react";

// backendApi는 mount 시 네트워크를 치므로 mock — 홈 로직(핸들러 재사용·문구)만 검증.
vi.mock("../../services/backend/client", () => ({
  backendApi: {
    listOrderAudits: vi.fn(async () => []),
    aiAgentDecisions: vi.fn(async () => []),
  },
}));

import { SimpleHome } from "./SimpleHome";

const basePortfolio = {
  cash: 6056042, positions: [], invested: 0,
  totalAsset: 9820000, totalPnL: -12000, totalPnLPct: -0.1,
  loading: false, error: "",
};

function renderHome(overrides = {}) {
  const props = {
    portfolio: basePortfolio,
    bot: { running: false },
    botControls: { start: vi.fn(), stop: vi.fn() },
    emergencyStop: false,
    onEmergencyStop: vi.fn(),
    onJumpTab: vi.fn(),
    onExpert: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<SimpleHome {...props} />) };
}

describe("SimpleHome", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => cleanup());

  it("7섹션 핵심이 보인다 (상태/내돈/버튼/보유/오늘/AI/안전고지)", () => {
    const { getByTestId, getByText } = renderHome();
    expect(getByTestId("simple-home-hero")).toBeTruthy();
    expect(getByText("총 자산")).toBeTruthy();
    expect(getByTestId("simple-home-startstop")).toBeTruthy();
    expect(getByText("보유 종목")).toBeTruthy();
    expect(getByText("오늘 한 일")).toBeTruthy();
    expect(getByText("AI 한마디")).toBeTruthy();
    expect(getByText(/모의투자.*실거래 OFF/)).toBeTruthy();
  });

  it("정지 상태: ▶ 시작 버튼 클릭은 기존 start 핸들러 호출", () => {
    const start = vi.fn(), stop = vi.fn();
    const { getByTestId } = renderHome({ bot: { running: false }, botControls: { start, stop } });
    const btn = getByTestId("simple-home-startstop");
    expect(btn.textContent).toContain("시작");
    fireEvent.click(btn);
    expect(start).toHaveBeenCalledTimes(1);
    expect(stop).not.toHaveBeenCalled();
  });

  it("가동 상태: ⏹ 정지 버튼 클릭은 기존 stop 핸들러 호출", () => {
    const start = vi.fn(), stop = vi.fn();
    const { getByTestId } = renderHome({ bot: { running: true }, botControls: { start, stop } });
    const btn = getByTestId("simple-home-startstop");
    expect(btn.textContent).toContain("정지");
    fireEvent.click(btn);
    expect(stop).toHaveBeenCalledTimes(1);
  });

  it("긴급 정지 클릭은 기존 onEmergencyStop 호출", () => {
    const onEmergencyStop = vi.fn();
    const { getByTestId } = renderHome({ onEmergencyStop });
    fireEvent.click(getByTestId("simple-home-emergency"));
    expect(onEmergencyStop).toHaveBeenCalledTimes(1);
  });

  it("전문가 보기 클릭은 onExpert 호출", () => {
    const onExpert = vi.fn();
    const { getByTestId } = renderHome({ onExpert });
    fireEvent.click(getByTestId("simple-home-expert"));
    expect(onExpert).toHaveBeenCalledTimes(1);
  });

  it("modification #1: 잔고 조회 실패 시 옛 숫자 대신 '불러오지 못했어요'", () => {
    const { getByTestId, queryByText } = renderHome({
      portfolio: { ...basePortfolio, error: "network error" },
    });
    expect(getByTestId("simple-home-balance-failed").textContent).toContain("불러오지 못했어요");
    // 죽은 총자산 숫자를 그대로 노출하지 않는다.
    expect(queryByText(/9,820,000/)).toBeNull();
  });

  it("정상 잔고는 총자산·현금을 보여준다", () => {
    const { getByText } = renderHome();
    expect(getByText(/9,820,000/)).toBeTruthy();
    expect(getByText(/6,056,042원/)).toBeTruthy();
  });

  it("보유 종목 출처 배지(봇 매수/기존 보유)를 표시", () => {
    const { getByText } = renderHome({
      portfolio: {
        ...basePortfolio,
        positions: [{ code: "005930", name: "삼성전자", qty: 1, avg: 100, cur: 110 }],
      },
    });
    expect(getByText("삼성전자")).toBeTruthy();
    expect(getByText("기존 보유")).toBeTruthy(); // 봇 체결 기록 없음 → 기존 보유
  });

  it("금지 전문용어가 화면에 없다 (cycle/tick/broker/advisory/영문상태코드)", async () => {
    const { container } = renderHome();
    await waitFor(() => {});
    const txt = container.textContent;
    for (const banned of ["cycle", "tick", "broker", "advisory", "RECEIVED", "TRADING", "STOPPED", "BLOCKED"]) {
      expect(txt).not.toContain(banned);
    }
  });
});
