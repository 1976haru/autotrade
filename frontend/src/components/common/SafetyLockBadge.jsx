/**
 * PART4-4: 안전 배지 — "🔒 실거래 차단" 을 *항상* 표시한다.
 *
 * 2026-06-01 첫 실전 모의 후, 운영자(주식 초보)가 "지금 실제 돈으로 거래되는
 * 건 아닌지" 를 한눈에 알 수 있도록 화면 최상단에 고정 배지를 둔다. 본 배지는
 * 어떤 상태/props 에서도 *사라지지 않는다* — 실거래는 기본 차단이며, 이 배지가
 * 안 보이면 안 된다 (테스트로 lock).
 *
 * 절대 invariant:
 *  - 항상 "실거래 차단" / "모의" 맥락 텍스트를 렌더한다.
 *  - 실거래 활성화 버튼/토글/입력 0개 — 순수 표시 컴포넌트.
 *  - props 로 끌 수 없다 (hidden prop 없음).
 */

export default function SafetyLockBadge({ compact = false }) {
  return (
    <div
      data-testid="safety-lock-badge"
      role="status"
      aria-label="실거래 차단 — 모의투자 전용"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: compact ? "3px 10px" : "6px 14px",
        borderRadius: 6,
        background: "#dcfce7",
        color: "#166534",
        border: "1px solid #86efac",
        fontWeight: "var(--fw-bold)",
        fontSize: compact ? "var(--fs-xs)" : "var(--fs-sm)",
        width: "fit-content",
      }}
    >
      <span aria-hidden="true">🔒</span>
      <span>실거래 차단 · 모의투자(Paper) 전용</span>
    </div>
  );
}
