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

import { Fragment, useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";


/** 표시용 KRW 포맷 — 1,000만원 / 3,000만원 / 5,000만원. */
function formatKrwManwon(amount) {
  const manwon = Math.round(Number(amount) / 10_000);
  return `${manwon.toLocaleString("ko-KR")}만원`;
}


// P-06: 고가주 처리 정책 — 3 옵션 + UI 라벨 + 매핑 메시지.
// backend `HighPricePolicy` enum 값과 1:1.
const HIGH_PRICE_POLICY_OPTIONS = [
  {
    key: "EXCLUDE",
    label: "제외",
    hint: "후보에서 자동 제외 (기본값 · 가장 보수적)",
    reason: "1주 가격이 투자한도 초과로 제외",
  },
  {
    key: "HOLD",
    label: "보류",
    hint: "후보에서 삭제하지 않고 보류 상태로 carry",
    reason: "1주 가격이 투자한도 초과로 보류",
  },
  {
    key: "INCREASE_BUDGET_HINT",
    label: "자금증액 안내",
    hint: "사용자에게 종목당 투자금을 늘리도록 안내",
    reason: "1주 가격이 투자한도 초과: 종목당 투자금 증액 필요",
  },
];


export function PaperCapitalCard({
  testId = "paper-capital-card",
  autoLoad = true,
}) {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pendingValue, setPendingValue] = useState(null);
  // P-05: 예시 매수 가능 수량 표.
  const [minLotPreview, setMinLotPreview] = useState(null);
  // P-06: 고가주 처리 정책 — default EXCLUDE.
  const [highPricePolicy, setHighPricePolicy] = useState("EXCLUDE");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [c, mlp] = await Promise.all([
        backendApi.paperCapitalConfig(),
        backendApi.paperMinLotPreview().catch(() => null),
      ]);
      setConfig(c);
      setMinLotPreview(mlp);
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

  const _refreshMinLotPreview = useCallback(async () => {
    try {
      const mlp = await backendApi.paperMinLotPreview();
      setMinLotPreview(mlp);
    } catch {
      // 무시 — preview 실패가 메인 흐름을 막지 않음.
    }
  }, []);

  const onSelect = useCallback(async (amount) => {
    setPendingValue(amount);
    setError("");
    try {
      const c = await backendApi.setPaperCapitalConfig({ initialCash: amount });
      setConfig(c);
      _refreshMinLotPreview();
    } catch (e) {
      setError(e?.message || "Paper 시드머니 설정 변경 실패");
    } finally {
      setPendingValue(null);
    }
  }, [_refreshMinLotPreview]);

  // P-02: 종목당 한도 옵션 — FIXED_KRW 절대값 또는 PCT_OF_EQUITY (10%) 선택.
  // 세 옵션을 단일 클릭 가능한 chip 으로 노출. 운영자가 *현재 선택* 을 한
  // 번에 알 수 있도록 mode + value 를 함께 묶어 표시한다.
  const onSelectPerSymbol = useCallback(async ({ mode, krw, pct }) => {
    setPendingValue(`per-symbol:${mode}:${krw ?? pct}`);
    setError("");
    try {
      const c = await backendApi.setPaperPerSymbolAllocation({
        mode,
        perSymbolMaxKrw: krw ?? null,
        perSymbolMaxPct: pct ?? null,
      });
      setConfig(c);
      _refreshMinLotPreview();
    } catch (e) {
      setError(e?.message || "종목당 한도 설정 변경 실패");
    } finally {
      setPendingValue(null);
    }
  }, [_refreshMinLotPreview]);

  // P-03: 최대 동시 보유 종목 수 변경.
  const onSelectMaxPositions = useCallback(async (count) => {
    setPendingValue(`max-positions:${count}`);
    setError("");
    try {
      const c = await backendApi.setPaperMaxConcurrentPositions({
        maxConcurrentPositions: count,
      });
      setConfig(c);
    } catch (e) {
      setError(e?.message || "최대 동시 보유 종목 수 설정 변경 실패");
    } finally {
      setPendingValue(null);
    }
  }, []);

  const options = config?.allowed_initial_cash_options
    || [10_000_000, 30_000_000, 50_000_000];
  const selected = config?.initial_cash;

  // P-02 — 종목당 한도 옵션 (UI 표시 3종 — 사용자 요청서 §8).
  const perSymbolOptions = [
    { key: "fixed-1m", label: "100만원", mode: "FIXED_KRW",
      krw: 1_000_000, pct: null, hint: "절대금액 100만원" },
    { key: "fixed-2m", label: "200만원", mode: "FIXED_KRW",
      krw: 2_000_000, pct: null, hint: "절대금액 200만원" },
    { key: "pct-10",   label: "시드머니의 10%", mode: "PCT_OF_EQUITY",
      krw: null, pct: 0.10,
      hint: config
        ? `현재 시드머니 ${Math.round(config.initial_cash / 10_000).toLocaleString("ko-KR")}만원 × 10%`
        : "현재 시드머니의 10%" },
  ];

  const perSymbolSelectedKey = (() => {
    if (!config) return null;
    if (config.per_symbol_mode === "PCT_OF_EQUITY"
        && config.per_symbol_max_pct === 0.10) return "pct-10";
    if (config.per_symbol_mode === "FIXED_KRW"
        && config.per_symbol_max_krw === 1_000_000) return "fixed-1m";
    if (config.per_symbol_mode === "FIXED_KRW"
        && config.per_symbol_max_krw === 2_000_000) return "fixed-2m";
    return null;
  })();

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

        {/* P-02: 종목당 최대 투자금 — 3개 chip + 현재 effective cap 표시 */}
        {config && (
          <div data-testid={`${testId}-per-symbol-section`}
               style={{
                 display: "flex", flexDirection: "column", gap: 6,
                 padding: "8px 10px",
                 background: "var(--c-surface-2, #f8fafc)",
                 border: "1px solid var(--c-border)",
                 borderRadius: 6,
               }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--c-text)" }}>
              📌 종목당 최대 투자금
            </div>
            <div
              data-testid={`${testId}-per-symbol-disclaimer`}
              style={{
                fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
              }}
            >
              이 값은 Paper 모의매매 전용이며 실전 주문금액이 아닙니다. AI 가
              한 종목에 *최대* 얼마까지 가상 매수할지 기준.
            </div>
            <div
              data-testid={`${testId}-per-symbol-options`}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                gap: 6,
              }}
            >
              {perSymbolOptions.map((opt) => {
                const isSel = perSymbolSelectedKey === opt.key;
                const isPending = pendingValue === `per-symbol:${opt.mode}:${opt.krw ?? opt.pct}`;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    data-testid={`${testId}-per-symbol-option-${opt.key}`}
                    data-selected={isSel ? "true" : "false"}
                    disabled={loading || pendingValue !== null}
                    onClick={() => onSelectPerSymbol({
                      mode: opt.mode, krw: opt.krw, pct: opt.pct,
                    })}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 5,
                      border: `1px solid ${isSel ? "#2563eb" : "var(--c-border)"}`,
                      background: isSel ? "#eff6ff" : "var(--c-surface)",
                      color: isSel ? "#1e3a8a" : "var(--c-text)",
                      fontWeight: isSel ? 700 : 500,
                      fontSize: 12,
                      cursor: loading || pendingValue !== null ? "wait" : "pointer",
                      textAlign: "center",
                      display: "flex",
                      flexDirection: "column",
                      gap: 1,
                      lineHeight: 1.3,
                    }}
                  >
                    <span>{opt.label}</span>
                    <span style={{
                      fontSize: 9, color: "var(--c-text-3)", fontWeight: 400,
                    }}>
                      {opt.hint}
                    </span>
                    {isSel && (
                      <span style={{ fontSize: 8, fontWeight: 700,
                                      color: "#2563eb" }}>✓ 선택됨</span>
                    )}
                    {isPending && (
                      <span style={{ fontSize: 8, color: "var(--c-text-3)" }}>
                        설정 중…
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div
              data-testid={`${testId}-per-symbol-effective`}
              style={{
                marginTop: 2,
                padding: "4px 8px",
                background: "var(--c-surface, #fff)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
              }}
            >
              현재 적용 한도: <b data-testid={`${testId}-per-symbol-effective-value`}>
                {formatKrwManwon(config.effective_per_symbol_cap_krw)}
              </b>
              <span style={{ fontSize: 10, color: "var(--c-text-3)",
                              marginLeft: 6, fontFamily: "monospace" }}>
                ({Number(config.effective_per_symbol_cap_krw).toLocaleString("ko-KR")} KRW
                · mode={config.per_symbol_mode})
              </span>
            </div>
          </div>
        )}

        {/* P-03: 최대 동시 보유 종목 수 — 3/5/10 chip + 현재 설정 표시 */}
        {config && (
          <div data-testid={`${testId}-max-positions-section`}
               style={{
                 display: "flex", flexDirection: "column", gap: 6,
                 padding: "8px 10px",
                 background: "var(--c-surface-2, #f8fafc)",
                 border: "1px solid var(--c-border)",
                 borderRadius: 6,
               }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--c-text)" }}>
              📊 최대 동시 보유 종목
            </div>
            <div
              data-testid={`${testId}-max-positions-disclaimer`}
              style={{
                fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
              }}
            >
              이 값은 Paper 모의매매 전용이며 실전 주문 한도가 아닙니다. 동시
              보유 종목 수가 한도에 도달하면 신규 진입(매수)만 차단되며, 청산/
              관망 판단은 영향을 받지 않습니다.
            </div>
            <div
              data-testid={`${testId}-max-positions-options`}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(80px, 1fr))",
                gap: 6,
              }}
            >
              {(config.allowed_max_concurrent_positions_options || [3, 5, 10]).map((count) => {
                const isSel = config.max_concurrent_positions === count;
                const isPending = pendingValue === `max-positions:${count}`;
                return (
                  <button
                    key={count}
                    type="button"
                    data-testid={`${testId}-max-positions-option-${count}`}
                    data-selected={isSel ? "true" : "false"}
                    disabled={loading || pendingValue !== null}
                    onClick={() => onSelectMaxPositions(count)}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 5,
                      border: `1px solid ${isSel ? "#2563eb" : "var(--c-border)"}`,
                      background: isSel ? "#eff6ff" : "var(--c-surface)",
                      color: isSel ? "#1e3a8a" : "var(--c-text)",
                      fontWeight: isSel ? 700 : 500,
                      fontSize: 12,
                      cursor: loading || pendingValue !== null ? "wait" : "pointer",
                      textAlign: "center",
                      display: "flex",
                      flexDirection: "column",
                      gap: 1,
                      lineHeight: 1.3,
                    }}
                  >
                    <span>{count}종목</span>
                    {isSel && (
                      <span style={{ fontSize: 8, fontWeight: 700,
                                      color: "#2563eb" }}>✓ 선택됨</span>
                    )}
                    {isPending && (
                      <span style={{ fontSize: 8, color: "var(--c-text-3)" }}>
                        설정 중…
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div
              data-testid={`${testId}-max-positions-current`}
              style={{
                marginTop: 2,
                padding: "4px 8px",
                background: "var(--c-surface, #fff)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
              }}
            >
              현재 설정: <b data-testid={`${testId}-max-positions-current-value`}>
                {config.max_concurrent_positions}종목
              </b>
              <span style={{ fontSize: 10, color: "var(--c-text-3)",
                              marginLeft: 6 }}>
                (한도 도달 시 신규 진입만 차단 — 청산은 자유)
              </span>
            </div>
          </div>
        )}

        {/* P-05: 현재 cap 기준 예상 매수 가능 수량 표 + floor / 소수점 불가 안내 */}
        {minLotPreview && (
          <div
            data-testid={`${testId}-min-lot-section`}
            style={{
              display: "flex", flexDirection: "column", gap: 6,
              padding: "8px 10px",
              background: "var(--c-surface-2, #f8fafc)",
              border: "1px solid var(--c-border)",
              borderRadius: 6,
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--c-text)" }}>
              🔢 최소 1주 매수 — 예상 가능 수량
            </div>
            <div
              data-testid={`${testId}-min-lot-disclaimer`}
              style={{
                fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
              }}
            >
              Paper 매수는 *정수 1주 이상* 만 허용 (소수점 주식 불가). 수량은
              항상 내림(floor) — 반올림하지 않습니다. 현재 종목당 한도{" "}
              <b>{Number(minLotPreview.effective_per_symbol_cap_krw).toLocaleString("ko-KR")} KRW</b>{" "}
              기준 예시 가격 별 매수 가능 수량 (시드머니 전체 가용 가정 —
              실제는 P-04 affordability 가 cash 잔액까지 검사).
            </div>
            <div
              data-testid={`${testId}-min-lot-examples`}
              style={{
                display: "grid",
                gridTemplateColumns: "auto 1fr auto",
                rowGap: 2,
                columnGap: 8,
                padding: "6px 8px",
                background: "var(--c-surface, #fff)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
              }}
            >
              <span style={{ fontWeight: 700, color: "var(--c-text-3)" }}>
                예시 1주 가격
              </span>
              <span style={{ fontWeight: 700, color: "var(--c-text-3)" }} />
              <span style={{ fontWeight: 700, color: "var(--c-text-3)",
                              textAlign: "right" }}>
                매수 가능 수량
              </span>
              {(minLotPreview.examples || []).map((ex) => (
                // React.Fragment 명시 사용 — 단축 <> 형은 key prop 을 받지
                // 못해 map 결과의 unique key 경고를 유발한다. 같은 ex.price
                // 를 row key 로 사용해 안정적 식별.
                <Fragment key={`ex-${ex.price}`}>
                  <span
                    data-testid={`${testId}-min-lot-example-price-${ex.price}`}
                    style={{ fontFamily: "monospace" }}
                  >
                    {Number(ex.price).toLocaleString("ko-KR")} KRW
                  </span>
                  <span
                    style={{
                      fontSize: 9,
                      color: ex.is_affordable ? "#15803d" : "#b91c1c",
                    }}
                  >
                    {ex.is_affordable ? "✓ 가능" : "✗ 1주 미만"}
                  </span>
                  <span
                    data-testid={`${testId}-min-lot-example-qty-${ex.price}`}
                    style={{
                      fontFamily: "monospace", textAlign: "right",
                      fontWeight: ex.affordable_quantity > 0 ? 700 : 400,
                      color: ex.affordable_quantity > 0
                        ? "var(--c-text)" : "var(--c-text-3)",
                    }}
                  >
                    {ex.affordable_quantity}주
                  </span>
                </Fragment>
              ))}
            </div>
            <div
              data-testid={`${testId}-min-lot-policy`}
              style={{
                fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
              }}
            >
              정책: 최소 수량 <b>{minLotPreview.min_lot_quantity}주</b> · 소수점
              주식 <b>지원 안 함</b> · 반올림 정책 <b>{minLotPreview.rounding_policy}</b>.
            </div>
          </div>
        )}

        {/* P-06: 고가주 처리 정책 — 3 옵션 chip + 선택된 정책의 사용자 메시지 */}
        {config && (
          <div
            data-testid={`${testId}-high-price-section`}
            style={{
              display: "flex", flexDirection: "column", gap: 6,
              padding: "8px 10px",
              background: "var(--c-surface-2, #f8fafc)",
              border: "1px solid var(--c-border)",
              borderRadius: 6,
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--c-text)" }}>
              📈 고가주 처리 정책
            </div>
            <div
              data-testid={`${testId}-high-price-disclaimer`}
              style={{
                fontSize: 10, color: "var(--c-text-3)", lineHeight: 1.5,
              }}
            >
              종목당 투자금으로 1주도 살 수 없는 고가주 (예: 종목당 한도
              100,000원 + 현재가 120,000원)는 AI Paper 진입 전 단계에서
              아래 정책에 따라 안전 처리합니다. Paper 전용이며 실거래 결정과
              결합되지 않습니다.
            </div>
            <div
              data-testid={`${testId}-high-price-options`}
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
                gap: 6,
              }}
            >
              {HIGH_PRICE_POLICY_OPTIONS.map((opt) => {
                const isSel = highPricePolicy === opt.key;
                return (
                  <button
                    key={opt.key}
                    type="button"
                    data-testid={`${testId}-high-price-option-${opt.key}`}
                    data-selected={isSel ? "true" : "false"}
                    onClick={() => setHighPricePolicy(opt.key)}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 5,
                      border: `1px solid ${isSel ? "#2563eb" : "var(--c-border)"}`,
                      background: isSel ? "#eff6ff" : "var(--c-surface)",
                      color: isSel ? "#1e3a8a" : "var(--c-text)",
                      fontWeight: isSel ? 700 : 500,
                      fontSize: 12,
                      cursor: "pointer",
                      textAlign: "center",
                      display: "flex",
                      flexDirection: "column",
                      gap: 1,
                      lineHeight: 1.3,
                    }}
                  >
                    <span>{opt.label}</span>
                    <span style={{
                      fontSize: 9, color: "var(--c-text-3)", fontWeight: 400,
                    }}>
                      {opt.hint}
                    </span>
                    {isSel && (
                      <span style={{ fontSize: 8, fontWeight: 700,
                                      color: "#2563eb" }}>✓ 선택됨</span>
                    )}
                    {opt.key === "EXCLUDE" && (
                      <span
                        data-testid={`${testId}-high-price-option-default-badge`}
                        style={{ fontSize: 8, color: "#475569" }}
                      >
                        기본값
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <div
              data-testid={`${testId}-high-price-current-reason`}
              style={{
                marginTop: 2,
                padding: "6px 8px",
                background: "var(--c-surface, #fff)",
                borderRadius: 4,
                fontSize: 11,
                color: "var(--c-text-2)",
                lineHeight: 1.5,
              }}
            >
              <span style={{ fontSize: 10, color: "var(--c-text-3)" }}>
                선택된 정책 적용 시 표시 사유 →{" "}
              </span>
              <b data-testid={`${testId}-high-price-current-reason-value`}>
                {(HIGH_PRICE_POLICY_OPTIONS.find(
                  (o) => o.key === highPricePolicy,
                ) || HIGH_PRICE_POLICY_OPTIONS[0]).reason}
              </b>
            </div>
          </div>
        )}

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
