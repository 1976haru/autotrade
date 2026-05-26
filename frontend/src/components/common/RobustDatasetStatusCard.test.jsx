/**
 * RobustDatasetStatusCard 테스트 — 상태/품질/그룹/ready/실전금지 + 주문버튼 0 + 경고.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { RobustDatasetStatusCard } from "./RobustDatasetStatusCard";

afterEach(cleanup);

function _report(overrides = {}) {
  return {
    available: true,
    collection_status: "WARN",
    data_quality_status: "WARN",
    symbol_count: 35,
    actual_period: "2024-01-02 ~ 2024-12-30 (245 거래일)",
    trading_days: 245,
    ready_for_robust_backtest: true,
    one_minute_available: false,
    one_minute_availability: "UNAVAILABLE",
    time_split_status: "PASS",
    regime_label_status: "OK",
    symbols_by_group: {
      LARGE_CAP: ["005930", "000660"], MID_CAP: ["105560"],
      HIGH_VOL_THEME: ["042700"], ETF_PROXY: ["069500"],
    },
    warnings: ["중앙 거래일 245 — 일부 부족"],
    next_recommended_task: "후속 작업에서 train/validation/test 분할로 검증",
    live_trading_recommendation: false, real_order_allowed: false,
    is_live_authorization: false, is_order_signal: false,
    kis_order_api_called: false, broker_order_sent: false,
    exe_build_executed: false, contains_secret: false,
    do_not_auto_apply: true, no_profit_guarantee: true,
    ...overrides,
  };
}

function _api(report = _report()) {
  return { robustDatasetStatus: vi.fn(async () => report) };
}

describe("<RobustDatasetStatusCard>", () => {
  it("완료: 품질 status + 종목군 + ready + 실전금지", async () => {
    render(<RobustDatasetStatusCard apiClient={_api()} />);
    await screen.findByTestId("robust-quality");
    expect(screen.getByTestId("robust-quality-status").textContent).toContain("WARN");
    expect(screen.getByTestId("robust-summary").textContent).toContain("LARGE_CAP");
    expect(screen.getByTestId("robust-ready").textContent).toContain("true");
    expect(screen.getByTestId("robust-live").textContent).toContain("false");
  });

  it("기간/메타/1분봉 표시", async () => {
    render(<RobustDatasetStatusCard apiClient={_api()} />);
    await screen.findByTestId("robust-quality");
    expect(screen.getByTestId("robust-period").textContent).toContain("245");
    expect(screen.getByTestId("robust-meta").textContent).toContain("UNAVAILABLE");
    expect(screen.getByTestId("robust-next").textContent).toContain("train/validation/test");
  });

  it("데이터 없음(empty)", async () => {
    render(<RobustDatasetStatusCard apiClient={_api(_report({ available: false }))} />);
    expect(await screen.findByTestId("robust-empty")).toBeTruthy();
    expect(screen.queryByTestId("robust-quality")).toBeNull();
  });

  it("실패", async () => {
    const api = { robustDatasetStatus: vi.fn(async () => { throw new Error("x"); }) };
    render(<RobustDatasetStatusCard apiClient={api} />);
    expect(await screen.findByTestId("robust-error")).toBeTruthy();
  });

  it("주문/실전/적용/자동매매 시작/빌드 버튼 0개 (새로고침·복사만)", async () => {
    const { container } = render(<RobustDatasetStatusCard apiClient={_api()} />);
    await screen.findByTestId("robust-quality");
    const labels = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(labels).toEqual(["상태 새로고침", "상태 복사"]);
    const forbidden = /실전|주문|적용|자동매매 시작|매수|매도|Place Order|빌드 시작|EXE 빌드/;
    for (const t of labels) expect(t).not.toMatch(forbidden);
  });

  it("input/textarea/select 0개", async () => {
    const { container } = render(<RobustDatasetStatusCard apiClient={_api()} />);
    await screen.findByTestId("robust-quality");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("한글 위험 경고 문구 노출 (수집 전용 · 수익 보장 없음)", async () => {
    const { container } = render(<RobustDatasetStatusCard apiClient={_api()} />);
    await screen.findByTestId("robust-warning");
    expect(container.textContent).toContain("데이터 수집·품질검증·메타데이터 전용");
    expect(container.textContent).toContain("수익 보장 없음");
  });

  it("apiClient 미구현 시 empty 처리", async () => {
    render(<RobustDatasetStatusCard apiClient={{}} />);
    expect(await screen.findByTestId("robust-empty")).toBeTruthy();
  });
});
