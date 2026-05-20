/**
 * P-04: Paper BUY 후보 매수 가능성 (affordability) 표시 카드.
 *
 * AI Paper 가 BUY 후보로 잡은 종목의 1주 가격이 종목당 투자금 한도를
 * 초과하거나, 남은 Paper 현금이 1주 가격보다 적으면 *advisory* 사유를
 * 사용자에게 명시 표시. 운영자가 직접 매수하지 않으며 본 카드는 *advisory
 * 표시 전용*.
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "BUY/SELL/HOLD" 라벨
 *    button 0개.
 *  - "Paper 전용 · 실제 주문 아님" 영구 배지 노출.
 *  - input / textarea / select 0개 — caller 가 prop 으로 후보 정보를 전달.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


const _VERDICT_LABEL = {
  AFFORDABLE:            { label: "매수 가능",   color: "#15803d", bg: "#dcfce7" },
  PRICE_OVER_CAP:        { label: "한도 초과",   color: "#b91c1c", bg: "#fee2e2" },
  INSUFFICIENT_CASH:     { label: "현금 부족",   color: "#b91c1c", bg: "#fee2e2" },
  BELOW_MIN_LOT:         { label: "1주 미만",    color: "#b91c1c", bg: "#fee2e2" },
  MAX_POSITIONS_REACHED: { label: "한도 도달",   color: "#b45309", bg: "#fef3c7" },
  INVALID_PRICE:         { label: "가격 이상",   color: "#b91c1c", bg: "#fee2e2" },
  MISSING_PRICE:         { label: "가격 누락",   color: "#b91c1c", bg: "#fee2e2" },
  SKIP_NON_BUY:          { label: "검사 무관",   color: "#475569", bg: "#f1f5f9" },
};


function _formatKrw(amount) {
  if (amount == null) return "—";
  return Number(amount).toLocaleString("ko-KR");
}


export function PaperAffordabilityCard({
  testId = "paper-affordability-card",
  // caller 가 후보 정보를 prop 으로 주입. 운영자가 카드 안에서 임의 입력
  // 하지 못하도록 input form 0개 정책.
  symbol            = null,
  action            = "BUY",
  price             = null,
  availableCashKrw  = 0,
  currentHeldSymbols = [],
  autoLoad          = true,
}) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (price == null) {
      // price 미주입 — backend 호출 없이 *대기* 상태 표시.
      setResult(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const r = await backendApi.previewPaperAffordability({
        action,
        symbol,
        price,
        availableCashKrw,
        currentHeldSymbols,
      });
      setResult(r);
      setError("");
    } catch (e) {
      setError(e?.message || "매수 가능성 조회 실패");
    } finally {
      setLoading(false);
    }
  }, [action, symbol, price, availableCashKrw, currentHeldSymbols]);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  const verdict = result?.verdict || null;
  const palette = verdict ? _VERDICT_LABEL[verdict] : null;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "baseline", flexWrap: "wrap", gap: 6 }}>
          <SectionLabel>🧮 매수 가능성</SectionLabel>
          {/* 영구 invariant 배지 */}
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

        {loading && !result ? (
          <div data-testid={`${testId}-loading`}
               style={{ fontSize: 11, color: "var(--c-text-3)" }}>
            매수 가능성 조회 중…
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

        {price == null && !loading && !error ? (
          <div data-testid={`${testId}-no-price`}
               style={{ fontSize: 11, color: "var(--c-text-3)", lineHeight: 1.5 }}>
            가격 정보가 아직 도착하지 않아 매수 가능성을 평가할 수 없습니다.
            (caller 가 후보 종목과 1주 가격을 prop 으로 전달하면 자동 평가합니다.)
          </div>
        ) : null}

        {result && palette && (
          <>
            {/* verdict 헤드라인 */}
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
                              fontWeight: 400, color: palette.color }}>
                ({verdict})
              </span>
            </div>

            {/* 사유 (사람 친화 한국어) */}
            <div
              data-testid={`${testId}-reason`}
              style={{
                padding: "6px 10px",
                background: "var(--c-surface-2, #f8fafc)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
                lineHeight: 1.5,
              }}
            >
              {result.reason_ko}
            </div>

            {/* 수치 요약 */}
            <div
              data-testid={`${testId}-details`}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr",
                rowGap: 4,
                columnGap: 10,
                padding: "6px 10px",
                background: "var(--c-surface, #fff)",
                border: "1px solid var(--c-border)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
              }}
            >
              <span style={{ color: "var(--c-text-3)" }}>종목</span>
              <span data-testid={`${testId}-detail-symbol`}
                    style={{ fontFamily: "monospace", fontWeight: 700 }}>
                {result.symbol || "—"}
              </span>

              <span style={{ color: "var(--c-text-3)" }}>1주 가격</span>
              <span data-testid={`${testId}-detail-price`}
                    style={{ fontFamily: "monospace" }}>
                {result.price != null ? `${_formatKrw(result.price)} KRW` : "—"}
              </span>

              <span style={{ color: "var(--c-text-3)" }}>종목당 한도</span>
              <span data-testid={`${testId}-detail-cap`}
                    style={{ fontFamily: "monospace" }}>
                {_formatKrw(result.effective_per_symbol_cap_krw)} KRW
              </span>

              <span style={{ color: "var(--c-text-3)" }}>남은 Paper 현금</span>
              <span data-testid={`${testId}-detail-cash`}
                    style={{ fontFamily: "monospace" }}>
                {_formatKrw(result.available_cash_krw)} KRW
              </span>

              <span style={{ color: "var(--c-text-3)" }}>매수 가능 수량</span>
              <span data-testid={`${testId}-detail-affordable-qty`}
                    style={{ fontFamily: "monospace", fontWeight: 700,
                              color: result.affordable_quantity > 0
                                       ? "#15803d" : "#b91c1c" }}>
                {result.affordable_quantity}주
              </span>

              <span style={{ color: "var(--c-text-3)" }}>보유 / 한도</span>
              <span data-testid={`${testId}-detail-positions`}
                    style={{ fontFamily: "monospace" }}>
                {result.current_held_unique_symbols} / {result.max_concurrent_positions}종목
              </span>
            </div>
          </>
        )}

        {/* 영구 disclaimer */}
        <div
          data-testid={`${testId}-disclaimer`}
          style={{
            fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
          }}
        >
          본 평가는 Paper 모의매매 전용 advisory 입니다. 실전 주문 결정과
          결합되지 않으며 broker / OrderExecutor 호출은 어떤 경로에서도
          발생하지 않습니다.
        </div>
      </div>
    </Card>
  );
}

export default PaperAffordabilityCard;
