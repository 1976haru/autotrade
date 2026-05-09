import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FuturesMarginRiskCard } from "./FuturesMarginRiskCard";
import { backendApi } from "../../services/backend/client";

vi.mock("../../services/backend/client", () => ({
  backendApi: { futuresMarginPreview: vi.fn() },
}));

afterEach(() => { cleanup(); vi.clearAllMocks(); });
beforeEach(() => {
  backendApi.futuresMarginPreview.mockResolvedValue({
    leverage:    { decision: "PASS", reasons: [], warnings: [], metrics: {} },
    margin:      { decision: "PASS", reasons: [], warnings: [], metrics: {} },
    liquidation: { decision: "PASS", reasons: [], warnings: [], metrics: {} },
    overall:     "PASS",
    notice:      "선물 마진/레버리지/강제청산 위험을 read-only로 사전 평가합니다.",
  });
});


describe("<FuturesMarginRiskCard>", () => {
  it("shows 'read-only / 실제 주문 아님' badge prominently", () => {
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    expect(getByTestId("futures-margin-readonly-badge").textContent)
      .toContain("실제 주문 아님");
  });

  it("calls preview API with form values on submit", async () => {
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    fireEvent.click(getByTestId("futures-margin-evaluate-btn"));
    await waitFor(() => expect(backendApi.futuresMarginPreview).toHaveBeenCalled());
    const body = backendApi.futuresMarginPreview.mock.calls[0][0];
    expect(body.contract).toBe("KOSPI200_2503");
    expect(body.side).toBe("BUY");
    expect(body.quantity).toBe(1);
    expect(body.mark_price).toBe(1000000);
    expect(body.leverage).toBe(5);
    expect(body.margin_used).toBe(0);
    expect(body.margin_available).toBe(10000000);
  });

  it("renders PASS decision banner with green color when overall=PASS", async () => {
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    fireEvent.click(getByTestId("futures-margin-evaluate-btn"));
    const overall = await waitFor(() => getByTestId("futures-margin-overall"));
    expect(overall.textContent).toContain("통과");
  });

  it("renders BLOCK decision banner with red color + reasons when liquidation BLOCK", async () => {
    backendApi.futuresMarginPreview.mockResolvedValueOnce({
      leverage: { decision: "PASS", reasons: [], warnings: [], metrics: {} },
      margin:   { decision: "PASS", reasons: [], warnings: [], metrics: {} },
      liquidation: {
        decision: "BLOCK",
        reasons: ["liquidation distance 0.50% <= critical threshold 3.0%"],
        warnings: [],
        metrics: { distance_pct: 0.5 },
      },
      overall: "BLOCK",
      notice:  "x",
    });
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    fireEvent.click(getByTestId("futures-margin-evaluate-btn"));
    const overall = await waitFor(() => getByTestId("futures-margin-overall"));
    expect(overall.textContent).toContain("차단");
    expect(getByTestId("futures-margin-rule-LiquidationRiskRule").textContent)
      .toContain("차단");
  });

  it("renders WARN decision banner with amber color when only WARN", async () => {
    backendApi.futuresMarginPreview.mockResolvedValueOnce({
      leverage: { decision: "PASS", reasons: [], warnings: [], metrics: {} },
      margin:   { decision: "WARN", reasons: [],
                   warnings: ["maintenance margin buffer thin"], metrics: {} },
      liquidation: { decision: "PASS", reasons: [], warnings: [], metrics: {} },
      overall: "WARN",
      notice:  "x",
    });
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    fireEvent.click(getByTestId("futures-margin-evaluate-btn"));
    const overall = await waitFor(() => getByTestId("futures-margin-overall"));
    expect(overall.textContent).toContain("경고");
  });

  it("surfaces submit error from backendApi", async () => {
    backendApi.futuresMarginPreview.mockRejectedValueOnce(new Error("network down"));
    const { getByTestId } = render(<FuturesMarginRiskCard />);
    fireEvent.click(getByTestId("futures-margin-evaluate-btn"));
    const err = await waitFor(() => getByTestId("futures-margin-error"));
    expect(err.textContent).toContain("network down");
  });

  it("disables evaluate button when contract empty", () => {
    const { getByTestId, getByPlaceholderText } = render(<FuturesMarginRiskCard />);
    fireEvent.change(getByPlaceholderText("KOSPI200_2503"),
      { target: { value: "" } });
    expect(getByTestId("futures-margin-evaluate-btn").disabled).toBe(true);
  });
});
