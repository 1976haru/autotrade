/**
 * #55 / 7-03 — PortfolioSourceCard 단위 테스트.
 *
 * lock 하는 invariant:
 *  - source / status / reason_code / cash / total_asset / position_count /
 *    last_updated 표시.
 *  - **API 실패 시 0원으로 표시하지 않고 "확인 불가" + 조회 실패 안내.**
 *  - 실제 0원(status=OK, cash=0)은 0원 표시.
 *  - Paper 모의 포트폴리오와 KIS 모의 계좌를 별도 섹션으로 분리.
 *  - 조회 실패는 0원이 아니라는 경고 문구.
 *  - 매수/매도/실전/Place Order 버튼 0개, 입력 form 0개.
 *  - account/secret 원문 표시 0건.
 */

import { afterEach, describe, it, expect, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PortfolioSourceCard } from "./PortfolioSourceCard";

afterEach(cleanup);

function _api(payload) {
  return { portfolioSource: vi.fn(async () => payload) };
}

const _PAPER_OK = {
  source: "PAPER_SIMULATED",
  status: "OK",
  reason_code: "PORTFOLIO_SOURCE_PAPER_SIMULATED",
  message_ko: "Paper 모의 포트폴리오 기준입니다.",
  cash: 9_025_000,
  total_asset: 10_000_000,
  position_count: 2,
  positions: [{ symbol: "005930" }],
  last_updated: "2026-05-24T01:00:00+00:00",
  value_available: true,
  attempted_source: "PAPER_SIMULATED",
};

const _KIS_UNAVAILABLE = {
  source: "UNAVAILABLE",
  status: "NOT_CONFIGURED",
  reason_code: "PORTFOLIO_KIS_PAPER_NOT_CONFIGURED",
  message_ko: "KIS 모의 계좌 잔고 조회가 아직 연결되지 않았습니다. 잔고 0원이 아닙니다.",
  cash: null,
  total_asset: null,
  position_count: null,
  positions: [],
  last_updated: "2026-05-24T01:00:00+00:00",
  value_available: false,
  attempted_source: "KIS_PAPER_ACCOUNT",
};

function _report(paper, kis) {
  return {
    primary_source: paper.source,
    generated_at: "2026-05-24T01:00:00+00:00",
    paper_simulated: paper,
    kis_paper_account: kis,
    snapshots: [paper, kis],
    is_live_authorization: false,
    contains_secret: false,
    ok: true,
  };
}

describe("<PortfolioSourceCard>", () => {
  it("Paper source / status / 현금 / 총자산 / 포지션 / 갱신시각 표시", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    expect((await screen.findByTestId("portfolio-source-paper-source")).textContent)
      .toContain("PAPER_SIMULATED");
    expect(screen.getByTestId("portfolio-source-paper-status").textContent).toContain("OK");
    expect(screen.getByTestId("portfolio-source-paper-cash").textContent).toContain("9,025,000");
    expect(screen.getByTestId("portfolio-source-paper-total-asset").textContent).toContain("10,000,000");
    expect(screen.getByTestId("portfolio-source-paper-positions").textContent).toContain("2종목");
    expect(screen.getByTestId("portfolio-source-paper-last-updated").textContent)
      .toContain("2026-05-24");
  });

  it("KIS 조회 실패 시 0원이 아니라 '확인 불가' 표시", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    const cash = (await screen.findByTestId("portfolio-source-kis-cash")).textContent;
    const total = screen.getByTestId("portfolio-source-kis-total-asset").textContent;
    expect(cash).toContain("확인 불가");
    expect(total).toContain("확인 불가");
    // 0원으로 표시되면 안 된다.
    expect(cash).not.toContain("0원");
    expect(total).not.toContain("0원");
  });

  it("KIS 조회 실패 안내(실제 잔고 0원 아님) 표시", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    const failure = (await screen.findByTestId("portfolio-source-kis-failure")).textContent;
    expect(failure).toContain("실제 잔고 0원이 아닙니다");
  });

  it("실제 0원(status=OK, cash=0)은 0원 표시", async () => {
    const paperZero = { ..._PAPER_OK, cash: 0, total_asset: 0, position_count: 0, value_available: true };
    render(<PortfolioSourceCard apiClient={_api(_report(paperZero, _KIS_UNAVAILABLE))} />);
    expect((await screen.findByTestId("portfolio-source-paper-cash")).textContent).toContain("0원");
  });

  it("Paper / KIS 섹션을 분리해서 표시 (source 혼합 금지)", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    await screen.findByTestId("portfolio-source-paper-section");
    expect(screen.getByTestId("portfolio-source-kis-section")).toBeTruthy();
    expect(screen.getByTestId("portfolio-source-paper-section").getAttribute("data-source"))
      .toBe("PAPER_SIMULATED");
    expect(screen.getByTestId("portfolio-source-kis-section").getAttribute("data-source"))
      .toBe("UNAVAILABLE");
  });

  it("조회 실패는 0원이 아니라는 경고 문구 상시 표시", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    expect((await screen.findByTestId("portfolio-source-zero-warning")).textContent)
      .toContain("0원이 아닙니다");
  });

  it("KIS 섹션 헤더는 KIS_PAPER_ACCOUNT(attempted_source) 로 표시", async () => {
    render(<PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    expect((await screen.findByTestId("portfolio-source-kis-source")).textContent)
      .toContain("KIS_PAPER_ACCOUNT");
  });

  it("API 전체 실패 시 에러 안내 + 0원 아님 문구", async () => {
    const api = { portfolioSource: vi.fn(async () => { throw new Error("network"); }) };
    render(<PortfolioSourceCard apiClient={api} />);
    const err = await screen.findByTestId("portfolio-source-error");
    expect(err.textContent).toContain("0원이 아닙니다");
  });

  it("매수/매도/실전/Place Order 버튼 0개, 입력 form 0개 (자동새로고침 토글 1 허용)", async () => {
    const { container } = render(
      <PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    await screen.findByTestId("portfolio-source-paper-section");
    // KIS-PAPER-FULL-LIFECYCLE E: AutoRefreshFooter 가 추가됨 — 자동 ON 상태(default)에서는
    // button 0, *checkbox 1개* (자동 새로고침 토글) 만 허용.
    expect(container.querySelectorAll("button").length).toBe(0);
    const inputs = container.querySelectorAll("input, textarea, select");
    expect(inputs.length).toBe(1);
    expect(inputs[0].getAttribute("type")).toBe("checkbox");
    // 실행 가능한 매수/매도/실전 라벨 버튼이 없어야 한다 (안내 문구는 별개).
    const text = container.textContent;
    for (const forbidden of ["Place Order", "실거래 시작", "주문 실행", "매수 실행", "매도 실행"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("secret / 계좌번호 원문 표시 0건", async () => {
    const { container } = render(
      <PortfolioSourceCard apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))} />);
    await screen.findByTestId("portfolio-source-paper-section");
    const text = container.textContent;
    for (const forbidden of ["sk-ant-", "Bearer ", "access_token", "12345678-01"]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("portfolioSource 미구현 client 여도 깨지지 않음", async () => {
    render(<PortfolioSourceCard apiClient={{}} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-source-card")).toBeTruthy());
  });

  // ─────────── KIS-PAPER-FULL-LIFECYCLE E: 자동 새로고침 동작 ───────────

  it("[E] AutoRefreshFooter 노출 + last-updated + 토글 표시", async () => {
    render(<PortfolioSourceCard
      apiClient={_api(_report(_PAPER_OK, _KIS_UNAVAILABLE))}
      pollIntervalMs={1500}
    />);
    await screen.findByTestId("portfolio-source-paper-section");
    expect(screen.getByTestId("portfolio-source-auto-refresh")).toBeTruthy();
    expect(screen.getByTestId("portfolio-source-auto-refresh-toggle")).toBeTruthy();
    expect(screen.getByTestId("portfolio-source-auto-refresh-last").textContent)
      .toMatch(/방금 전|초 전|—/);
    // 자동 ON 상태 — 인터벌 표시.
    expect(screen.getByTestId("portfolio-source-auto-refresh").textContent)
      .toContain("자동 새로고침 ON");
  });

  it("[E] 자동 새로고침 OFF 토글 후 수동 버튼 활성화", async () => {
    const api = _api(_report(_PAPER_OK, _KIS_UNAVAILABLE));
    render(<PortfolioSourceCard apiClient={api} pollIntervalMs={5000} />);
    await screen.findByTestId("portfolio-source-paper-section");
    // OFF 로 토글.
    const toggle = screen.getByTestId("portfolio-source-auto-refresh-toggle");
    fireEvent.click(toggle);
    // 수동 새로고침 버튼 나타남.
    expect(screen.getByTestId("portfolio-source-auto-refresh-manual")).toBeTruthy();
    // 클릭 시 API 추가 호출.
    const before = api.portfolioSource.mock.calls.length;
    fireEvent.click(screen.getByTestId("portfolio-source-auto-refresh-manual"));
    await waitFor(() =>
      expect(api.portfolioSource.mock.calls.length).toBe(before + 1));
  });

  it("[E] 백엔드 호출 실패 시 footer 에 에러 표시 + 카드 자체는 유지", async () => {
    const api = { portfolioSource: vi.fn(async () => { throw new Error("ECONNREFUSED"); }) };
    render(<PortfolioSourceCard apiClient={api} pollIntervalMs={5000} />);
    await waitFor(() =>
      expect(screen.getByTestId("portfolio-source-auto-refresh")).toBeTruthy());
    // 에러 노출 (footer 의 -error testid OR 카드 본문의 portfolio-source-error).
    await waitFor(() => {
      const fErr = screen.queryByTestId("portfolio-source-auto-refresh-error");
      const bErr = screen.queryByTestId("portfolio-source-error");
      expect(fErr || bErr).toBeTruthy();
    });
  });
});
