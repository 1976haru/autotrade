/**
 * 체크리스트 #62: RiskControlPanel 테스트.
 *
 * 검증 invariant:
 *   - 3단계 버튼이 모두 표시되고, 각 버튼 클릭 시 *반드시* 확인 모달이 열린다.
 *   - 위험 액션은 모달 없이 실행되지 않는다.
 *   - "실제 취소 아님" / "자동 청산 아님" 문구가 후보 list에 표시된다.
 *   - 자동 전량청산 버튼이 *없다*.
 *   - place_order / cancel_order / 실거래/LIVE flag 토글 호출 0건 — backendApi
 *     모킹으로 검증.
 *   - 친화 에러 메시지 — raw "Failed to fetch" 미노출.
 */

import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  CancelCandidatesList,
  LiquidationCandidatesList,
  RiskActionConfirmModal,
  RiskControlPanel,
  RiskLimitsSummary,
  SafetyFlagsRow,
} from "./RiskControlPanel";
import { backendApi } from "../../services/backend/client";


vi.mock("../../services/backend/client", () => ({
  backendApi: {
    getRiskPolicy:                       vi.fn(),
    emergencyStopStatus:                 vi.fn(),
    setEmergencyStop:                    vi.fn(),
    emergencyStopCancelCandidates:       vi.fn(),
    emergencyStopLiquidationCandidates:  vi.fn(),
    // 실제 broker / order API — 모두 mock으로 등록해 호출되면 테스트 fail.
    brokerOrder:                         vi.fn(),
    approveApproval:                     vi.fn(),
    cancelApproval:                      vi.fn(),
  },
}));


afterEach(() => { cleanup(); vi.clearAllMocks(); });


function _policy(overrides = {}) {
  return {
    max_order_notional:           1_000_000,
    max_daily_loss:                 200_000,
    max_positions:                        5,
    max_symbol_exposure:          1_500_000,
    max_total_exposure:                   0,
    enable_live_trading:              false,
    enable_ai_execution:              false,
    enable_futures_live_trading:      false,
    ...overrides,
  };
}


function _status(overrides = {}) {
  return {
    level:                       "OFF",
    emergency_stop:              false,
    cancel_candidate_count:           0,
    liquidation_candidate_count:      0,
    ...overrides,
  };
}


beforeEach(() => {
  backendApi.getRiskPolicy.mockResolvedValue(_policy());
  backendApi.emergencyStopStatus.mockResolvedValue(_status());
  backendApi.setEmergencyStop.mockResolvedValue({
    emergency_stop: true, level: "LEVEL_1",
  });
  backendApi.emergencyStopCancelCandidates.mockResolvedValue({
    candidates: [], count: 0, note: "read-only",
  });
  backendApi.emergencyStopLiquidationCandidates.mockResolvedValue({
    candidates: [], count: 0, total_unrealized_pnl: 0, note: "no auto",
  });
});


// ====================================================================
// 1. RiskLimitsSummary
// ====================================================================


describe("<RiskLimitsSummary>", () => {
  it("renders the 5 key limits when policy is present", () => {
    const { getByTestId } = render(<RiskLimitsSummary policy={_policy()} />);
    expect(getByTestId("risk-limit-max_order_notional").textContent)
      .toMatch(/1,000,000/);
    expect(getByTestId("risk-limit-max_daily_loss").textContent)
      .toMatch(/200,000/);
    expect(getByTestId("risk-limit-max_positions").textContent).toMatch(/5건/);
  });

  it("displays '비활성' when a limit is 0", () => {
    const { getByTestId } = render(
      <RiskLimitsSummary policy={_policy({ max_total_exposure: 0 })} />
    );
    expect(getByTestId("risk-limit-max_total_exposure").textContent)
      .toMatch(/비활성/);
  });

  it("renders fallback empty message when policy is null", () => {
    const { getByTestId } = render(<RiskLimitsSummary policy={null} />);
    expect(getByTestId("risk-limits-summary-empty").textContent)
      .toMatch(/설정값 없음/);
  });
});


// ====================================================================
// 2. SafetyFlagsRow
// ====================================================================


describe("<SafetyFlagsRow>", () => {
  it("shows all LIVE flags as 비활성 + 긴급정지 OFF in safe state", () => {
    const { getByTestId } = render(
      <SafetyFlagsRow policy={_policy()} emergencyStop={false} />
    );
    expect(getByTestId("flag-live").textContent).toMatch(/비활성/);
    expect(getByTestId("flag-ai").textContent).toMatch(/비활성/);
    expect(getByTestId("flag-futures").textContent).toMatch(/비활성/);
    expect(getByTestId("flag-emergency").textContent).toMatch(/OFF/);
  });

  it("shows emergency 'ON' when emergencyStop=true", () => {
    const { getByTestId } = render(
      <SafetyFlagsRow policy={_policy()} emergencyStop={true} />
    );
    const chip = getByTestId("flag-emergency");
    expect(chip.textContent).toMatch(/ON/);
    expect(chip.getAttribute("data-ok")).toBe("false");
  });

  it("warns when enable_live_trading=true (operator should not have flipped it)", () => {
    const { getByTestId } = render(
      <SafetyFlagsRow policy={_policy({ enable_live_trading: true })}
                      emergencyStop={false} />
    );
    expect(getByTestId("flag-live").textContent).toMatch(/활성화됨/);
    expect(getByTestId("flag-live").getAttribute("data-ok")).toBe("false");
  });
});


// ====================================================================
// 3. CancelCandidatesList
// ====================================================================


describe("<CancelCandidatesList>", () => {
  it("shows the '실제 취소 아님' banner above candidate rows", () => {
    const cands = [{
      id: 17, symbol: "005930", side: "BUY", quantity: 5,
      order_type: "MARKET", status: "PENDING",
      created_at: "2026-05-13T10:00:00Z",
    }];
    const { getByTestId } = render(<CancelCandidatesList candidates={cands} />);
    expect(getByTestId("cancel-candidates-banner").textContent)
      .toMatch(/실제 취소 아님/);
    expect(getByTestId("cancel-candidate-17").textContent).toMatch(/005930/);
  });

  it("renders empty state for zero candidates", () => {
    const { getByTestId } = render(<CancelCandidatesList candidates={[]} />);
    expect(getByTestId("cancel-candidates-empty").textContent)
      .toMatch(/미체결 취소 후보가 없습니다/);
  });

  it("uses friendlyErrorMessage for raw Failed to fetch", () => {
    const { getByTestId } = render(
      <CancelCandidatesList candidates={null} error="Failed to fetch" />
    );
    const err = getByTestId("cancel-candidates-error");
    // raw "Failed to fetch"가 그대로 노출되지 *않는다*
    expect(err.textContent).not.toBe("Failed to fetch");
    expect(err.textContent).toMatch(/백엔드|데모/);
  });
});


// ====================================================================
// 4. LiquidationCandidatesList
// ====================================================================


describe("<LiquidationCandidatesList>", () => {
  it("renders the '자동 청산 아님' banner with positions", () => {
    const cands = [{
      symbol: "005930", quantity: 10,
      avg_price: 60_000, market_price: 65_000,
      unrealized_pnl: 50_000, risk_reason: "stop-loss近接",
    }];
    const { getByTestId } = render(
      <LiquidationCandidatesList candidates={cands} totalUnrealized={50_000} />
    );
    expect(getByTestId("liquidation-candidates-banner").textContent)
      .toMatch(/자동 청산 아님/);
    expect(getByTestId("liquidation-candidates-banner").textContent)
      .toMatch(/자동 전량청산 버튼은 비활성화/);
    expect(getByTestId("liquidation-candidate-005930").textContent)
      .toMatch(/\+.*50,000원/);
  });

  it("renders empty state for zero positions", () => {
    const { getByTestId } = render(
      <LiquidationCandidatesList candidates={[]} totalUnrealized={0} />
    );
    expect(getByTestId("liquidation-candidates-empty").textContent)
      .toMatch(/청산 후보가 없습니다/);
  });

  it("hides total when totalUnrealized is null", () => {
    const cands = [{ symbol: "X", quantity: 1, avg_price: 1, market_price: 1,
                     unrealized_pnl: 0 }];
    const { queryByTestId } = render(
      <LiquidationCandidatesList candidates={cands} totalUnrealized={null} />
    );
    expect(queryByTestId("liquidation-total-unrealized")).toBeNull();
  });
});


// ====================================================================
// 5. RiskActionConfirmModal — safety wording
// ====================================================================


describe("<RiskActionConfirmModal>", () => {
  it("renders explicit safety bullets for ENABLE_LEVEL_1", () => {
    const { getByTestId } = render(
      <RiskActionConfirmModal
        actionType="ENABLE_LEVEL_1"
        busy={false}
        onConfirm={() => {}} onCancel={() => {}}
      />
    );
    const summary = getByTestId("risk-confirm-summary-ENABLE_LEVEL_1");
    expect(summary.textContent).toMatch(/신규 매수만 중단/);
    expect(summary.textContent).toMatch(/자동 청산하지/);
    expect(summary.textContent).toMatch(/자동 취소하지/);
  });

  it("renders 'no real cancel' wording for VIEW_CANCEL_CANDIDATES", () => {
    const { getByTestId } = render(
      <RiskActionConfirmModal
        actionType="VIEW_CANCEL_CANDIDATES"
        busy={false}
        onConfirm={() => {}} onCancel={() => {}}
      />
    );
    const summary = getByTestId("risk-confirm-summary-VIEW_CANCEL_CANDIDATES");
    expect(summary.textContent).toMatch(/표시.*합니다/);
    expect(summary.textContent).toMatch(/cancel_order.*발생하지 않습니다/);
  });

  it("renders 'no auto liquidation' wording for VIEW_LIQUIDATION_CANDIDATES", () => {
    const { getByTestId } = render(
      <RiskActionConfirmModal
        actionType="VIEW_LIQUIDATION_CANDIDATES"
        busy={false}
        onConfirm={() => {}} onCancel={() => {}}
      />
    );
    const summary = getByTestId("risk-confirm-summary-VIEW_LIQUIDATION_CANDIDATES");
    expect(summary.textContent).toMatch(/자동 전량청산.*비활성/);
    expect(summary.textContent).toMatch(/수동 승인/);
  });

  it("renders nothing for unknown actionType", () => {
    const { container } = render(
      <RiskActionConfirmModal actionType="UNKNOWN" busy={false}
        onConfirm={() => {}} onCancel={() => {}} />
    );
    expect(container.firstChild).toBeNull();
  });
});


// ====================================================================
// 6. RiskControlPanel — end-to-end
// ====================================================================


describe("<RiskControlPanel>", () => {
  it("renders all 3 level buttons + auto-liquidation invariant warning", async () => {
    const { findByTestId, getByText } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    expect(getByText(/신규매수 중단 \(LEVEL 1\)/)).toBeTruthy();
    expect(getByText(/미체결 취소 후보 확인 \(LEVEL 2\)/)).toBeTruthy();
    expect(getByText(/청산 후보 표시 \(LEVEL 3\)/)).toBeTruthy();
    // 자동 전량청산 invariant banner 노출
    expect(await findByTestId("risk-control-auto-liquidation-warning"))
      .toBeTruthy();
  });

  it("does NOT render forbidden labels — no auto liquidation button", async () => {
    const { findByTestId, queryByText } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    expect(queryByText(/전량청산 실행/)).toBeNull();
    expect(queryByText(/자동 청산 시작/)).toBeNull();
    expect(queryByText(/즉시 시장가 청산/)).toBeNull();
    expect(queryByText(/Liquidate Now/i)).toBeNull();
    expect(queryByText(/Place Order/i)).toBeNull();
  });

  it("LEVEL 1 button opens confirm modal — no immediate API call", async () => {
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    const btn = await findByText(/신규매수 중단 \(LEVEL 1\)/);
    fireEvent.click(btn);
    await findByTestId("risk-confirm-summary-ENABLE_LEVEL_1");
    // 모달이 열렸을 뿐 — setEmergencyStop 미호출
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
  });

  it("LEVEL 2 button opens confirm modal — no API call yet", async () => {
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    fireEvent.click(await findByText(/미체결 취소 후보 확인/));
    await findByTestId("risk-confirm-summary-VIEW_CANCEL_CANDIDATES");
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
    expect(backendApi.emergencyStopCancelCandidates).not.toHaveBeenCalled();
  });

  it("LEVEL 3 button opens confirm modal — no API call yet", async () => {
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    fireEvent.click(await findByText(/청산 후보 표시/));
    await findByTestId("risk-confirm-summary-VIEW_LIQUIDATION_CANDIDATES");
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
    expect(backendApi.emergencyStopLiquidationCandidates).not.toHaveBeenCalled();
  });

  it("confirming LEVEL 1 calls setEmergencyStop(true, {level:'LEVEL_1'})", async () => {
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    fireEvent.click(await findByText(/신규매수 중단 \(LEVEL 1\)/));
    const dialog = await findByTestId("risk-confirm-summary-ENABLE_LEVEL_1");
    // confirm 버튼 클릭 (DecisionDialog의 confirmLabel)
    const confirmBtn = dialog.closest("[role='dialog']")
      .querySelector("button:nth-of-type(2)");
    await act(async () => { fireEvent.click(confirmBtn); });
    await waitFor(() => {
      expect(backendApi.setEmergencyStop).toHaveBeenCalledWith(
        true, expect.objectContaining({ level: "LEVEL_1" }),
      );
    });
    // 실거래/주문 API는 *호출되지 않음* invariant
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
    expect(backendApi.approveApproval).not.toHaveBeenCalled();
    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
  });

  it("confirming LEVEL 2 fetches cancel candidates (read-only)", async () => {
    backendApi.emergencyStopCancelCandidates.mockResolvedValueOnce({
      candidates: [{ id: 7, symbol: "005930", side: "BUY", quantity: 5,
                     order_type: "MARKET",
                     created_at: "2026-05-13T09:00:00Z" }],
      count: 1, note: "read-only",
    });
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    fireEvent.click(await findByText(/미체결 취소 후보 확인/));
    const dialog = await findByTestId("risk-confirm-summary-VIEW_CANCEL_CANDIDATES");
    const confirmBtn = dialog.closest("[role='dialog']")
      .querySelector("button:nth-of-type(2)");
    await act(async () => { fireEvent.click(confirmBtn); });
    await waitFor(() => {
      expect(backendApi.emergencyStopCancelCandidates).toHaveBeenCalled();
    });
    await findByTestId("cancel-candidate-7");
    // 실제 cancel API는 호출되지 *않음*
    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
  });

  it("confirming LEVEL 3 fetches liquidation candidates (read-only)", async () => {
    backendApi.emergencyStopLiquidationCandidates.mockResolvedValueOnce({
      candidates: [{ symbol: "005930", quantity: 5,
                     avg_price: 60_000, market_price: 65_000,
                     unrealized_pnl: 25_000 }],
      count: 1, total_unrealized_pnl: 25_000, note: "no auto",
    });
    const { findByText, findByTestId } = render(<RiskControlPanel />);
    fireEvent.click(await findByText(/청산 후보 표시/));
    const dialog = await findByTestId("risk-confirm-summary-VIEW_LIQUIDATION_CANDIDATES");
    const confirmBtn = dialog.closest("[role='dialog']")
      .querySelector("button:nth-of-type(2)");
    await act(async () => { fireEvent.click(confirmBtn); });
    await waitFor(() => {
      expect(backendApi.emergencyStopLiquidationCandidates).toHaveBeenCalled();
    });
    await findByTestId("liquidation-candidate-005930");
    // 실제 주문 / sell API는 호출되지 *않음*
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
  });

  it("RESUME button visible only when level != OFF", async () => {
    backendApi.emergencyStopStatus.mockResolvedValueOnce(
      _status({ level: "LEVEL_1", emergency_stop: true })
    );
    const { findByText } = render(<RiskControlPanel />);
    const btn = await findByText(/Kill Switch 해제/);
    expect(btn).toBeTruthy();
  });

  it("RESUME button hidden when level == OFF", async () => {
    const { findByTestId, queryByText } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    expect(queryByText(/Kill Switch 해제/)).toBeNull();
  });

  it("shows friendly error when both policy + status fetch fail", async () => {
    backendApi.getRiskPolicy.mockRejectedValueOnce(new Error("Failed to fetch"));
    backendApi.emergencyStopStatus.mockRejectedValueOnce(new Error("Failed to fetch"));
    const { findByTestId } = render(<RiskControlPanel />);
    const err = await findByTestId("risk-control-panel-error");
    // raw "Failed to fetch" 미노출
    expect(err.textContent).not.toMatch(/^Failed to fetch$/);
  });

  it("renders safety flags row in safe state", async () => {
    const { findByTestId } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    await findByTestId("risk-safety-flags-row");
  });

  it("renders RiskLimitsSummary", async () => {
    const { findByTestId } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    await findByTestId("risk-limits-summary");
  });

  it("invariant: no place_order / cancel_order / LIVE flag toggle API calls during lifecycle", async () => {
    const { findByTestId } = render(<RiskControlPanel />);
    await findByTestId("risk-control-panel");
    // 초기 렌더 시 호출되는 API는 read-only 두 개뿐
    expect(backendApi.getRiskPolicy).toHaveBeenCalled();
    expect(backendApi.emergencyStopStatus).toHaveBeenCalled();
    // 주문/취소/체결/실거래 토글 API는 부르지 *않는다*
    expect(backendApi.brokerOrder).not.toHaveBeenCalled();
    expect(backendApi.approveApproval).not.toHaveBeenCalled();
    expect(backendApi.cancelApproval).not.toHaveBeenCalled();
    expect(backendApi.setEmergencyStop).not.toHaveBeenCalled();
    expect(backendApi.emergencyStopCancelCandidates).not.toHaveBeenCalled();
    expect(backendApi.emergencyStopLiquidationCandidates).not.toHaveBeenCalled();
  });
});
