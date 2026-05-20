/**
 * MarketClosedNotice — 장 종료 / 휴장 시 카드 내부에 노출되는 *advisory* 안내.
 *
 * fix/market-closed-state-distinction:
 * 사용자가 장 종료 후 desktop EXE 를 실행했을 때 카드들이 "조회 실패" 처럼
 * 보이는 문제를 해결. 본 컴포넌트는 phase (PRE_OPEN / CLOSED / WEEKEND) 에
 * 따라 친절한 문구를 노출하며, 어떤 액션 버튼도 포함하지 않는다.
 *
 * 절대 원칙:
 *  - 본 컴포넌트는 "주문 신호" 가 아니다.
 *  - "지금 매수" / "Place Order" / "BUY/SELL/HOLD" / "활성화" 버튼 0개.
 *  - 본 컴포넌트는 backend / broker / route_order 호출 0건.
 */

import {
  MarketPhase,
  marketClosedHeadline,
  marketPhaseLabel,
} from "../../utils/marketHours";


export function MarketClosedNotice({
  phase,
  testId = "market-closed-notice",
  detail = null,
  onRefresh = null,
}) {
  if (!phase || phase === MarketPhase.OPEN) return null;

  const headline = marketClosedHeadline(phase);
  const label    = marketPhaseLabel(phase);

  return (
    <div
      data-testid={testId}
      data-market-phase={phase}
      style={{
        padding: "10px 12px",
        borderRadius: 6,
        border: "1px solid var(--c-border)",
        background: "var(--c-surface-2, #f8fafc)",
        color: "var(--c-text-2)",
        fontSize: 12,
        lineHeight: 1.6,
        display: "flex",
        flexDirection: "column",
        gap: 4,
      }}
    >
      <div
        data-testid={`${testId}-headline`}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontWeight: 700,
          color: "var(--c-text)",
        }}
      >
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: 3,
            fontSize: 10,
            background: "#94a3b815",
            border: "1px solid #94a3b855",
            color: "#94a3b8",
          }}
        >
          {label}
        </span>
        <span>{headline}</span>
      </div>
      {detail ? (
        <div data-testid={`${testId}-detail`} style={{ color: "var(--c-text-3)" }}>
          {detail}
        </div>
      ) : null}
      <div
        data-testid={`${testId}-hint`}
        style={{ color: "var(--c-text-3)", fontSize: 11 }}
      >
        장 종료 / 휴장 시간에는 신규 Agent 판단 · 전략 신호가 생성되지 않습니다.
        09:00 KST 정규장 시작 후 자동으로 데이터가 갱신됩니다.
      </div>
      {onRefresh ? (
        <div style={{ marginTop: 4 }}>
          <button
            type="button"
            data-testid={`${testId}-refresh`}
            onClick={onRefresh}
            style={{
              fontSize: 11,
              padding: "3px 10px",
              borderRadius: 3,
              cursor: "pointer",
              border: "1px solid var(--c-border)",
              background: "transparent",
              color: "var(--c-text-3)",
              fontFamily: "inherit",
            }}
          >
            ↻ 다시 확인
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default MarketClosedNotice;
