/**
 * Agent Council 4전략 투표 카드 — ORB/Momentum/Gap/VWAP 투표 + 최종 판단 표시.
 *
 * (기존 `AgentCouncilCard` 는 AgentDecisionLog chain 뷰어 — 본 카드는 *전략별
 * vote + 최종 BUY/SELL/HOLD* 표시 전용으로 별개.)
 *
 * **advisory only** — 주문 신호 / 실거래 권한이 아니다. "지금 매수" / "Place Order"
 * / "실거래 시작" / "ENABLE_*" 라벨 button 0개 (테스트로 lock). secret / 계좌번호
 * 표시 0건.
 *
 * props:
 *   - decision : Agent Council 결정 dict (/api/agents/council/evaluate 또는
 *                KIS auto tick 의 council 필드). 없으면 안내 문구.
 */

import { Card, SectionLabel } from "../common";

const _ACTION_COLOR = { BUY: "#16a34a", SELL: "#dc2626", HOLD: "#64748b" };

const _PROFILE_LABEL = {
  CONSERVATIVE: "보수적", BALANCED: "안정적", AGGRESSIVE: "공격적",
};

const _STRATEGY_ORDER = ["MOMENTUM", "VWAP", "ORB", "GAP"];

function _ActionBadge({ action, testid }) {
  return (
    <span
      data-testid={testid}
      style={{
        display: "inline-block", padding: "1px 8px", borderRadius: 4,
        fontWeight: "var(--fw-bold)", fontSize: "var(--fs-xs)",
        background: _ACTION_COLOR[action] || "#94a3b8", color: "#fff",
      }}
    >
      {action}
    </span>
  );
}

export function AgentCouncilVoteCard({ decision = null }) {
  if (!decision) {
    return (
      <Card data-testid="agent-council-vote-card">
        <SectionLabel>Agent Council (4전략 투표)</SectionLabel>
        <div data-testid="council-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          아직 Agent Council 판단이 없습니다. 자동매매 tick 또는 평가 실행 시
          ORB / Momentum / Gap / VWAP 투표와 최종 판단이 여기에 표시됩니다.
        </div>
      </Card>
    );
  }

  const votes = Array.isArray(decision.votes) ? decision.votes : [];
  const byStrategy = Object.fromEntries(votes.map((v) => [v.strategy, v]));
  const ordered = _STRATEGY_ORDER
    .map((s) => byStrategy[s])
    .filter(Boolean)
    .concat(votes.filter((v) => !_STRATEGY_ORDER.includes(v.strategy)));
  const finalAction = decision.final_action || "HOLD";

  return (
    <Card data-testid="agent-council-vote-card">
      <SectionLabel>Agent Council (4전략 투표 · advisory)</SectionLabel>

      <div style={{ marginBottom: 8 }} data-testid="council-badges">
        <span data-testid="council-badge-advisory"
              style={{ display: "inline-block", padding: "3px 10px", borderRadius: 6,
                       fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
                       background: "#1e3a8a", color: "#fff", marginRight: 6 }}>
          주문 신호 아님 · 실거래 OFF
        </span>
        <span data-testid="council-risk-profile"
              data-risk-profile={decision.risk_profile || "BALANCED"}
              style={{ display: "inline-block", padding: "3px 10px", borderRadius: 6,
                       fontSize: "var(--fs-xs)", background: "#0ea5e9", color: "#fff" }}>
          성향: {_PROFILE_LABEL[decision.risk_profile] || decision.risk_profile || "안정적"}
        </span>
      </div>

      <div
        data-testid="council-final"
        data-final-action={finalAction}
        style={{ marginBottom: 10, padding: "8px 10px", background: "#f8fafc",
                 border: `1px solid ${_ACTION_COLOR[finalAction]}44`, borderRadius: "var(--r-sm)" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <strong style={{ fontSize: "var(--fs-sm)" }}>최종 판단:</strong>
          <_ActionBadge action={finalAction} testid="council-final-action" />
          <span data-testid="council-confidence" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
            confidence {Math.round((decision.confidence || 0) * 100)}%
          </span>
          <span data-testid="council-quality-score" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
            quality {decision.quality_score ?? 0}
          </span>
        </div>
        {Array.isArray(decision.selected_strategies) && decision.selected_strategies.length > 0 && (
          <div data-testid="council-selected-strategies" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text)" }}>
            채택 전략: {decision.selected_strategies.join(", ")}
          </div>
        )}
        {decision.reason && (
          <div data-testid="council-reason" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            {decision.reason}
          </div>
        )}
        {Array.isArray(decision.risk_flags) && decision.risk_flags.length > 0 && (
          <div data-testid="council-risk-flags" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "#b45309" }}>
            ⚠ risk_flags: {decision.risk_flags.join(", ")}
          </div>
        )}
      </div>

      <div data-testid="council-votes">
        {ordered.map((v) => (
          <div
            key={v.strategy}
            data-testid={`council-vote-${v.strategy}`}
            data-signal={v.signal}
            style={{ fontSize: "var(--fs-xs)", padding: "4px 0",
                     borderBottom: "1px dashed #e2e8f0", display: "flex",
                     alignItems: "center", gap: 6, flexWrap: "wrap" }}
          >
            <strong style={{ minWidth: 78, display: "inline-block" }}>{v.strategy}</strong>
            <_ActionBadge action={v.signal} testid={`council-vote-signal-${v.strategy}`} />
            <span data-testid={`council-vote-score-${v.strategy}`} style={{ color: "var(--c-text-2)" }}>
              score {v.score ?? 0}
            </span>
            <span style={{ color: "var(--c-text-3)" }}>
              conf {Math.round((v.confidence || 0) * 100)}%
            </span>
            {v.reason && (
              <span data-testid={`council-vote-reason-${v.strategy}`} style={{ color: "var(--c-text-3)" }}>
                · {v.reason}
              </span>
            )}
            {Array.isArray(v.risk_flags) && v.risk_flags.length > 0 && (
              <span style={{ color: "#b45309" }}>· ⚠ {v.risk_flags.join(",")}</span>
            )}
          </div>
        ))}
      </div>

      <div data-testid="council-disclaimer" style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        Agent Council 판단은 advisory — 실제 주문은 RiskManager / PermissionGate /
        KIS Paper Gate 를 모두 통과해야 하며 broker 직접 호출 0건,
        is_live_authorization=false.
      </div>
    </Card>
  );
}

export default AgentCouncilVoteCard;
