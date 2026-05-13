/**
 * 체크리스트 #75: AI Execution Activation Gate readiness card.
 *
 * CLAUDE.md 절대 원칙:
 *   1. *AI 자동매매 켜기 / ENABLE_AI_EXECUTION 토글 / 실제 주문 시작 버튼 0개*.
 *      본 카드는 readiness 평가 결과 표시만 한다.
 *   2. broker / 주문 / route_order 호출 0건.
 *   3. UI 상단 고지 강제: "이 화면은 활성화 평가만 하며 실제 모드를 켜지 않습니다."
 *   4. READY_FOR_REVIEW = 활성화 검토 가능 (활성화 자체 아님).
 *   5. 선물 AI 자동 실행은 본 카드에서 *영구* 표시: futures_allowed=false 안내.
 */

import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";


const VERDICT_COLOR = {
  READY_FOR_REVIEW: "#22c55e",
  CAUTION:          "#f59e0b",
  BLOCKED:          "#ef4444",
  UNKNOWN:          "#94a3b8",
};

const VERDICT_LABEL = {
  READY_FOR_REVIEW: "활성화 검토 가능",
  CAUTION:          "주의 / 검토 필요",
  BLOCKED:          "차단됨",
  UNKNOWN:          "측정 불가",
};


function VerdictBadge({ verdict }) {
  const color = VERDICT_COLOR[verdict] || VERDICT_COLOR.UNKNOWN;
  const label = VERDICT_LABEL[verdict] || verdict || "—";
  return (
    <span
      data-testid={`ai-execution-verdict-${verdict}`}
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


function FlagRow({ label, value }) {
  const ok = !!value;
  return (
    <div
      data-testid={`ai-execution-flag-${label}`}
      style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center",
        padding: "6px 10px",
        borderBottom: "1px solid var(--c-border)",
        fontSize: 12,
      }}
    >
      <span>{label}</span>
      <span
        style={{
          fontWeight: 700,
          color: ok ? "#22c55e" : "#ef4444",
        }}
      >
        {ok ? "✓" : "—"}
      </span>
    </div>
  );
}


export function AIExecutionGateCard({ inputOverride = null, resultOverride = null }) {
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
      const r = await backendApi.aiExecutionGateEvaluate(
        inputOverride || { strategy_name: "ai_signals" },
      );
      setResult(r);
    } catch (e) {
      setError(e?.message || "AI Execution Gate 평가 실패");
    } finally {
      setLoading(false);
    }
  };

  const verdict   = result?.verdict || "UNKNOWN";
  const metrics   = result?.metrics || {};
  const blocked   = result?.blocked_criteria || [];
  const cautions  = result?.cautions || [];
  const actions   = result?.required_actions || [];

  return (
    <Card style={{ marginBottom: 12 }} accentColor={VERDICT_COLOR[verdict]}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginBottom: 10,
      }}>
        <SectionLabel>AI Execution Gate — 활성화 readiness 평가</SectionLabel>
        <VerdictBadge verdict={verdict} />
      </div>

      <div
        data-testid="ai-execution-disclaimer"
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
        ⚠️ 이 화면은 활성화 *평가만* 하며 실제 모드를 켜지 않습니다.
        READY_FOR_REVIEW도 LIVE_AI_EXECUTION 자동 허가가 아니며,
        활성화는 별도 옵트인 PR + 사용자 명시 승인 + 초소액 canary +
        즉시 kill switch 가능 모두 필요합니다.
      </div>

      <div
        data-testid="ai-execution-futures-banner"
        style={{
          padding: "8px 12px",
          background: "#fee2e2",
          border: "1px solid #ef444455",
          color: "#b91c1c",
          borderRadius: 6,
          fontSize: 11,
          fontWeight: 700,
          marginBottom: 12,
        }}
      >
        🛑 선물 AI Execution은 본 게이트가 *영구* 허용하지 않습니다
        (futures_allowed=false). 선물은 별도 9단계 blocker + 별도 게이트 필요.
      </div>

      <button
        data-testid="ai-execution-evaluate-btn"
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
        {loading ? "평가 중…" : "활성화 검토 평가"}
      </button>

      {error ? (
        <div
          data-testid="ai-execution-error"
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
          <div style={{
            display: "grid", gridTemplateColumns: "1fr 1fr",
            gap: 8, marginBottom: 10,
          }}>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                전제 게이트
              </div>
              <FlagRow label="promotion_gate_passed"   value={metrics.promotion_gate_passed} />
              <FlagRow label="paper_gate_passed"       value={metrics.paper_gate_passed} />
              <FlagRow label="ai_assist_gate_passed"   value={metrics.ai_assist_gate_passed} />
              <FlagRow label="live_manual_gate_passed" value={metrics.live_manual_gate_passed} />
              <FlagRow label="user_explicit_opt_in"    value={metrics.user_explicit_opt_in} />
            </div>
            <div style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
            }}>
              <div style={{
                padding: "6px 10px",
                background: "var(--c-surface-2)",
                fontSize: 11, fontWeight: 700,
              }}>
                안전 인프라
              </div>
              <FlagRow label="risk_manager_active"       value={metrics.risk_manager_active} />
              <FlagRow label="order_guard_active"        value={metrics.order_guard_active} />
              <FlagRow label="ai_permission_gate_active" value={metrics.ai_permission_gate_active} />
              <FlagRow label="audit_log_complete"        value={metrics.audit_log_complete} />
              <FlagRow label="kill_switch_ready"         value={metrics.kill_switch_ready} />
              <FlagRow label="circuit_breaker_configured" value={metrics.circuit_breaker_configured} />
            </div>
          </div>

          <div data-testid="ai-execution-limits" style={{
            border: "1px solid var(--c-border)", borderRadius: 6,
            marginBottom: 10, padding: 10, fontSize: 12,
            display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 6,
          }}>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>1회 주문</div>
              <div style={{ fontWeight: 700 }}>
                {(metrics.current_max_order_notional_krw || 0).toLocaleString()} 원
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>일일 손실</div>
              <div style={{ fontWeight: 700 }}>
                {(metrics.current_max_daily_loss_krw || 0).toLocaleString()} 원
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>일일 주문 수</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.current_max_daily_order_count || 0} 건
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>보유 종목</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.current_max_open_positions || 0} 개
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>허용 종목</div>
              <div style={{ fontWeight: 700 }}>
                {metrics.allowed_symbols_count || 0} 개
              </div>
            </div>
            <div>
              <div style={{ color: "var(--c-text-3)", fontSize: 10 }}>거래 시간 (KST)</div>
              <div style={{ fontWeight: 700, fontSize: 11 }}>
                {(metrics.window_start_kst || "—") + " ~ " + (metrics.window_end_kst || "—")}
              </div>
            </div>
          </div>

          {blocked.length > 0 ? (
            <div data-testid="ai-execution-blocked-list" style={{
              border: "1px solid #ef444455", borderRadius: 6,
              marginBottom: 10, padding: 10, background: "#fee2e2",
            }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: "#b91c1c" }}>
                차단 사유 (BLOCKED)
              </div>
              {blocked.map((b, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>🛑 {b}</div>
              ))}
            </div>
          ) : null}

          {cautions.length > 0 ? (
            <div data-testid="ai-execution-caution-list" style={{
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

          {actions.length > 0 ? (
            <div data-testid="ai-execution-actions" style={{
              border: "1px solid var(--c-border)", borderRadius: 6,
              padding: 10,
            }}>
              <div style={{ fontSize: 11, fontWeight: 700 }}>필요 조치</div>
              {actions.map((a, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>📝 {a}</div>
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
        * 본 카드는 활성화 *평가*만 합니다. ENABLE_AI_EXECUTION / 실제 주문 라우팅
        활성화는 별도 옵트인 PR + 사용자 명시 승인이 필요합니다.
      </div>
    </Card>
  );
}

export default AIExecutionGateCard;
