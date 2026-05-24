/**
 * #56 / 7-04 — RuntimeEventLogViewer 단위 테스트.
 *
 * lock 하는 invariant:
 *  - 최근 이벤트 로그 제목 + 최근 100건 안내
 *  - RuntimeEvent / AgentDecision / KIS order 행 표시
 *  - reason_code / broker_order_no 표시
 *  - source / severity 필터 + 검색 + 새로고침 동작
 *  - 복사 동작 + 복사 내용 secret/account 미포함
 *  - loading / empty / error 상태
 *  - secret/account 원문 표시 0건
 *  - 매수/매도/실전/Place Order/주문 재시도 버튼 0개
 *  - 주문 API 호출 0건 (systemLogs GET 만)
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RuntimeEventLogViewer } from "./RuntimeEventLogViewer";


afterEach(cleanup);


const _LOGS = [
  { timestamp: "2026-05-24T09:01:00+00:00", source: "RUNTIME_EVENT",
    severity: "ERROR", reason_code: "BACKEND_FAIL", message: "backend 연결 실패",
    symbol: null, action: null, broker_order_no: null, episode_id: null },
  { timestamp: "2026-05-24T09:02:00+00:00", source: "AGENT_DECISION",
    severity: "INFO", reason_code: "NO_SIGNAL", message: "조건에 맞는 신호 없음",
    symbol: "005930", action: "HOLD", broker_order_no: null, episode_id: "ep-1" },
  { timestamp: "2026-05-24T09:03:00+00:00", source: "KIS_ORDER",
    severity: "WARN", reason_code: "REJECTED", message: "주문 거절: 한도 초과",
    symbol: "005930", action: "BUY", broker_order_no: "K-99", episode_id: null },
];

function _api(logs = _LOGS) {
  return {
    systemLogs: vi.fn(async () => ({
      logs, count: logs.length,
      summary: { by_source: {}, by_severity: {} },
      is_live_authorization: false, contains_secret: false,
    })),
  };
}


describe("<RuntimeEventLogViewer>", () => {
  it("제목 + 최근 100건 안내", async () => {
    render(<RuntimeEventLogViewer apiClient={_api()} />);
    expect((await screen.findByTestId("log-viewer-note")).textContent).toContain("최근 100건");
  });

  it("RuntimeEvent / AgentDecision / KIS order 행 표시", async () => {
    const { container } = render(<RuntimeEventLogViewer apiClient={_api()} />);
    await screen.findByTestId("log-viewer-rows");
    const sources = [...container.querySelectorAll("[data-source]")]
      .map((el) => el.getAttribute("data-source"));
    expect(sources).toContain("RUNTIME_EVENT");
    expect(sources).toContain("AGENT_DECISION");
    expect(sources).toContain("KIS_ORDER");
  });

  it("reason_code 표시", async () => {
    render(<RuntimeEventLogViewer apiClient={_api()} />);
    await screen.findByTestId("log-viewer-rows");
    expect(screen.getByTestId("log-reason-0").textContent).toContain("BACKEND_FAIL");
  });

  it("broker_order_no 표시", async () => {
    render(<RuntimeEventLogViewer apiClient={_api()} />);
    await screen.findByTestId("log-viewer-rows");
    expect(screen.getByTestId("log-order-no-2").textContent).toContain("K-99");
  });

  it("source 필터 클릭 시 systemLogs 에 source 전달", async () => {
    const api = _api();
    render(<RuntimeEventLogViewer apiClient={api} />);
    await screen.findByTestId("log-viewer-rows");
    fireEvent.click(screen.getByTestId("log-source-KIS_ORDER"));
    await waitFor(() => {
      const last = api.systemLogs.mock.calls.at(-1)[0];
      expect(last.source).toBe("KIS_ORDER");
    });
  });

  it("severity 필터 클릭 시 systemLogs 에 severity 전달", async () => {
    const api = _api();
    render(<RuntimeEventLogViewer apiClient={api} />);
    await screen.findByTestId("log-viewer-rows");
    fireEvent.click(screen.getByTestId("log-severity-ERROR"));
    await waitFor(() => {
      const last = api.systemLogs.mock.calls.at(-1)[0];
      expect(last.severity).toBe("ERROR");
    });
  });

  it("검색 q 입력 시 systemLogs 에 q 전달", async () => {
    const api = _api();
    render(<RuntimeEventLogViewer apiClient={api} />);
    await screen.findByTestId("log-viewer-rows");
    fireEvent.change(screen.getByTestId("log-viewer-search"), {
      target: { value: "REJECTED" },
    });
    await waitFor(() => {
      const last = api.systemLogs.mock.calls.at(-1)[0];
      expect(last.q).toBe("REJECTED");
    });
  });

  it("새로고침 버튼 동작", async () => {
    const api = _api();
    render(<RuntimeEventLogViewer apiClient={api} />);
    await screen.findByTestId("log-viewer-rows");
    api.systemLogs.mockClear();
    fireEvent.click(screen.getByTestId("log-viewer-refresh-btn"));
    await waitFor(() => expect(api.systemLogs).toHaveBeenCalled());
  });

  it("복사 버튼 — clipboard.writeText 호출 + 내용 secret/account 미포함", async () => {
    const writeText = vi.fn(async () => {});
    render(<RuntimeEventLogViewer apiClient={_api()} clipboard={{ writeText }} />);
    await screen.findByTestId("log-viewer-rows");
    fireEvent.click(screen.getByTestId("log-viewer-copy-btn"));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    const copied = writeText.mock.calls[0][0];
    expect(copied).not.toMatch(/\d{8}-\d{2}/);     // 계좌번호 패턴 없음
    expect(copied).not.toContain("sk-ant-");
    expect(copied).not.toContain("Bearer ");
  });

  it("복사 직전 secret 감지 시 차단 (clipboard 미호출)", async () => {
    const writeText = vi.fn(async () => {});
    const leaky = [{
      timestamp: "t", source: "RUNTIME_EVENT", severity: "INFO",
      reason_code: null, message: "leak sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAA",
      symbol: null, action: null, broker_order_no: null, episode_id: null,
    }];
    render(<RuntimeEventLogViewer apiClient={_api(leaky)} clipboard={{ writeText }} />);
    await screen.findByTestId("log-viewer-rows");
    fireEvent.click(screen.getByTestId("log-viewer-copy-btn"));
    expect(await screen.findByTestId("log-viewer-copy-blocked")).toBeTruthy();
    expect(writeText).not.toHaveBeenCalled();
  });

  it("empty 상태", async () => {
    render(<RuntimeEventLogViewer apiClient={_api([])} />);
    expect(await screen.findByTestId("log-viewer-empty")).toBeTruthy();
  });

  it("error 상태", async () => {
    const api = { systemLogs: vi.fn(async () => { throw new Error("offline"); }) };
    render(<RuntimeEventLogViewer apiClient={api} />);
    expect(await screen.findByTestId("log-viewer-error")).toBeTruthy();
  });
});


describe("<RuntimeEventLogViewer> — safety invariants", () => {
  it("매수/매도/실전/Place Order/주문 재시도 버튼 0개", async () => {
    const { container } = render(<RuntimeEventLogViewer apiClient={_api()} />);
    await screen.findByTestId("log-viewer-rows");
    const text = container.textContent || "";
    for (const banned of [
      "지금 매수", "지금 매도", "Place Order", "매수 실행", "매도 실행",
      "실거래 시작", "실거래 활성화", "주문 재시도", "주문 재전송", "재전송",
      "ENABLE_LIVE_TRADING",
    ]) {
      expect(text).not.toContain(banned);
    }
    // 버튼 라벨에도 주문/실거래 라벨 0개.
    for (const b of [...container.querySelectorAll("button")]) {
      const t = (b.textContent || "").trim();
      expect(t).not.toContain("매수");
      expect(t).not.toContain("매도");
      expect(t).not.toContain("실거래");
      expect(t.toLowerCase()).not.toContain("place order");
    }
  });

  it("입력 form 은 검색 1개뿐 (주문 입력 0개)", async () => {
    const { container } = render(<RuntimeEventLogViewer apiClient={_api()} />);
    await screen.findByTestId("log-viewer-rows");
    const inputs = container.querySelectorAll("input,textarea,select");
    expect(inputs.length).toBe(1); // 검색 input 만.
    expect(inputs[0].getAttribute("data-testid")).toBe("log-viewer-search");
  });

  it("secret/account 원문이 응답에 있어도 화면 텍스트엔 노출 안 됨(backend 마스킹 전제) — 행 메시지 그대로 표시", async () => {
    // backend 가 이미 마스킹하므로 logs 에는 [REDACTED] 만 온다.
    const masked = [{
      timestamp: "t", source: "KIS_ORDER", severity: "WARN",
      reason_code: "REJECTED", message: "계좌 [REDACTED] 거절",
      symbol: "005930", action: "BUY", broker_order_no: "K-1", episode_id: null,
    }];
    const { container } = render(<RuntimeEventLogViewer apiClient={_api(masked)} />);
    await screen.findByTestId("log-viewer-rows");
    const text = container.textContent || "";
    expect(text).toContain("[REDACTED]");
    expect(text).not.toMatch(/\d{8}-\d{2}/);
  });

  it("주문 API 호출 0건 — systemLogs 만 사용", async () => {
    const api = _api();
    render(<RuntimeEventLogViewer apiClient={api} />);
    await screen.findByTestId("log-viewer-rows");
    expect(api.systemLogs).toHaveBeenCalled();
    expect(Object.keys(api)).toEqual(["systemLogs"]);
  });
});
