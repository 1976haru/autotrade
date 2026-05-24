/**
 * #53 / 7-01 — BackendSidecarStatusCard 단위 테스트.
 *
 * lock 하는 invariant:
 *  - reachable true → "Backend API 연결됨", false → "Backend API 연결 실패"
 *  - 연결됨 / 연결 실패 동시 표시 0건
 *  - sidecar / diagnostics / db / kis 상태 분리 표시
 *  - 연결 실패 시 진단·DB·KIS 는 "확인 불가" + 단일 안내 배너
 *  - secret / 계좌번호 원문 표시 0건
 *  - 매수 / 매도 / 실거래 / Place Order 버튼 0개, 입력 form 0개
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { BackendSidecarStatusCard } from "./BackendSidecarStatusCard";


afterEach(cleanup);


const _RAW_OK = {
  backend_api_reachable: true,
  sidecar_status: "UNKNOWN",
  diagnostics_status: "OK",
  db_status: "OK",
  kis_paper_readiness: "READY",
  checked_at: "2026-05-24T00:00:00+00:00",
  last_error_message: null,
  is_live_authorization: false,
  contains_secret: false,
};

function _api(payload = _RAW_OK) {
  return { exeStatus: vi.fn(async () => payload) };
}

function _failApi(message = "Failed to fetch") {
  return { exeStatus: vi.fn(async () => { throw new Error(message); }) };
}


describe("<BackendSidecarStatusCard> — reachable", () => {
  it("backend_api_reachable=true → 'Backend API 연결됨'", async () => {
    render(<BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    const row = await screen.findByTestId("exe-status-backend");
    expect(row.textContent).toContain("Backend API 연결됨");
    expect(row.textContent).not.toContain("연결 실패");
  });

  it("diagnostics OK → '진단 상태 정상'", async () => {
    render(<BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-diagnostics")).textContent)
      .toContain("진단 상태 정상");
  });

  it("db OK → 'DB 정상'", async () => {
    render(<BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-db")).textContent)
      .toContain("DB 정상");
  });

  it("kis_paper_readiness READY 표시", async () => {
    render(<BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-kis-paper")).textContent)
      .toContain("READY");
  });

  it("kis_paper_readiness BLOCKED 표시", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_api({ ..._RAW_OK, kis_paper_readiness: "BLOCKED" })}
      pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-kis-paper")).textContent)
      .toContain("BLOCKED");
  });

  it("diagnostics FAIL → '진단 상태 실패'", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_api({ ..._RAW_OK, diagnostics_status: "FAIL" })}
      pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-diagnostics")).textContent)
      .toContain("진단 상태 실패");
  });

  it("checked_at 표시", async () => {
    render(<BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-checked-at")).textContent)
      .toContain("마지막 확인 시각");
  });

  it("desktop + reachable → 'Sidecar 실행 중'", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_api()} pollIntervalMs={0} desktop={true} />);
    expect((await screen.findByTestId("exe-status-sidecar")).textContent)
      .toContain("Sidecar 실행 중");
  });

  it("non-desktop → 'Sidecar 확인 불가'", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_api()} pollIntervalMs={0} desktop={false} />);
    expect((await screen.findByTestId("exe-status-sidecar")).textContent)
      .toContain("Sidecar 확인 불가");
  });

  it("sidecarStatus override → 'Sidecar 중지'", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_api()} pollIntervalMs={0} desktop={true}
      sidecarStatus="STOPPED" />);
    expect((await screen.findByTestId("exe-status-sidecar")).textContent)
      .toContain("Sidecar 중지");
  });
});


describe("<BackendSidecarStatusCard> — unreachable (연결 실패)", () => {
  it("exeStatus 실패 → 'Backend API 연결 실패'", async () => {
    render(<BackendSidecarStatusCard apiClient={_failApi()} pollIntervalMs={0} />);
    const row = await screen.findByTestId("exe-status-backend");
    expect(row.textContent).toContain("Backend API 연결 실패");
    expect(row.textContent).not.toContain("연결됨");
  });

  it("연결 실패 시 진단/DB/KIS 모두 '확인 불가'", async () => {
    render(<BackendSidecarStatusCard apiClient={_failApi()} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-diagnostics")).textContent)
      .toContain("확인 불가");
    expect(screen.getByTestId("exe-status-db").textContent).toContain("확인 불가");
    expect(screen.getByTestId("exe-status-kis-paper").textContent)
      .toContain("확인 불가");
  });

  it("연결 실패 시 단일 안내 배너 표시", async () => {
    render(<BackendSidecarStatusCard apiClient={_failApi()} pollIntervalMs={0} />);
    const banner = await screen.findByTestId("exe-status-unreachable-banner");
    expect(banner.textContent)
      .toContain("Backend가 연결되지 않아 진단 상태를 확인할 수 없습니다");
  });

  it("연결 실패 시 오류 메시지 표시", async () => {
    render(<BackendSidecarStatusCard
      apiClient={_failApi("timeout 8000ms")} pollIntervalMs={0} />);
    expect((await screen.findByTestId("exe-status-error")).textContent)
      .toContain("timeout 8000ms");
  });

  it("연결됨/연결 실패가 DOM 어디에도 동시 표시되지 않음", async () => {
    const { container } = render(
      <BackendSidecarStatusCard apiClient={_failApi()} pollIntervalMs={0} />);
    await screen.findByTestId("exe-status-backend");
    const text = container.textContent || "";
    expect(text).toContain("Backend API 연결 실패");
    expect(text).not.toContain("Backend API 연결됨");
  });
});


describe("<BackendSidecarStatusCard> — loading + safety invariants", () => {
  it("loading 상태 표시", async () => {
    // 영원히 pending 인 promise → 로딩 표시 유지.
    const api = { exeStatus: vi.fn(() => new Promise(() => {})) };
    render(<BackendSidecarStatusCard apiClient={api} pollIntervalMs={0} />);
    expect(await screen.findByTestId("exe-status-loading")).toBeTruthy();
  });

  it("매수/매도/실거래/Place Order 버튼 0개", async () => {
    const { container } = render(
      <BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("exe-status-backend");
    expect(container.querySelectorAll("button").length).toBe(0);
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화", "ENABLE_LIVE_TRADING",
    ]) {
      expect(text).not.toContain(banned);
    }
  });

  it("입력 form(input/textarea/select) 0개", async () => {
    const { container } = render(
      <BackendSidecarStatusCard apiClient={_api()} pollIntervalMs={0} />);
    await screen.findByTestId("exe-status-backend");
    expect(container.querySelectorAll("input,textarea,select").length).toBe(0);
  });

  it("secret / 계좌번호 원문이 응답에 있어도 표시되지 않음", async () => {
    const { container } = render(
      <BackendSidecarStatusCard
        apiClient={_api({
          ..._RAW_OK,
          kis_app_secret: "LEAKSECRET123",
          kis_account_no: "98765432-11",
        })}
        pollIntervalMs={0} />);
    await screen.findByTestId("exe-status-backend");
    const text = container.textContent || "";
    expect(text).not.toContain("LEAKSECRET123");
    expect(text).not.toContain("98765432");
  });
});
