/**
 * 체크리스트 #74: AI Assist Gate readiness card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. *AI 자동매매 / LIVE_AI_EXECUTION 활성화 버튼 없음* — 본 카드는 평가 결과
 *      표시만 한다.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. UI 상단 고지 강제: "본 리포트는 투자 조언이 아니라 시스템 검증 자료입니다."
 *   4. PASS != LIVE_AI_EXECUTION 허가 (UI에 명시).
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  PASS:    "#22c55e",
  CAUTION: "#f59e0b",
  FAIL:    "#ef4444",
  UNKNOWN: "#94a3b8",
};

const VERDICT_LABEL = {
  PASS:    "다음 검증 단계 가능",
  CAUTION: "주의 / 검토 필요",
  FAIL:    "추가 보완 필요",
  UNKNOWN: "측정 불가",
};


function VerdictBadge({ verdict }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.UNKNOWN;
  const label = VERDICT_LABEL[verdict] || verdict || "—";
  return (
    <span
      data-testid={`ai-assist-verdict-${verdict}`}
      style={{
        display: "inline-block",
        padding: "4px 12px",
        borderRadius: 4,
        fontSize: 12,
        fontWeight: 700,
        color,
        background: `${color}15`,
        border: `1px solid ${color}55`,
      }}
    >
      {label}
    </span>
  );
}


export function AIAssistGateCard({ inputOverride = null, resultOverride = null }) {
  const [result, setResult] = useState(resultOverride);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (resultOverride) setResult(resultOverride);
  }, [resultOverride]);

  const onEvaluate = async () => {
    if (resultOverride) return;
    setLoading(true);
    setError("");
    try {
      const r = await backendApi.aiAssistGateEvaluate(
        inputOverride || { strategy_name: "ai_signals" },
      );
      setResult(r);
    } catch (e) {
      setError(e?.message || "AI Assist Gate 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict   = result?.verdict || "UNKNOWN";
  const metrics   = result?.metrics || {};
  const failed    = result?.failed_criteria || [];
  const cautions  = result?.cautions || [];
  const tags      = result?.failure_reason_tags || {};

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>AI Assist Gate — 시스템 검증 자료</SectionLabel>
        <VerdictBadge verdict={verdict} />
      </div>

      <div
        data-testid="ai-assist-disclaimer"
        style={{
          padding: "10px 12px",
          background: "#fef3c7",
          border: "1px solid #f59e0b55",
          color: "#92400e",
          borderRadius: 6,
          fontSize: 12,
          fontWeight: 600,
          marginBottom: 12,
        }}
      >
        ⚠️ 본 리포트는 *투자 조언이 아니라* AI Assist 기능의 시스템 검증 자료입니다.
        PASS는 LIVE_AI_EXECUTION 자동 허가가 아니라 다음 검증 단계 진입 검토 가능 상태입니다.
      </div>

      <button
        data-testid="ai-assist-evaluate-btn"
        onClick={onEvaluate}
        disabled={loading || !!resultOverride}
        style={{
          padding: "8px 14px",
          borderRadius: 6,
          border: "1px solid var(--c-border-strong)",
          background: "var(--c-surface-2)",
          color: "var(--c-text)",
          cursor: loading ? "wait" : "pointer",
          fontSize: 12,
          marginBottom: 10,
        }}
      >
        {loading ? "평가 중…" : "AI Assist 품질 평가"}
      </button>

      {error ? (
        <div
          data-testid="ai-assist-error"
          style={{
            padding: 8, color: "#b91c1c", fontSize: 12,
            background: "#fee2e2", borderRadius: 4, marginBottom: 10,
          }}
        >
          {error}
        </div>
      ) : null}

      {result ? (
        <>
          <div data-testid="ai-assist-metrics" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            marginBottom: 10, padding: 10, fontSize: 12,
            display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6,
          }}>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>AI 제안 수</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.proposal_count || 0}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>승인 제안</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.approved_proposals || 0}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>Risk 거절율</div>
              <div style={{ fontWeight: 700 }}>
                {((metrics.risk_rejection_rate || 0) * 100).toFixed(1)} %
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>운영자 거절율</div>
              <div style={{ fontWeight: 700 }}>
                {((metrics.operator_rejection_rate || 0) * 100).toFixed(1)} %
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>승인 손실율</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.approved_loss_rate == null
                  ? "—"
                  : ((metrics.approved_loss_rate || 0) * 100).toFixed(1) + " %"}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>Confidence calibration</div>
              <div style={{ fontWeight: 700 }}>
                {(metrics.confidence_calibration || 0).toFixed(2)}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>Audit drift</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.ai_decision_audit_drift || 0}
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>긴급정지</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.emergency_stops_in_period || 0}
              </div>
            </div>
          </div>

          {Object.keys(tags).length > 0 ? (
            <div data-testid="ai-assist-failure-tags" style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
              marginBottom: 10,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                실패 사유 (advisory 태그)
              </div>
              {Object.entries(tags)
                .sort((a, b) => b[1] - a[1])
                .map(([k, v]) => (
                  <div key={k} style={{
                    display: "flex", justifyContent: "space-between",
                    padding: "6px 10px", fontSize: 12,
                    borderTop: "1px solid var(--c-border)",
                  }}>
                    <span style={{ fontFamily: "monospace" }}>{k}</span>
                    <span style={{ fontWeight: 700 }}>{v}</span>
                  </div>
                ))}
            </div>
          ) : null}

          {failed.length > 0 ? (
            <div data-testid="ai-assist-failed-list" style={{
              border: "1px solid #ef444455", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fee2e2",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
                미충족 기준 (FAIL)
              </div>
              {failed.map((b, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>❌ {b}</div>
              ))}
            </div>
          ) : null}

          {cautions.length > 0 ? (
            <div data-testid="ai-assist-caution-list" style={{
              border: "1px solid #f59e0b55", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fef3c7",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#92400e" }}>
                CAUTION
              </div>
              {cautions.map((c, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>⚠️ {c}</div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      <div style={{
        marginTop: 10, padding: "8px 10px",
        fontSize: 10, color: "var(--c-text-3)",
        background: "var(--c-surface-2)", borderRadius: 4,
      }}>
        * 본 카드는 AI Assist 품질 *분석*만 합니다. LIVE_AI_EXECUTION 활성화는
        AIExecutionGate(#45) + 별도 옵트인 PR + 사용자 명시 승인 모두 필요합니다.
      </div>
    </Card>
  );
}

export default AIAssistGateCard;
