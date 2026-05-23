import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

// Paper 가상 포트폴리오 카드 — Paper 현금 + 보유 종목 + 평가금액 + 총 자산 +
// 평가손익. 모두 *모의매매 전용* 이며 실거래 계좌와 무관 (broker 호출 0건).
//
// 데이터 source:
//   - paperCashState()   → GET /api/auto-paper/cash-state (available_cash_krw 등)
//   - virtualPositions() → GET /api/virtual/positions (FIFO 페어매칭 포지션)
//
// 실거래/매수/매도/Place Order 버튼 0개 — read-only 표시 전용.

const POLL_INTERVAL_MS = 5_000;

function _krw(n) {
  return `${Number(n || 0).toLocaleString()}원`;
}

export function PortfolioCard({
  apiClient = backendApi,
  pollIntervalMs = POLL_INTERVAL_MS,
} = {}) {
  const [cash, setCash] = useState(null);
  const [positions, setPositions] = useState([]);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const c = await apiClient.paperCashState();
      setCash(c || null);
      setError(null);
    } catch (err) {
      setError(err?.message || String(err));
    }
    if (typeof apiClient.virtualPositions === "function") {
      try {
        const p = await apiClient.virtualPositions();
        if (Array.isArray(p)) setPositions(p);
        else if (p && Array.isArray(p.positions)) setPositions(p.positions);
      } catch {
        // positions 미가용 — 조용히 무시 (현금 표시는 유지).
      }
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (!pollIntervalMs || pollIntervalMs <= 0) return undefined;
    const t = setInterval(refresh, pollIntervalMs);
    return () => clearInterval(t);
  }, [refresh, pollIntervalMs]);

  const availableCash = cash?.available_cash_krw ?? 0;
  const positionValue = positions.reduce((acc, p) => {
    const mark = p.last_price && p.last_price > 0 ? p.last_price : p.avg_price;
    return acc + Number(mark || 0) * Number(p.quantity || 0);
  }, 0);
  const unrealized = positions.reduce(
    (acc, p) => acc + Number(p.unrealized_pnl || 0), 0,
  );
  const totalEquity = availableCash + positionValue;

  return (
    <Card data-testid="portfolio-card">
      <SectionLabel>Paper 가상 포트폴리오</SectionLabel>

      <div style={{ marginBottom: 10 }} data-testid="portfolio-badges">
        <span
          data-testid="portfolio-badge-paper"
          style={{
            display: "inline-block", padding: "3px 10px", borderRadius: 6,
            fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            background: "#1e3a8a", color: "#fff", marginRight: 6,
          }}
        >
          모의 전용 · 실제 주문 아님
        </span>
        <span
          data-testid="portfolio-badge-no-broker"
          style={{
            display: "inline-block", padding: "3px 10px", borderRadius: 6,
            fontSize: "var(--fs-xs)", background: "#6b7280", color: "#fff",
          }}
        >
          broker_order_sent=false
        </span>
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 8, marginBottom: 12,
        }}
      >
        <div data-testid="portfolio-cash"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>Paper 현금</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(availableCash)}</div>
        </div>
        <div data-testid="portfolio-position-value"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>평가금액</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(positionValue)}</div>
        </div>
        <div data-testid="portfolio-total-equity"
             style={{ padding: "8px 10px", background: "#ecfeff", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>총 자산</div>
          <div style={{ fontWeight: "var(--fw-bold)" }}>{_krw(totalEquity)}</div>
        </div>
        <div data-testid="portfolio-unrealized-pnl"
             style={{ padding: "8px 10px", background: "#f1f5f9", borderRadius: "var(--r-sm)" }}>
          <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>평가손익</div>
          <div style={{
            fontWeight: "var(--fw-bold)",
            color: unrealized > 0 ? "#16a34a" : unrealized < 0 ? "#dc2626" : "var(--c-text)",
          }}>
            {unrealized >= 0 ? "+" : ""}{_krw(unrealized)}
          </div>
        </div>
      </div>

      <div data-testid="portfolio-holdings">
        <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)", marginBottom: 4, color: "var(--c-text-2)" }}>
          보유 종목 ({positions.length})
        </div>
        {positions.length === 0 ? (
          <div data-testid="portfolio-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            보유 중인 가상 포지션이 없습니다.
          </div>
        ) : (
          positions.map((p) => (
            <div
              key={`${p.symbol}-${p.strategy || ""}`}
              data-testid={`portfolio-holding-${p.symbol}`}
              style={{
                fontSize: "var(--fs-xs)", padding: "4px 0",
                borderBottom: "1px dashed #e2e8f0",
              }}
            >
              <b>{p.symbol}</b> · <span data-testid={`portfolio-qty-${p.symbol}`}>{p.quantity}주</span>
              {" · 평균 "}{_krw(p.avg_price)}
              {" · 평가 "}{_krw((p.last_price && p.last_price > 0 ? p.last_price : p.avg_price) * p.quantity)}
            </div>
          ))
        )}
      </div>

      {error && (
        <div data-testid="portfolio-error"
             style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
          {error}
        </div>
      )}

      <div data-testid="portfolio-disclaimer"
           style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        Paper 모의매매 전용 가상 포트폴리오 — 실거래 계좌와 무관하며 broker 호출 0건.
      </div>
    </Card>
  );
}

export default PortfolioCard;
