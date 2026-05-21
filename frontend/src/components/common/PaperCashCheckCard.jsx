/**
 * P-07: Paper 현금 잔고 (CapitalState) 사전 검사 카드.
 *
 * BUY 후보의 *필요 금액* (현재가 × 수량) 이 현재 *남은 Paper 현금* 으로
 * 살 수 있는지 *advisory* 표시. 종목당 한도와는 별개 — 한도가 충분해도
 * 누적 매수로 인해 현금이 부족하면 INSUFFICIENT_PAPER_CASH 사유로 차단.
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "BUY/SELL/HOLD" 라벨
 *    button 0개 — 본 카드는 *advisory 표시 전용*.
 *  - "Paper 전용 · 실제 주문 아님" 영구 배지 노출.
 *  - input / textarea / select 0개 — caller 가 prop 으로 후보 정보를 전달.
 *  - 차단된 BUY 는 *현금을 차감하지 않는다* — 본 카드는 *상태 변경 0건*.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


const _VERDICT_LABEL = {
  ALLOWED:                  { label: "현금 충분",   color: "#15803d", bg: "#dcfce7" },
  INSUFFICIENT_PAPER_CASH:  { label: "현금 부족",   color: "#b91c1c", bg: "#fee2e2" },
  INVALID_PRICE:            { label: "가격 이상",   color: "#b91c1c", bg: "#fee2e2" },
  MISSING_PRICE:            { label: "가격 누락",   color: "#b91c1c", bg: "#fee2e2" },
  INVALID_QUANTITY:         { label: "수량 이상",   color: "#b91c1c", bg: "#fee2e2" },
  SKIP_NON_BUY:             { label: "검사 무관",   color: "#475569", bg: "#f1f5f9" },
};


function _formatKrw(amount) {
  if (amount == null) return "—";
  return Number(amount).toLocaleString("ko-KR");
}


export function PaperCashCheckCard({
  testId = "paper-cash-check-card",
  symbol            = null,
  action            = "BUY",
  price             = null,
  quantity          = null,
  availableCashKrw  = null,   // null → backend singleton 자동 사용
  autoLoad          = true,
}) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (price == null || quantity == null) {
      setResult(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const r = await backendApi.previewPaperCashCheck({
        action,
        symbol,
        price,
        quantity,
        availableCashKrw,
      });
      setResult(r);
      setError("");
    } catch (e) {
      setError(e?.message || "현금 잔고 사전 검사 실패");
    } finally {
      setLoading(false);
    }
  }, [action, symbol, price, quantity, availableCashKrw]);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  const verdict = result?.verdict || null;
  const palette = verdict ? _VERDICT_LABEL[verdict] : null;
  const isInsufficient = verdict === "INSUFFICIENT_PAPER_CASH";

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", flexWrap: "wrap", gap: 6 }}>
          <SectionLabel>💵 Paper 현금 잔고 사전 검사</SectionLabel>
          <span
            data-testid={`${testId}-paper-only-badge`}
            style={{
              fontSize: 9, fontWeight: 700,
              padding: "1px 6px", borderRadius: 3,
              background: "#94a3b820",
              border: "1px solid #94a3b855",
              color: "#475569",
            }}
          >
            Paper 전용 · 실제 주문 아님
          </span>
        </div>

        <div
          data-testid={`${testId}-disclaimer`}
          style={{
            padding: "6px 10px",
            background: "#1e3a8a15",
            border: "1px solid #1e3a8a55",
            borderRadius: 4,
            fontSize: 11,
            color: "#1e3a8a",
            lineHeight: 1.5,
          }}
        >
          종목당 투자 한도와 *별개* — 한도가 충분해도 누적 매수로 인해 남은
          Paper 현금이 부족하면 BUY 가 차단됩니다. 차단된 BUY 는 Paper 현금을
          차감하지 않습니다.
        </div>

        {loading && !result ? (
          <div data-testid={`${testId}-loading`}
               style={{ fontSize: 11, color: "var(--c-text-3)" }}>
            현금 잔고 검사 중…
          </div>
        ) : null}

        {error ? (
          <div data-testid={`${testId}-error`}
               style={{
                 padding: "6px 10px",
                 background: "#fef2f2",
                 border: "1px solid #fecaca",
                 borderRadius: 4,
                 fontSize: 11,
                 color: "#991b1b",
               }}>
            ⚠️ {error}
          </div>
        ) : null}

        {(price == null || quantity == null) && !loading && !error ? (
          <div data-testid={`${testId}-no-input`}
               style={{ fontSize: 11, color: "var(--c-text-3)", lineHeight: 1.5 }}>
            후보 정보(현재가 + 요청 수량) 가 아직 전달되지 않아 검사를 시작
            하지 않았습니다. caller 가 prop 으로 전달하면 자동 평가합니다.
          </div>
        ) : null}

        {result && palette && (
          <>
            <div
              data-testid={`${testId}-verdict`}
              data-verdict={verdict}
              style={{
                padding: "8px 10px",
                background: palette.bg,
                border: `1px solid ${palette.color}55`,
                borderRadius: 6,
                fontSize: 13,
                color: palette.color,
                fontWeight: 700,
                display: "flex",
                alignItems: "center",
                gap: 8,
                flexWrap: "wrap",
              }}
            >
              <span>{palette.label}</span>
              <span style={{ fontFamily: "monospace", fontSize: 10,
                              opacity: 0.7 }}>
                {verdict}
              </span>
            </div>

            {/* 차단된 경우 명시 안내 — "남은 Paper 현금이 부족하여 매수 차단" */}
            {isInsufficient && (
              <div
                data-testid={`${testId}-blocked-banner`}
                style={{
                  padding: "6px 10px",
                  background: "#fef2f2",
                  border: "1px solid #fecaca",
                  borderRadius: 4,
                  fontSize: 11,
                  color: "#991b1b",
                  lineHeight: 1.5,
                }}
              >
                남은 Paper 현금이 부족하여 매수 차단
              </div>
            )}

            {/* 자세한 reason_ko + 금액 */}
            <div
              data-testid={`${testId}-reason`}
              style={{
                padding: "6px 10px",
                background: "var(--c-surface-2, #f8fafc)",
                border: "1px solid var(--c-border)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
                lineHeight: 1.6,
              }}
            >
              {result.reason_ko}
            </div>

            <div
              data-testid={`${testId}-amounts`}
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                gap: 6,
                fontSize: 11,
              }}
            >
              <div data-testid={`${testId}-amount-required`}
                   style={{ padding: "6px 8px",
                              background: "var(--c-surface-2, #f8fafc)",
                              border: "1px solid var(--c-border)",
                              borderRadius: 4 }}>
                필요 금액:{" "}
                <b style={{ fontFamily: "monospace" }}>
                  {_formatKrw(result.required_krw)} KRW
                </b>
              </div>
              <div data-testid={`${testId}-amount-available`}
                   style={{ padding: "6px 8px",
                              background: "var(--c-surface-2, #f8fafc)",
                              border: "1px solid var(--c-border)",
                              borderRadius: 4 }}>
                남은 현금:{" "}
                <b style={{ fontFamily: "monospace" }}>
                  {_formatKrw(result.available_cash_krw)} KRW
                </b>
              </div>
              <div data-testid={`${testId}-amount-shortfall`}
                   style={{ padding: "6px 8px",
                              background: "var(--c-surface-2, #f8fafc)",
                              border: "1px solid var(--c-border)",
                              borderRadius: 4,
                              color: result.shortfall_krw > 0 ? "#991b1b"
                                                              : "var(--c-text-3)" }}>
                부족:{" "}
                <b style={{ fontFamily: "monospace" }}>
                  {_formatKrw(result.shortfall_krw)} KRW
                </b>
              </div>
            </div>
          </>
        )}

        {/* 절대 invariant 배지 */}
        <div
          data-testid={`${testId}-invariant-badges`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {[
            "Paper 전용",
            "현금 차감 0건",
            "broker 호출 0건",
          ].map((label) => (
            <span
              key={label}
              style={{
                fontSize: 9,
                fontWeight: 700,
                padding: "1px 6px",
                borderRadius: 3,
                background: "#94a3b820",
                border: "1px solid #94a3b855",
                color: "#475569",
              }}
            >
              {label}
            </span>
          ))}
        </div>
      </div>
    </Card>
  );
}

export default PaperCashCheckCard;
