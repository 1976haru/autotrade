/**
 * #52 / 6-07 — AI 판단 설명 카드 (read-only, 분석/설명 전용).
 *
 * 사용자가 AI 가 왜 매수/매도/보류했는지 이해할 수 있도록 entry_reason /
 * counter_reason / exit_plan / risk_flags / risk_veto / sell_reason / 최종 판단
 * 이유를 표시한다. `decision`(council dict) prop 을 주면 client-side 로 유도하고,
 * 없으면 최신 episode 의 council 을 apiClient 로 가져와 표시한다. 누락 필드는
 * fallback 문구로 대체(구버전 episode 호환).
 *
 * 절대 invariant (테스트로 lock):
 *  - 매수/매도/실전/승인/Place Order 버튼 0개, 입력 form 0개.
 *  - secret/account 원문 표시 0건.
 *  - "이 화면은 분석/설명 전용" / "주문 버튼이 아닙니다" / "실전 전환 승인과
 *    무관" / "수익을 보장하지 않습니다" 문구 노출.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";
import { buildDecisionExplanation } from "../../utils/decisionExplanation";

function _Row({ label, value, testId, warn = false }) {
  return (
    <div data-testid={testId} style={{ marginBottom: 6 }}>
      <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>{label}</div>
      <div style={{ fontSize: "var(--fs-xs)", color: warn ? "#b45309" : "var(--c-text)" }}>
        {value || "—"}
      </div>
    </div>
  );
}

export function AgentDecisionExplanationCard({
  apiClient = backendApi,
  decision = null,
  testId = "agent-decision-explanation-card",
} = {}) {
  // decision(council) prop 이 있으면 client-side 유도, 없으면 최신 episode fetch.
  const [explanation, setExplanation] = useState(
    decision ? buildDecisionExplanation(decision) : null,
  );
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(Boolean(decision));

  const refresh = useCallback(async () => {
    if (decision) {
      setExplanation(buildDecisionExplanation(decision));
      setLoaded(true);
      return;
    }
    if (typeof apiClient.agentDecisionEpisodes !== "function") { setLoaded(true); return; }
    try {
      const r = await apiClient.agentDecisionEpisodes({ limit: 1 });
      const ep = Array.isArray(r?.episodes) && r.episodes.length ? r.episodes[0] : null;
      const council = ep && typeof ep.council === "object" ? ep.council : null;
      const tphase = ep?.market_summary?.time_phase || null;
      setExplanation(buildDecisionExplanation(council, { timePhase: tphase }));
      setError("");
    } catch (e) {
      setExplanation(null);
      setError(e?.message || String(e));
    } finally {
      setLoaded(true);
    }
  }, [apiClient, decision]);

  useEffect(() => { refresh(); }, [refresh]);

  const e = explanation;

  return (
    <div data-testid={testId}>
      <Card accentColor="#8b5cf633">
        <SectionLabel>🧠 AI 판단 설명</SectionLabel>

        <div data-testid="explanation-intro" style={{
          fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginBottom: 8,
        }}>
          이 화면은 분석/설명 전용입니다. AI 가 왜 그렇게 판단했는지 보여주며,
          주문 버튼이 아닙니다. 실전 전환 승인과 무관하며, 수익을 보장하지 않습니다.
        </div>

        {error ? (
          <div data-testid="explanation-error" style={{
            fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 6,
          }}>판단 설명을 불러올 수 없습니다 (backend 연결 확인).</div>
        ) : null}

        {loaded && e == null && !error ? (
          <div data-testid="explanation-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
          }}>표시할 판단 기록이 없습니다.</div>
        ) : null}

        {e ? (
          <>
            <div data-testid="explanation-final" style={{
              padding: "5px 8px", borderRadius: 4, marginBottom: 8,
              background: "#f5f3ff", fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)",
            }}>
              최종 판단: {e.final_action_ko} ({e.final_action})
              {e.confidence != null ? ` · confidence ${(e.confidence * 100).toFixed(0)}%` : ""}
              {e.quality_score != null ? ` · quality ${e.quality_score}` : ""}
            </div>

            <_Row label="최종 판단 이유" value={e.final_reason} testId="explanation-final-reason" />
            <_Row label="진입 근거 (entry_reason)" value={e.entry_reason} testId="explanation-entry-reason" />
            <_Row label="반대 근거 (counter_reason)" value={e.counter_reason} testId="explanation-counter-reason" />
            <_Row label="청산 계획 (exit_plan)" value={e.exit_plan_text} testId="explanation-exit-plan" />
            <_Row label="리스크 플래그 (risk_flags)" value={e.risk_flags_text} testId="explanation-risk-flags" />
            <_Row label="RiskOfficer veto" value={e.risk_veto_text} testId="explanation-risk-veto"
                  warn={e.risk_veto_text.includes("veto:")} />
            {e.exit_plan_validation_text ? (
              <_Row label="ExitPlan 검증" value={e.exit_plan_validation_text}
                    testId="explanation-exit-plan-validation" warn />
            ) : null}
            {e.final_action === "SELL" ? (
              <_Row label="매도 사유 (sell_reason)" value={e.sell_reason_text}
                    testId="explanation-sell-reason" />
            ) : null}

            <div data-testid="explanation-context" style={{
              fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 6,
            }}>
              selected_strategies: {e.selected_strategies.join(", ") || "—"}
              {" · "}market_regime: {e.market_regime}
              {" · "}time_phase: {e.time_phase}
            </div>
          </>
        ) : null}

        <div data-testid="explanation-footer" style={{
          marginTop: 8, fontSize: "var(--fs-xs)", color: "var(--c-text-3)", lineHeight: 1.6,
        }}>
          이 설명은 주문 버튼이 아닙니다. 실전 전환 승인과 무관하며, 자격정보
          (계좌번호/secret)는 표시되지 않습니다.
        </div>
      </Card>
    </div>
  );
}

export default AgentDecisionExplanationCard;
