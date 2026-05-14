/**
 * #89 KIS Paper One-Click Test Card 테스트.
 *
 * 요구 사항:
 * - 카드 렌더링 + "한투 모의투자 전용 · 실제 돈 안 나감" 배지
 * - 5개 큰 버튼 (준비상태 / quick / slow / mock / 정지)
 * - readiness 가 차단되면 KIS 모드 버튼 disabled
 * - 확인 모달이 *반드시* 거쳐야 시작 가능
 * - 결과판 (AI 판단 / 주문 / 체결 / 거절 / 리스크 차단 / 오류)
 * - 점수판
 * - 금지 단어 (지금 매수 / 지금 매도 / 실거래 / Place Order) 0건
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    kisPaperReadiness: vi.fn(),
    kisPaperStart:     vi.fn(),
    kisPaperStop:      vi.fn(),
    kisPaperStatus:    vi.fn(),
    kisPaperReport:    vi.fn(),
  },
}));

import { backendApi } from "../../services/backend/client";
import { KisPaperOneClickTestCard } from "./KisPaperOneClickTestCard";


function _readiness(overrides = {}) {
  return {
    ready:                true,
    can_run_kis_paper:    true,
    can_run_mock:         true,
    blocked_reasons:      [],
    detail_messages:      [],
    safety_flags: {
      default_mode:                "PAPER",
      enable_live_trading:         false,
      enable_ai_execution:         false,
      enable_futures_live_trading: false,
      kis_is_paper:                true,
    },
    kis_key_present:      true,
    kis_secret_present:   true,
    kis_account_present:  true,
    is_order_intent:      false,
    is_order_signal:      false,
    ...overrides,
  };
}


function _status(overrides = {}) {
  return {
    state:        "IDLE",
    mode:         null,
    started_at:   null,
    finished_at:  null,
    counters: {
      ai_decisions: 0, ai_buy_signals: 0, ai_sell_signals: 0,
      orders_attempted: 0, orders_executed: 0, orders_rejected: 0,
      fills_observed: 0, unfilled_count: 0, risk_blocks: 0, errors: 0,
    },
    failures: [],
    ...overrides,
  };
}


beforeEach(() => {
  for (const k of Object.keys(backendApi)) {
    if (typeof backendApi[k]?.mockReset === "function") {
      backendApi[k].mockReset();
    }
  }
  backendApi.kisPaperReadiness.mockResolvedValue(_readiness());
  backendApi.kisPaperStatus.mockResolvedValue(_status());
  backendApi.kisPaperReport.mockResolvedValue(null);
  backendApi.kisPaperStart.mockResolvedValue(_status({ state: "RUNNING" }));
  backendApi.kisPaperStop.mockResolvedValue(_status({ state: "STOPPING" }));
});

afterEach(() => { cleanup(); });


// ====================================================================
// 1. 렌더링 + 안전 배지
// ====================================================================


describe("KisPaperOneClickTestCard — 기본 렌더링", () => {
  it("카드 렌더링 + '실제 돈 안 나감' 배지 노출", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    expect(getByTestId("kis-paper-one-click-card")).toBeTruthy();
    expect(getByTestId("kis-paper-not-real-money-badge").textContent)
      .toContain("실제 돈 안 나감");
  });

  it("안내 텍스트에 안전 메시지 포함", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    const notice = getByTestId("kis-paper-notice").textContent;
    expect(notice).toContain("한투 모의투자 전용");
    expect(notice).toContain("실제 돈이 나가지 않습니다");
    expect(notice).toContain("RiskManager");
  });

  it("KIS_IS_PAPER true / live flag false 표시", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => {
      expect(getByTestId("kis-paper-flag-paper").textContent).toBe("true");
    });
    expect(getByTestId("kis-paper-flag-live").textContent).toContain("비활성");
    expect(getByTestId("kis-paper-flag-ai-exec").textContent).toContain("비활성");
  });
});


// ====================================================================
// 2. 5개 버튼 (1.준비상태 / 2.quick / 3.slow / 4.mock / 5.정지)
// ====================================================================


describe("KisPaperOneClickTestCard — 5개 버튼", () => {
  it("5개 버튼 모두 렌더링", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    expect(getByTestId("kis-paper-btn-readiness")).toBeTruthy();
    expect(getByTestId("kis-paper-btn-start-quick")).toBeTruthy();
    expect(getByTestId("kis-paper-btn-start-slow")).toBeTruthy();
    expect(getByTestId("kis-paper-btn-start-mock")).toBeTruthy();
    expect(getByTestId("kis-paper-btn-stop")).toBeTruthy();
  });

  it("정지 버튼은 IDLE 상태에서 disabled", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-stop").disabled).toBe(true));
  });

  it("KIS Paper 모드 차단 시 KIS 버튼 disabled", async () => {
    backendApi.kisPaperReadiness.mockResolvedValue(
      _readiness({ can_run_kis_paper: false }),
    );
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-start-quick").disabled).toBe(true));
    expect(getByTestId("kis-paper-btn-start-slow").disabled).toBe(true);
    // mock 은 별개 — readiness.can_run_mock 가 true 면 활성.
    expect(getByTestId("kis-paper-btn-start-mock").disabled).toBe(false);
  });

  it("Mock 모드 차단 시 mock 버튼 disabled", async () => {
    backendApi.kisPaperReadiness.mockResolvedValue(
      _readiness({ can_run_mock: false, can_run_kis_paper: false }),
    );
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-start-mock").disabled).toBe(true));
  });
});


// ====================================================================
// 3. 확인 모달 — 시작 전 반드시 통과
// ====================================================================


describe("KisPaperOneClickTestCard — 확인 모달", () => {
  it("시작 버튼 클릭 시 즉시 API 호출 X — 확인 모달 표시", async () => {
    const { getByTestId, queryByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-start-quick").disabled).toBe(false));

    expect(queryByTestId("kis-paper-confirm-modal")).toBeNull();

    await act(async () => {
      fireEvent.click(getByTestId("kis-paper-btn-start-quick"));
    });

    expect(getByTestId("kis-paper-confirm-modal")).toBeTruthy();
    // *아직* API 호출 안 함.
    expect(backendApi.kisPaperStart).not.toHaveBeenCalled();
  });

  it("'모의투자 주문 테스트 시작' 클릭 후에만 backend 호출 — confirm=true 강제", async () => {
    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-start-mock").disabled).toBe(false));

    await act(async () => {
      fireEvent.click(getByTestId("kis-paper-btn-start-mock"));
    });
    await act(async () => {
      fireEvent.click(getByTestId("kis-paper-confirm-yes"));
    });

    expect(backendApi.kisPaperStart).toHaveBeenCalledTimes(1);
    const call = backendApi.kisPaperStart.mock.calls[0][0];
    expect(call.confirm).toBe(true);
    expect(call.mode).toBe("mock");
  });

  it("취소 클릭 시 모달 닫힘 + API 미호출", async () => {
    const { getByTestId, queryByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() =>
      expect(getByTestId("kis-paper-btn-start-mock").disabled).toBe(false));

    await act(async () => {
      fireEvent.click(getByTestId("kis-paper-btn-start-mock"));
    });
    await act(async () => {
      fireEvent.click(getByTestId("kis-paper-confirm-no"));
    });

    expect(queryByTestId("kis-paper-confirm-modal")).toBeNull();
    expect(backendApi.kisPaperStart).not.toHaveBeenCalled();
  });
});


// ====================================================================
// 4. 결과판 + 점수판
// ====================================================================


describe("KisPaperOneClickTestCard — 결과판", () => {
  it("status 카운터 표시", async () => {
    backendApi.kisPaperStatus.mockResolvedValue(_status({
      state: "COMPLETED",
      mode:  "mock",
      counters: {
        ai_decisions: 10, ai_buy_signals: 4, ai_sell_signals: 2,
        orders_attempted: 3, orders_executed: 2, orders_rejected: 1,
        fills_observed: 2, unfilled_count: 0, risk_blocks: 1, errors: 0,
      },
    }));

    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => {
      expect(getByTestId("kis-paper-counter-ai-decisions").textContent).toBe("10");
    });
    expect(getByTestId("kis-paper-counter-buy").textContent).toBe("4");
    expect(getByTestId("kis-paper-counter-sell").textContent).toBe("2");
    expect(getByTestId("kis-paper-counter-orders-executed").textContent).toBe("2");
    expect(getByTestId("kis-paper-counter-rejected").textContent).toBe("1");
    expect(getByTestId("kis-paper-counter-risk-blocks").textContent).toBe("1");
  });

  it("점수판 표시 + grade label", async () => {
    backendApi.kisPaperReport.mockResolvedValue({
      mode: "mock", state: "COMPLETED",
      started_at: new Date().toISOString(),
      finished_at: new Date().toISOString(),
      duration_seconds: 5.5,
      counters: {}, failures: [],
      safety_note: "한투 모의투자 전용 — 실제 돈이 나가지 않습니다.",
      is_order_signal: false,
      score: {
        total: 85,
        grade: "PAPER_NEEDS_MORE",
        grade_label: "Paper 추가 검증 필요",
        breakdown: {},
        one_liner: "양호 (85/100): 핵심 흐름은 동작합니다.",
        is_live_authorization: false,
        is_order_signal: false,
        attention_flags: [],
      },
    });

    const { getByTestId } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => {
      expect(getByTestId("kis-paper-score-total").textContent).toBe("85");
    });
    expect(getByTestId("kis-paper-score-grade").textContent)
      .toContain("Paper 추가 검증 필요");
    expect(getByTestId("kis-paper-score-one-liner").textContent)
      .toContain("85/100");
  });
});


// ====================================================================
// 5. invariant — 금지 단어 / 금지 버튼 0건
// ====================================================================


describe("KisPaperOneClickTestCard — invariant", () => {
  it("'지금 매수' / '지금 매도' / '실거래 시작' / 'Place Order' 라벨 button 0개", async () => {
    const { container } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => container.querySelector("button"));
    const buttons = container.querySelectorAll("button");
    for (const btn of buttons) {
      const text = (btn.textContent || "").toLowerCase();
      for (const banned of [
        "place order", "buy", "sell",
        "지금 매수", "지금 매도", "실거래 시작",
        "live 켜기", "live 시작",
        "실계좌 주문",
        "enable_live_trading", "enable live",
      ]) {
        expect(text).not.toContain(banned.toLowerCase());
      }
    }
  });

  it("readiness 응답에 secret 원문 carry 0건 (frontend 가 표시 안 함)", async () => {
    // 운영 backend 가 잘못해서 secret 을 넣더라도, 카드 자체는 *_present bool 만 사용.
    backendApi.kisPaperReadiness.mockResolvedValue({
      ..._readiness(),
      kis_app_secret: "SHOULD_NOT_APPEAR_IN_UI_AT_ALL",
    });
    const { container } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => container.querySelector('[data-testid="kis-paper-key-present"]'));
    expect(container.textContent || "").not.toContain("SHOULD_NOT_APPEAR_IN_UI_AT_ALL");
  });

  it("UI 어디에도 KIS 키 / Secret / 계좌번호 입력 form 없음", async () => {
    const { container } = render(<KisPaperOneClickTestCard />);
    await waitFor(() => container.querySelector("button"));
    // input / textarea 어떤 것도 — secret 입력 UI 0개.
    expect(container.querySelectorAll("input").length).toBe(0);
    expect(container.querySelectorAll("textarea").length).toBe(0);
  });
});
