/**
 * P-01: Paper 시드머니 설정 카드.
 *
 * AI Paper 모의매매에 사용할 *가상 자본* 을 설정하는 UI.
 *
 * 절대 invariant (테스트로 lock):
 *  - 본 카드는 broker / 실거래 API / OrderExecutor 호출 0건.
 *  - "지금 매수" / "Place Order" / "실거래 시작" / "BUY/SELL/HOLD" 라벨 button
 *    0개 — 본 카드는 *Paper 시드머니 설정 전용*.
 *  - 사용자에게 "이 값은 모의매매 전용이며 실전 계좌와 무관합니다" 안내
 *    영구 노출.
 *  - 허용 옵션 (10,000,000 / 30,000,000 / 50,000,000) 만 button 으로 노출 —
 *    임의 KRW 입력 form 0개 (잘못된 값 입력 차단).
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


/** 표시용 KRW 포맷 — 1,000만원 / 3,000만원 / 5,000만원. */
function formatKrwManwon(amount) {
  const manwon = Math.round(Number(amount) / 10_000);
  return `${manwon.toLocaleString("ko-KR")}만원`;
}


export function PaperCapitalCard({
  testId = "paper-capital-card",
  autoLoad = true,
}) {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pendingValue, setPendingValue] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const c = await backendApi.paperCapitalConfig();
      setConfig(c);
      setError("");
    } catch (e) {
      setError(e?.message || "Paper 시드머니 설정 조회 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (autoLoad) refresh();
  }, [autoLoad, refresh]);

  const onSelect = useCallback(async (amount) => {
    setPendingValue(amount);
    setError("");
    try {
      const c = await backendApi.setPaperCapitalConfig({ initialCash: amount });
      setConfig(c);
    } catch (e) {
      setError(e?.message || "Paper 시드머니 설정 변경 실패");
    } finally {
      setPendingValue(null);
    }
  }, []);

  const options = config?.allowed_initial_cash_options
    || [10_000_000, 30_000_000, 50_000_000];
  const selected = config?.initial_cash;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <SectionLabel>💰 Paper 시드머니</SectionLabel>

        {/* 영구 disclaimer — 사용자가 *실전 자본* 으로 오인하지 않도록. */}
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
          이 값은 모의매매 전용이며 실전 계좌와 무관합니다. AI 가 매수/매도
          판단을 할 때 *기준 자본* 으로 사용합니다. 실거래 한도와 결합되지
          않습니다.
        </div>

        {loading && !config ? (
          <div data-testid={`${testId}-loading`}
               style={{ fontSize: 11, color: "var(--c-text-3)" }}>
            설정 조회 중…
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

        {/* 옵션 3종 chip */}
        <div
          data-testid={`${testId}-options`}
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
            gap: 8,
          }}
        >
          {options.map((amount) => {
            const isSelected = selected === amount;
            const isPending = pendingValue === amount;
            return (
              <button
                key={amount}
                type="button"
                data-testid={`${testId}-option-${amount}`}
                data-selected={isSelected ? "true" : "false"}
                disabled={loading || pendingValue !== null}
                onClick={() => onSelect(amount)}
                style={{
                  padding: "10px 12px",
                  borderRadius: 6,
                  border: `1px solid ${isSelected ? "#2563eb" : "var(--c-border)"}`,
                  background: isSelected ? "#eff6ff" : "var(--c-surface)",
                  color: isSelected ? "#1e3a8a" : "var(--c-text)",
                  fontWeight: isSelected ? 700 : 500,
                  fontSize: 13,
                  cursor: loading || pendingValue !== null ? "wait" : "pointer",
                  textAlign: "center",
                  display: "flex",
                  flexDirection: "column",
                  gap: 2,
                  lineHeight: 1.3,
                }}
              >
                <span>{formatKrwManwon(amount)}</span>
                <span style={{ fontSize: 10, color: "var(--c-text-3)",
                                fontFamily: "monospace" }}>
                  {Number(amount).toLocaleString("ko-KR")} KRW
                </span>
                {isSelected && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, color: "#2563eb",
                    marginTop: 2,
                  }}>
                    ✓ 선택됨
                  </span>
                )}
                {isPending && (
                  <span style={{ fontSize: 9, color: "var(--c-text-3)" }}>
                    설정 중…
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {/* 현재 설정 요약 */}
        {config && (
          <div data-testid={`${testId}-summary`}
               style={{
                 padding: "6px 10px",
                 background: "var(--c-surface-2, #f8fafc)",
                 borderRadius: 4,
                 fontSize: 11,
                 color: "var(--c-text-2)",
                 lineHeight: 1.5,
               }}>
            <div>
              현재 Paper 시드머니: <b data-testid={`${testId}-current-value`}>
                {formatKrwManwon(config.initial_cash)} ({config.currency})
              </b>
            </div>
            <div style={{ fontSize: 10, color: "var(--c-text-3)", marginTop: 2 }}>
              저장 위치: 메모리 (앱 재시작 시 default 로 복귀 — 영구 저장은
              P-16 에서 추가 예정).
            </div>
          </div>
        )}

        {/* 절대 invariant 배지 */}
        <div
          data-testid={`${testId}-invariant-badges`}
          style={{ display: "flex", flexWrap: "wrap", gap: 4 }}
        >
          {[
            "Paper 전용",
            "실거래 OFF 유지",
            "주문 권한 없음",
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

export default PaperCapitalCard;
