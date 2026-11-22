// UI-revamp STEP 1 (2026-06-04): 홈 최상단 "지금 상태" 요약 바.
// 여러 카드가 서로 다른 상태(봇 RUNNING vs 루프 정지)를 보여주던 혼란을, 단일
// computeTradingStatus 판정 + 오늘 핵심 숫자 한 줄로 정리한다. 안전 배지/문구는
// 건드리지 않으며(별도 카드 유지), 본 바는 *요약 추가*다.
import { fmtKRW, nowKstHm } from "../../utils/format";
import { tradingStatusHeadline } from "../../utils/tradingStatus";

/**
 * 순수 표시 컴포넌트.
 * @param status computeTradingStatus(...) 결과 {state, icon, color, label, reason}
 * @param today  {orderCount, filledCount, realizedPnl|null}
 * @param kstTime "HH:MM" (미지정 시 현재 KST)
 */
export function TradingStatusBar({ status, today = {}, kstTime, onJumpTab }) {
  if (!status) return null;
  const { orderCount = 0, filledCount = 0, realizedPnl = null } = today;
  const t = kstTime || nowKstHm();
  const pnlStr =
    realizedPnl == null ? "—" : `${realizedPnl >= 0 ? "+" : ""}${fmtKRW(Math.round(realizedPnl))}원`;
  const pnlColor =
    realizedPnl == null ? "var(--c-text-3)" : realizedPnl >= 0 ? "#22c55e" : "#ef4444";

  return (
    <div
      data-testid="trading-status-bar"
      style={{
        display: "flex", alignItems: "center", flexWrap: "wrap", gap: 12,
        padding: "12px 16px", borderRadius: "var(--r-lg)",
        background: `${status.color}12`,
        border: `1px solid ${status.color}55`,
        boxShadow: "var(--sh-1)",
      }}
    >
      {/* 단일 상태 — 거래 중 / 대기 / 정지 / 거래 불가(사유) */}
      <button
        type="button"
        onClick={onJumpTab ? () => onJumpTab("bot") : undefined}
        data-testid="trading-status-headline"
        style={{
          display: "flex", alignItems: "center", gap: 8,
          background: "transparent", border: "none", padding: 0,
          fontFamily: "inherit", cursor: onJumpTab ? "pointer" : "default",
          color: status.color, fontWeight: "var(--fw-bold)", fontSize: "var(--fs-md)",
        }}
      >
        <span style={{ fontSize: 18 }}>{status.icon}</span>
        <span>
          {status.label}
          {status.reason ? (
            <span style={{ fontWeight: 600, opacity: 0.9 }}> ({status.reason})</span>
          ) : null}
        </span>
      </button>

      <div style={{ flex: 1 }} />

      {/* 오늘 핵심 숫자 */}
      <div data-testid="trading-status-today" style={{
        display: "flex", gap: 14, fontSize: "var(--fs-sm)", color: "var(--c-text-3)",
        whiteSpace: "nowrap",
      }}>
        <span>오늘 주문 <b style={{ color: "var(--c-text)" }}>{orderCount}</b>건</span>
        <span>체결 <b style={{ color: filledCount > 0 ? "#22c55e" : "var(--c-text)" }}>{filledCount}</b>건</span>
        <span>실현손익 <b style={{ color: pnlColor }}>{pnlStr}</b></span>
        <span style={{ color: "var(--c-text-4)" }} data-testid="trading-status-kst">KST {t}</span>
      </div>
    </div>
  );
}
