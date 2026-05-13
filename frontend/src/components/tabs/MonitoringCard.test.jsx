import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MonitoringCard } from "./MonitoringCard";

vi.mock("../../services/backend/client", () => ({
  backendApi: { monitoringMetrics: vi.fn() },
}));


const _SNAPSHOT_OK = {
  overall: "OK",
  metrics: [
    { name: "server",             status: "OK",   value: { uptime_seconds: 100 }, message: "시스템 정상" },
    { name: "database",           status: "OK",   value: { reachable: true }, message: "DB 정상" },
    { name: "api_error_rate",     status: "OK",   value: { error_rate: 0.0 }, message: "API 오류율 정상" },
    { name: "order_failure_rate", status: "OK",   value: { rate: 0.0 }, message: "주문 실패율 정상" },
    { name: "approval_queue",     status: "OK",   value: { pending_count: 0 }, message: "승인 대기 없음" },
    { name: "risk_events",        status: "OK",   value: { count: 0 }, message: "리스크 이벤트 정상 범위" },
    { name: "data_freshness",     status: "OK",   value: { provider: "mock" }, message: "데이터 정상" },
    { name: "notification",       status: "OK",   value: { enabled: false }, message: "알림 비활성" },
  ],
  alerts: [],
  generated_at: new Date().toISOString(),
};


const _SNAPSHOT_CRITICAL = {
  overall: "CRITICAL",
  metrics: [
    ..._SNAPSHOT_OK.metrics.slice(0, 3),
    { name: "order_failure_rate", status: "CRITICAL",
      value: { rate: 0.8, failed: 8, total: 10 },
      message: "주문 실패율 매우 높음" },
    ..._SNAPSHOT_OK.metrics.slice(4),
  ],
  alerts: [
    {
      severity: "CRITICAL", kind: "order_failure_rate",
      title: "[CRITICAL] order_failure_rate",
      message: "주문 실패율 매우 높음",
      dedupe_key: "monitoring:order_failure_rate:CRITICAL",
    },
  ],
  generated_at: new Date().toISOString(),
};


afterEach(cleanup);


describe("MonitoringCard — overall badge", () => {
  it("OK 스냅샷에서 정상 배지를 노출한다", () => {
    const { getAllByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_OK} />,
    );
    const badges = getAllByTestId("status-badge-OK");
    expect(badges.length).toBeGreaterThan(0);
    expect(badges[0].textContent).toBe("정상");
  });

  it("CRITICAL 스냅샷에서 심각 배지를 노출한다", () => {
    const { getAllByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_CRITICAL} />,
    );
    const badges = getAllByTestId("status-badge-CRITICAL");
    expect(badges.length).toBeGreaterThan(0);
    expect(badges[0].textContent).toBe("심각");
  });
});


describe("MonitoringCard — 8개 메트릭 행 노출", () => {
  it("8개 메트릭 모두 행으로 렌더한다", () => {
    const { getByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_OK} />,
    );
    for (const name of [
      "server", "database", "api_error_rate", "order_failure_rate",
      "approval_queue", "risk_events", "data_freshness", "notification",
    ]) {
      expect(getByTestId(`metric-row-${name}`)).toBeTruthy();
    }
  });
});


describe("MonitoringCard — 알림 후보", () => {
  it("알림 후보가 없으면 안내 메시지", () => {
    const { getByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_OK} />,
    );
    expect(getByTestId("monitoring-alerts-empty")).toBeTruthy();
  });

  it("알림 후보가 있으면 행으로 렌더하고 송신 버튼은 0개", () => {
    const { getByTestId, container } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_CRITICAL} />,
    );
    expect(getByTestId("monitoring-alerts-list")).toBeTruthy();
    expect(getByTestId("alert-row-order_failure_rate")).toBeTruthy();
    // 송신 / 무시 / 토글 같은 control 버튼이 *0개* — monitoring은 read-only.
    const buttons = container.querySelectorAll("button");
    expect(buttons.length).toBe(0);
  });
});


describe("MonitoringCard — 모바일 요약", () => {
  it("3개 요약 (시스템 / 데이터 / 주문&리스크)", () => {
    const { getByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_OK} />,
    );
    expect(getByTestId("mobile-summary-system")).toBeTruthy();
    expect(getByTestId("mobile-summary-data")).toBeTruthy();
    expect(getByTestId("mobile-summary-trade")).toBeTruthy();
  });

  it("주문 실패율 CRITICAL이면 주문&리스크 요약이 심각", () => {
    const { getByTestId } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_CRITICAL} />,
    );
    const trade = getByTestId("mobile-summary-trade");
    // 그룹 내부의 가장 나쁜 status가 표시됨 — CRITICAL.
    expect(trade.textContent).toContain("심각");
  });
});


describe("MonitoringCard — invariant", () => {
  it("어떤 시나리오에서도 BUY/SELL/HOLD/긴급정지 토글 버튼 0개", () => {
    const { container } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_CRITICAL} />,
    );
    const text = container.textContent || "";
    for (const banned of ["매수 실행", "매도 실행", "BUY", "SELL", "HOLD",
                          "긴급정지 토글", "LIVE 활성화"]) {
      expect(text.includes(banned)).toBe(false);
    }
  });

  it("Secret 패턴이 화면에 노출되지 않는다", () => {
    const { container } = render(
      <MonitoringCard snapshotOverride={_SNAPSHOT_OK} />,
    );
    const text = (container.textContent || "").toLowerCase();
    for (const needle of [
      "kis_app_key", "kis_app_secret", "anthropic_api_key",
      "telegram_bot_token", "sk-", "bearer ",
    ]) {
      expect(text.includes(needle)).toBe(false);
    }
  });

  it("스냅샷이 null이면 측정 불가로 fallback", () => {
    const { getAllByTestId } = render(
      <MonitoringCard snapshotOverride={{
        overall: "UNKNOWN", metrics: [], alerts: [],
        generated_at: new Date().toISOString(),
      }} />,
    );
    const badges = getAllByTestId("status-badge-UNKNOWN");
    expect(badges.length).toBeGreaterThan(0);
    expect(badges[0].textContent).toBe("측정 불가");
  });
});
