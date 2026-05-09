import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { ErrorState } from "../common/primitives";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";

// 43: Live Shadow trade summary (read-only).
//
// LIVE_SHADOW 모드에서 실 시세 기준으로 RiskManager가 통과시켰을 *추정* 후보를
// 모아 사후 분석할 수 있게 한다. 본 카드는:
//  - 전체 shadow 기록 수 / would-have-APPROVED / would-have-REJECTED 카운트
//  - 평균 추정 슬리피지 bps
//  - 절대 invariant: actual_broker_orders_sent = 0 (CLAUDE.md 절대 원칙 5/7)
//  - "실제 주문 아님 / 실 체결과 다를 수 있음" disclaimer
//
// broker.place_order는 본 surface에서 절대 호출되지 않는다 — DB SELECT만.


export function ShadowSummaryCard({ summary, loading, error, onRefresh }) {
  if (loading && !summary) {
    return (
      <Card>
        <SectionLabel>Shadow 기록</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>Shadow 기록</SectionLabel>
        <ErrorState
          title="shadow 요약 조회 실패"
          hint={friendlyErrorMessage(error)}
          onRetry={onRefresh || undefined}
          retryLabel="↻ 다시 시도"
          testId="shadow-summary-error"
        />
      </Card>
    );
  }
  if (!summary) return null;

  const total       = summary.total ?? 0;
  const wouldApp    = summary.would_have_approved_count ?? 0;
  const wouldRej    = summary.would_have_rejected_count ?? 0;
  const avgSlipBps  = summary.avg_estimated_slippage_bps ?? 0;
  const actualSent  = summary.actual_broker_orders_sent ?? 0;
  // invariant 위반 — 0이 아니면 즉시 운영자 surface (붉은색 강조).
  const invariantBroken = actualSent !== 0;
  const accent = invariantBroken ? "#ef444488" : undefined;

  return (
    <Card data-testid="shadow-summary" accentColor={accent}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>Shadow 기록</SectionLabel>
        <span data-testid="shadow-not-real-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          실제 주문 아님
        </span>
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        LIVE_SHADOW: 실제 주문 없이 신호만 기록합니다. 추정 체결가는 latest_price
        proxy로 산출되며, orderbook depth / 부분체결 / 호가 공백 / 슬리피지가
        반영되지 않아 실 체결과 다를 수 있습니다.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
                    gap: 6, marginBottom: 8 }}>
        <div data-testid="shadow-tile-total"
             style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>총 기록</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#94a3b8" }}>
            {total}
          </div>
        </div>
        <div data-testid="shadow-tile-approved"
             style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>would-have 통과</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#22c55e" }}>
            {wouldApp}
          </div>
        </div>
        <div data-testid="shadow-tile-rejected"
             style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>다른 가드 거부</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#fbbf24" }}>
            {wouldRej}
          </div>
        </div>
      </div>

      <div data-testid="shadow-tile-invariant"
           style={{ display: "flex", justifyContent: "space-between",
                    padding: "5px 8px",
                    background: invariantBroken ? "#3b1f25" : "#0c2035",
                    borderRadius: 3, fontSize: 11, marginBottom: 6 }}>
        <span style={{ color: "#94a3b8" }}>
          actual_broker_orders_sent
        </span>
        <span style={{
          color: invariantBroken ? "#fca5a5" : "#22c55e",
          fontWeight: 700, fontFamily: "monospace",
        }}>
          {actualSent}
          {invariantBroken && " ⚠ invariant 위반"}
        </span>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between",
                    fontSize: 11, color: "#94a3b8" }}>
        <span>평균 추정 슬리피지</span>
        <span style={{ fontFamily: "monospace", color: "#7dd3fc" }}>
          {Number(avgSlipBps).toFixed(2)} bps
        </span>
      </div>

      {onRefresh && (
        <div style={{ marginTop: 8, textAlign: "right" }}>
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3, cursor: "pointer",
            color: "#7dd3fc",
          }}>↻ 새로고침</button>
        </div>
      )}
    </Card>
  );
}


// 43: hook for /api/shadow/summary. ReconciliationStatusCard 패턴 그대로.
export function useShadowSummary() {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.shadowSummary();
      setSummary(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.shadowSummary();
        if (!cancelled) setSummary(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  return { summary, loading, error, refresh };
}
