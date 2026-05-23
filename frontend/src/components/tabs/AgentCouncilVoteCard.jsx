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
 *   - decision : Agent Council 결정 dict. 명시되면(객체/null) 그대로 사용한다.
 *                **prop 을 생략하면** 최근 episode 의 council 을 read-only 로 자동
 *                조회한다 (Dashboard mount 용). null 명시는 "데이터 없음".
 *   - apiClient: 자동 조회용 (기본 backendApi). decision 명시 시 미사용.
 *   - autoFetch: 자동 조회 on/off (기본 true).
 */

import { useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

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

function _Shell({ children }) {
  return (
    <Card data-testid="agent-council-vote-card">
      <SectionLabel>Agent Council (4전략 투표 · advisory)</SectionLabel>
      {children}
    </Card>
  );
}

export function AgentCouncilVoteCard({
  decision,
  apiClient = backendApi,
  autoFetch = true,
} = {}) {
  const [fetched, setFetched] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // decision prop 을 *생략* 한 경우에만 최근 episode 의 council 자동 조회.
  const shouldFetch = decision === undefined && autoFetch;

  useEffect(() => {
    if (!shouldFetch) return undefined;
    if (typeof apiClient.agentDecisionEpisodes !== "function") return undefined;
    let alive = true;
    setLoading(true);
    setError("");
    (async () => {
      try {
        const r = await apiClient.agentDecisionEpisodes({ limit: 1 });
        const ep = Array.isArray(r?.episodes) && r.episodes[0] ? r.episodes[0] : null;
        if (alive) setFetched(ep && ep.council ? ep.council : null);
      } catch (e) {
        if (alive) setError(e?.message || String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [shouldFetch, apiClient]);

  const data = decision !== undefined ? decision : fetched;

  if (shouldFetch && loading) {
    return (
      <_Shell>
        <div data-testid="council-loading"
             style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          Agent Council 판단 근거를 불러오는 중…
        </div>
      </_Shell>
    );
  }
  if (shouldFetch && error) {
    return (
      <_Shell>
        <div data-testid="council-error"
             style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
          판단 근거를 불러오지 못했습니다: {error}
        </div>
      </_Shell>
    );
  }
  if (!data) {
    return (
      <_Shell>
        <div data-testid="council-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          아직 Agent Council 판단이 없습니다. 자동매매 tick 또는 평가 실행 시
          ORB / Momentum / Gap / VWAP 투표와 최종 판단이 여기에 표시됩니다.
        </div>
      </_Shell>
    );
  }

  const votes = Array.isArray(data.votes) ? data.votes : [];
  const byStrategy = Object.fromEntries(votes.map((v) => [v.strategy, v]));
  const ordered = _STRATEGY_ORDER
    .map((s) => byStrategy[s])
    .filter(Boolean)
    .concat(votes.filter((v) => !_STRATEGY_ORDER.includes(v.strategy)));
  const finalAction = data.final_action || "HOLD";
  const veto = data.risk_veto_result || {};
  const exitPlan = data.exit_plan || {};
  const exitVal = data.exit_plan_validation || {};
  const _num = (v) => (v != null ? Number(v).toLocaleString("ko-KR") : null);

  return (
    <_Shell>
      <div style={{ marginBottom: 8 }} data-testid="council-badges">
        <span data-testid="council-badge-advisory"
              style={{ display: "inline-block", padding: "3px 10px", borderRadius: 6,
                       fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
                       background: "#1e3a8a", color: "#fff", marginRight: 6 }}>
          주문 신호 아님 · 실거래 OFF
        </span>
        <span data-testid="council-risk-profile"
              data-risk-profile={data.risk_profile || "BALANCED"}
              style={{ display: "inline-block", padding: "3px 10px", borderRadius: 6,
                       fontSize: "var(--fs-xs)", background: "#0ea5e9", color: "#fff" }}>
          성향: {_PROFILE_LABEL[data.risk_profile] || data.risk_profile || "안정적"}
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
            confidence {Math.round((data.confidence || 0) * 100)}%
          </span>
          <span data-testid="council-quality-score" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
            quality {data.quality_score ?? 0}
          </span>
        </div>
        {/* 2-10: buy/sell/hold 점수 합산 */}
        {(data.buy_score != null || data.sell_score != null || data.hold_score != null) && (
          <div data-testid="council-scores" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
            buy {data.buy_score ?? 0} / sell {data.sell_score ?? 0} / hold {data.hold_score ?? 0}
          </div>
        )}
        {Array.isArray(data.selected_strategies) && data.selected_strategies.length > 0 && (
          <div data-testid="council-selected-strategies" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text)" }}>
            채택 전략: {data.selected_strategies.join(", ")}
          </div>
        )}
        {data.reason && (
          <div data-testid="council-reason" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            {data.reason}
          </div>
        )}
        {Array.isArray(data.risk_flags) && data.risk_flags.length > 0 && (
          <div data-testid="council-risk-flags" style={{ marginTop: 4, fontSize: "var(--fs-xs)", color: "#b45309" }}>
            ⚠ risk_flags: {data.risk_flags.join(", ")}
          </div>
        )}
      </div>

      {/* 2-10 / 2-08: RiskOfficer veto (위험 플래그 초과로 HOLD 강등) */}
      {veto.veto_applied && (
        <div data-testid="council-risk-veto"
             style={{ marginBottom: 8, fontSize: "var(--fs-xs)", color: "#dc2626",
                      fontWeight: "var(--fw-bold)" }}>
          RiskOfficer veto: 위험 플래그 {veto.risk_flag_count}개 &gt; 허용 {veto.max_risk_flags}개
          ({veto.risk_profile}) · {veto.pre_veto_action} → HOLD 강등
        </div>
      )}

      {/* 2-10 / 2-09: exit_plan 검증 결과 */}
      {exitPlan.stop_loss != null && (
        <div data-testid="council-exit-plan"
             style={{ marginBottom: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          Exit plan: 손절 {_num(exitPlan.stop_loss)}원 / 익절 {_num(exitPlan.take_profit)}원
          {exitPlan.risk_reward_ratio != null && ` (RR ${exitPlan.risk_reward_ratio})`}
        </div>
      )}
      {exitVal.valid === false && (
        <div data-testid="council-exit-plan-veto"
             style={{ marginBottom: 8, fontSize: "var(--fs-xs)", color: "#dc2626",
                      fontWeight: "var(--fw-bold)" }}>
          BUY 차단: exit_plan {exitVal.reason_code} → HOLD 강등
        </div>
      )}

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
            <span data-testid={`council-vote-conf-${v.strategy}`} style={{ color: "var(--c-text-3)" }}>
              conf {Math.round((v.confidence || 0) * 100)}%
            </span>
            <span data-testid={`council-vote-reason-${v.strategy}`} style={{ color: "var(--c-text-3)" }}>
              · {v.reason || "사유 미기록"}
            </span>
            {Array.isArray(v.risk_flags) && v.risk_flags.length > 0 && (
              <span data-testid={`council-vote-flags-${v.strategy}`} style={{ color: "#b45309" }}>
                · ⚠ {v.risk_flags.join(",")}
              </span>
            )}
          </div>
        ))}
      </div>

      <div data-testid="council-disclaimer" style={{ marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
        본 카드는 AI 판단 근거 표시용이며 주문 버튼이 아닙니다. 실제 주문 권한과
        무관하며, 실전 전환은 별도 승인 절차가 필요합니다. Agent Council 판단은
        advisory — 실제 주문은 RiskManager / PermissionGate / KIS Paper Gate 를 모두
        통과해야 하며 broker 직접 호출 0건, is_live_authorization=false.
      </div>
    </_Shell>
  );
}

export default AgentCouncilVoteCard;
