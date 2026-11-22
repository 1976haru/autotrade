import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, waitFor, fireEvent } from "@testing-library/react";

import { IntradaySessionPanel, summarizeEodProgress } from "./IntradaySessionPanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const base = {
  phase: "ENTRY_OPEN", trading_date: "2026-10-22", buy_allowed: true,
  buy_block_reason_code: null, buy_block_reason_message: "신규 매수 가능 시간입니다.",
  schedule: { calendar: "TRADING_DAY", buy_cutoff: "14:30", flatten_start: "15:15", session_close: "15:30" },
  eod_state: "IDLE", eod_reason_code: null, summary: {},
  paper_auto_mode: { mode: "KIS_REALTIME_PAPER_AUTO", price_source: "kis", dry_run: false, smoke_mode: false },
  broker_label: "KIS_PAPER", kis_is_paper: true, enable_live_trading: false,
  background_driver_running: true, previous_incident_trading_date: null,
  last_reconcile_at: null, last_reconcile_ok: false,
};
const api = (d) => ({ kisPaperEodStatus: vi.fn(async () => d), kisPaperEodAcknowledge: vi.fn(async () => ({})) });

describe("IntradaySessionPanel (INTRADAY-FLAT-BY-CLOSE-V1)", () => {
  it("EOD lifecycle distinguishes reconciliation from successful completion", async () => {
    const d = { ...base, lifecycle_state: "RECONCILING" };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-eod-lifecycle").textContent)
      .toContain("잔고·미체결 대조 중"));
  });

  it("서버 일정(14:30/15:15/15:30) + 모의계좌 배지 + 운용모드 표시", async () => {
    const { getByTestId } = render(<IntradaySessionPanel api={api(base)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-buy-cutoff").textContent).toBe("14:30"));
    expect(getByTestId("isp-flatten-start").textContent).toBe("15:15");
    expect(getByTestId("isp-close").textContent).toBe("15:30");
    expect(getByTestId("isp-account-badge").textContent).toContain("모의투자");
    expect(getByTestId("isp-mode").textContent).toContain("KIS 모의 자동주문");
    expect(getByTestId("isp-buy-status").textContent).toContain("신규 매수 가능");
  });

  it("14:30 이후: 신규매수 불가 사유 + 청산 진행률 표시 (서버 값 그대로)", async () => {
    const d = {
      ...base, phase: "EOD_FLATTEN", buy_allowed: false, buy_block_reason_code: "BUY_CUTOFF_ACTIVE",
      buy_block_reason_message: "신규 매수 마감 시각이 지나 신규 매수를 하지 않습니다 (보유 청산만 진행).",
      eod_state: "FLATTENING", eod_reason_code: "EOD_FLATTEN_IN_PROGRESS",
      summary: {
        symbols: { "005930": { managed_qty: 7 }, "000660": { managed_qty: 0 } },
        totals: { managed_qty: 7, open_sell_qty: 3, unconfirmed_eod_qty: 0, program_open_buy_qty: 0,
                  eod_orders_submitted: 2, eod_filled_qty: 4, unmanaged_symbols: 1 },
        last_broker_order_no: "0000012345", last_failure_code: null,
      },
    };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-buy-status").textContent).toContain("신규 매수 불가"));
    expect(getByTestId("isp-eod-state").textContent).toContain("강제청산 진행 중");
    const p = getByTestId("isp-progress").textContent;
    expect(p).toContain("청산 대상 1종목");
    expect(p).toContain("남은 보유 7주");
    expect(p).toContain("미체결 3주");
    expect(p).toContain("주문 접수 2건");
    expect(p).toContain("체결 4주");
    expect(getByTestId("isp-meta").textContent).toContain("0000012345");
  });

  it("가상/dry-run 은 '모의투자 계좌' 로 표시하지 않는다 (실계좌·모의 오인 방지)", async () => {
    const d = { ...base, broker_label: "VIRTUAL/MOCK", paper_auto_mode: { mode: "VIRTUAL_ONLY" } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-account-badge").textContent).toContain("가상"));
  });

  it("FLATTEN_INCOMPLETE 사고 + 확인 버튼 → acknowledge 호출 (주문 0)", async () => {
    const d = { ...base, eod_state: "FLATTEN_INCOMPLETE", previous_incident_trading_date: "2026-10-21" };
    const a = api(d);
    vi.spyOn(window, "prompt").mockReturnValue("운영자");
    const { getByTestId } = render(<IntradaySessionPanel api={a} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-incident").textContent).toContain("2026-10-21"));
    fireEvent.click(getByTestId("isp-ack"));
    await waitFor(() => expect(a.kisPaperEodAcknowledge).toHaveBeenCalledWith({ tradingDate: "2026-10-21", decidedBy: "운영자" }));
  });

  it("background driver 꺼짐 경고 / 휴장일 / 조회 실패 정직 표시", async () => {
    const d = { ...base, background_driver_running: false, schedule: { calendar: "HOLIDAY" } };
    const { getByTestId, unmount } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-driver-off")).toBeTruthy());
    expect(getByTestId("isp-non-trading").textContent).toContain("휴장일");
    unmount();
    const bad = { kisPaperEodStatus: vi.fn(async () => { throw new Error("down"); }) };
    const r2 = render(<IntradaySessionPanel api={bad} pollMs={60_000} />);
    await waitFor(() => expect(r2.getByTestId("isp-failed").textContent).toContain("불러오지 못했어요"));
  });

  it("V2: 15:00 사전점검 시각 + 자동청산 보장 아님 안내 항상 표시", async () => {
    const d = { ...base, schedule: { ...base.schedule, precheck_start: "15:00" } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-precheck").textContent).toBe("15:00"));
    expect(getByTestId("isp-not-guaranteed").textContent).toContain("자동 보장 아님");
  });

  it("V2: 긴급정지 + 보유 → 수동 청산 필요 경고 (서버 판정 그대로)", async () => {
    const d = { ...base, emergency_stop: true, manual_liquidation_required: true,
                program_ledger_holdings: [{ symbol: "005930", quantity: 3 }], eod_state: "BLOCKED" };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-manual-liquidation").textContent).toContain("직접 정리"));
    expect(getByTestId("isp-manual-liquidation").textContent).toContain("1종목");
  });

  it("V2: 미체결 매수 취소 상태 표시 (요청·확인·실패)", async () => {
    const d = { ...base, eod_state: "BUY_CUTOFF_ACTIVE", summary: { buy_cancels: {
      "0000000777": { symbol: "005930", status: "CANCEL_REQUESTED_AWAITING_CONFIRM", remaining: 6 },
      "0000000778": { symbol: "000660", status: "CANCEL_FAILED", remaining: 2 },
    } } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-buy-cancels").textContent).toContain("취소 요청됨"));
    expect(getByTestId("isp-buy-cancels").textContent).toContain("취소 실패");
    expect(getByTestId("isp-buy-cancels").textContent).toContain("잔량 6주");
  });

  it("EXE: 시작 화면 — KIS 설정·리허설·자동매매 상태·보유/미체결 수 표시", async () => {
    const d = { ...base, kis_credentials_present: true, rehearsal_mode: true,
      rehearsal_symbol: "005930", rehearsal_max_buy_qty: 1, auto_loop_state: "PAUSED",
      program_ledger_holdings: [{ symbol: "005930", quantity: 1 }],
      summary: { totals: { open_sell_qty: 1 } } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-kis-conn").textContent).toContain("연결 정보 있음"));
    expect(getByTestId("isp-rehearsal").textContent).toContain("005930 최대 1주");
    expect(getByTestId("isp-loop").textContent).toContain("시작 버튼을 눌러야 신규 매수");
    expect(getByTestId("isp-holdings").textContent).toContain("보유 1종목 · 미체결 1주");
    expect(getByTestId("isp-not-guaranteed").textContent).toContain("트레이로 숨김");
  });

  it("PREFLIGHT: 정지(⏸)와 긴급정지(🛑)의 청산 영향 차이를 항상 표시", async () => {
    const { getByTestId } = render(<IntradaySessionPanel api={api(base)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-stop-vs-emergency").textContent).toContain("청산은 계속"));
    expect(getByTestId("isp-stop-vs-emergency").textContent).toContain("미체결 취소까지 전부 중단");
    // P0-SAFETY-FIX-V1: LEVEL 1 은 청산 계속, LEVEL 2/3·이전 정책만 전부 중단.
    expect(getByTestId("isp-stop-vs-emergency").textContent).toContain("LEVEL 1 = 신규 매수 차단(청산·미체결 매수 취소는 계속)");
    expect(getByTestId("isp-stop-vs-emergency").textContent).toContain("MTS/HTS");
  });

  it("P0: 긴급정지 상태 줄 — 단계·이전 정책 여부·서버 안내 그대로 표시", async () => {
    const d = { ...base, kill_switch: { active: true, level: "LEVEL_1", legacy_strict: true,
      operator_action_ko: "모든 자동 주문·취소가 중단됐습니다." } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-kill-switch").textContent).toContain("LEVEL_1 (이전 정책)"));
    expect(getByTestId("isp-kill-switch").textContent).toContain("모든 자동 주문·취소가 중단");
  });

  it("P0: 긴급정지 OFF 면 상태 줄 없음", async () => {
    const d = { ...base, kill_switch: { active: false, level: "OFF" } };
    const { queryByTestId, getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => getByTestId("isp-stop-vs-emergency"));
    expect(queryByTestId("isp-kill-switch")).toBeNull();
  });

  it("V3: 점검 모드 판정은 '실제 청산 결과 아님' 표시", async () => {
    const d = { ...base, eod_state: "FLAT_CONFIRMED", summary: { verdict_basis: "DRY_RUN_NO_KIS_ORDERS" } };
    const { getByTestId } = render(<IntradaySessionPanel api={api(d)} pollMs={60_000} />);
    await waitFor(() => expect(getByTestId("isp-dryrun-basis").textContent).toContain("실제 청산 결과가 아닙니다"));
  });

  it("summarizeEodProgress 는 서버 totals 만 사용 (추정 0)", () => {
    expect(summarizeEodProgress({})).toEqual({ symbols: 0, heldQty: 0, openQty: 0, submitted: 0, filledQty: 0, unmanaged: 0 });
  });
});
