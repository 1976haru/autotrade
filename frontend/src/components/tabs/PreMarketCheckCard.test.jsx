import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PreMarketCheckCard } from "./PreMarketCheckCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: {
    preMarketCheckGet:  vi.fn(),
    preMarketCheckPost: vi.fn(),
  },
}));


const _READY = {
  mode: "PAPER",
  verdict: "READY_TO_START",
  start_allowed: true,
  items: [
    { name: "api", category: "api", status: "PASS", required: true, message: "OK", detail: {} },
    { name: "db",  category: "db",  status: "PASS", required: true, message: "OK", detail: {} },
    { name: "broker_paper", category: "broker", status: "PASS", required: true, message: "OK", detail: {} },
    { name: "watchlist", category: "watchlist", status: "PASS", required: true, message: "10 종목", detail: {} },
    { name: "notification", category: "notification", status: "PASS", required: false, message: "OK", detail: {} },
  ],
  failed_required: [],
  warnings: [],
  required_actions: [],
  manual_ack_recorded: false,
  manual_ack_by: "",
  manual_ack_note: "",
  is_order_signal: false,
  live_flag_changed: false,
  mode_changed: false,
  generated_at: new Date().toISOString(),
};


const _BLOCKED = {
  mode: "PAPER",
  verdict: "DO_NOT_START",
  start_allowed: false,
  items: [
    { name: "api", category: "api", status: "PASS", required: true, message: "OK", detail: {} },
    { name: "broker_paper", category: "broker", status: "FAIL", required: true,
      message: "PAPER broker 준비 안 됨", detail: {} },
    { name: "kill_switch", category: "kill_switch", status: "FAIL", required: true,
      message: "emergency_stop 활성", detail: {} },
  ],
  failed_required: ["broker_paper", "kill_switch"],
  warnings: [],
  required_actions: ["required FAIL 항목을 모두 해결 후 재점검."],
  manual_ack_recorded: false,
  manual_ack_by: "",
  manual_ack_note: "",
  is_order_signal: false,
  live_flag_changed: false,
  mode_changed: false,
  generated_at: new Date().toISOString(),
};


const _WARN = {
  ..._READY,
  verdict: "WARN_BUT_START_ALLOWED",
  warnings: ["data_freshness: stale 종목 2개 존재"],
};


afterEach(cleanup);


describe("PreMarketCheckCard", () => {
  it("READY_TO_START 에서 '오늘 자동운용 가능' 헤드라인", () => {
    const { getByTestId } = render(
      <PreMarketCheckCard resultOverride={_READY} />,
    );
    const head = getByTestId("pre-market-headline");
    expect(head.textContent).toContain("오늘 자동운용 가능");
    expect(head.textContent).toContain("start_allowed = true");
  });

  it("DO_NOT_START 에서 '시작 금지' 헤드라인 + 실패 항목 표시", () => {
    const { getByTestId } = render(
      <PreMarketCheckCard resultOverride={_BLOCKED} />,
    );
    expect(getByTestId("pre-market-headline").textContent).toContain("시작 금지");
    expect(getByTestId("pre-market-failed").textContent).toContain("broker_paper");
    expect(getByTestId("pre-market-failed").textContent).toContain("kill_switch");
  });

  it("WARN 에서 '주의 필요' 헤드라인 + 경고 목록", () => {
    const { getByTestId } = render(
      <PreMarketCheckCard resultOverride={_WARN} />,
    );
    expect(getByTestId("pre-market-headline").textContent).toContain("주의 필요");
    expect(getByTestId("pre-market-warnings").textContent).toContain("stale");
  });

  it("disclaimer 가 영구 노출 + start_allowed 우회 불가 명시", () => {
    const { getByTestId, rerender } = render(
      <PreMarketCheckCard resultOverride={_READY} />,
    );
    let d = getByTestId("pre-market-disclaimer").textContent;
    expect(d).toContain("안전 점검");
    expect(d).toContain("우회하지 않습니다");
    expect(d).toContain("BotControl");

    rerender(<PreMarketCheckCard resultOverride={_BLOCKED} />);
    d = getByTestId("pre-market-disclaimer").textContent;
    expect(d).toContain("우회하지 않습니다");
  });

  it("'확인했습니다' 버튼은 UI 상태만 바꾸고 start_allowed 불변", () => {
    const { getByTestId, queryByTestId } = render(
      <PreMarketCheckCard resultOverride={_BLOCKED} />,
    );
    expect(queryByTestId("pre-market-ack-note")).toBeNull();
    fireEvent.click(getByTestId("pre-market-ack-btn"));
    const note = getByTestId("pre-market-ack-note");
    expect(note.textContent).toContain("required FAIL 이");
    expect(note.textContent).toContain("ack 와 무관하게");
    // result(BLOCKED) 그대로 — start_allowed=false 유지.
    expect(getByTestId("pre-market-headline").textContent).toContain("시작 금지");
  });

  it("자동매매 시작 / mode 변경 / flag 토글 라벨 버튼 0개", () => {
    const { container } = render(
      <PreMarketCheckCard resultOverride={_BLOCKED} />,
    );
    const buttons = container.querySelectorAll("button");
    for (const b of buttons) {
      const txt = (b.textContent || "").trim();
      for (const banned of [
        "자동매매 시작",
        "지금 시작",
        "Start Bot",
        "Start Trading",
        "mode 변경",
        "활성화 토글",
        "ENABLE_LIVE_TRADING",
        "ENABLE_AI_EXECUTION",
        "Place Order",
        "실거래 활성화",
      ]) {
        expect(txt.includes(banned)).toBe(false);
      }
    }
  });

  it("BUY / SELL / HOLD / 긴급정지 토글 문구 0건", () => {
    const { container } = render(
      <PreMarketCheckCard resultOverride={_BLOCKED} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY signal", "SELL signal",
                          "HOLD signal", "긴급정지 토글"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴 노출 0건", () => {
    const { container } = render(
      <PreMarketCheckCard resultOverride={_READY} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("세부 항목 펼치기 토글", () => {
    const { getByTestId, queryByTestId } = render(
      <PreMarketCheckCard resultOverride={_READY} />,
    );
    expect(queryByTestId("pre-market-items")).toBeNull();
    fireEvent.click(getByTestId("pre-market-toggle-detail-btn"));
    expect(getByTestId("pre-market-items").textContent).toContain("api");
    expect(getByTestId("pre-market-items").textContent).toContain("watchlist");
  });

  it("점검 / 확인 버튼 라벨 정확", () => {
    const { getByTestId } = render(
      <PreMarketCheckCard resultOverride={_READY} />,
    );
    expect(getByTestId("pre-market-recheck-btn").textContent.trim()).toBe("다시 점검");
    expect(getByTestId("pre-market-ack-btn").textContent.trim()).toBe("확인했습니다");
  });
});
