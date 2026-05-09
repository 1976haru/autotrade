import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 45: AIExecutionGate read-only policy card.
//
// LIVE_AI_EXECUTION 모드에서 AI 자동 주문이 어떤 조건에서 허용되는지 운영자가
// 한 화면에서 확인. **명시적으로 추가하지 않는 것**:
//  - ENABLE_AI_EXECUTION 토글 버튼
//  - 자동매매 시작 버튼
//  - canary mode 해제 버튼
//
// 본 카드는 *정보 표시만* 한다 — 정책 변경은 env 변수 + 운영자 명시 opt-in
// 절차를 통해서만 가능하다.

export function useAiExecutionPolicy() {
  const [policy,  setPolicy]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.aiExecutionPolicy();
      setPolicy(data);
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
        const data = await backendApi.aiExecutionPolicy();
        if (!cancelled) setPolicy(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  return { policy, loading, error, refresh };
}


function _Row({ label, value, mono = false, color }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "4px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{
        color:      color || "#e2e8f0",
        fontFamily: mono ? "monospace" : "inherit",
        fontWeight: 700,
      }}>
        {value}
      </span>
    </div>
  );
}


export function AiExecutionPolicyCard({ policy, loading, error }) {
  if (loading && !policy) {
    return (
      <Card>
        <SectionLabel>AI 자동 실행 정책</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>AI 자동 실행 정책</SectionLabel>
        <div data-testid="ai-execution-policy-error"
             style={{ fontSize: 11, color: "#f87171" }}>{error}</div>
      </Card>
    );
  }
  if (!policy) return null;

  const disabled = !!policy.live_ai_execution_disabled;
  const canary   = !!policy.is_canary_mode;

  // status banner: 비활성(default) > canary > active
  const statusLabel =
    disabled ? "비활성 (기본값)" :
    canary   ? "canary 모드 (실 주문 없음)" :
              "활성";
  const statusColor =
    disabled ? "#94a3b8" :
    canary   ? "#fbbf24" :
              "#22c55e";

  return (
    <Card data-testid="ai-execution-policy-card"
          accentColor={disabled ? "#94a3b855" : (canary ? "#fbbf2455" : undefined)}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>AI 자동 실행 정책</SectionLabel>
        <span data-testid="ai-execution-status-badge"
              style={{
                fontSize: 9, fontWeight: 700, color: statusColor,
                padding: "1px 6px", borderRadius: 3,
                border: `1px solid ${statusColor}66`,
                background: `${statusColor}15`,
              }}>
          {statusLabel}
        </span>
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        {policy.notice}
      </div>

      <_Row label="ENABLE_AI_EXECUTION"
             value={policy.enable_ai_execution ? "true" : "false"}
             mono
             color={policy.enable_ai_execution ? "#22c55e" : "#94a3b8"} />
      <_Row label="ENABLE_LIVE_TRADING"
             value={policy.enable_live_trading ? "true" : "false"}
             mono
             color={policy.enable_live_trading ? "#22c55e" : "#94a3b8"} />
      <_Row label="canary mode"
             value={policy.is_canary_mode ? "ON (기록만)" : "OFF"}
             color={policy.is_canary_mode ? "#fbbf24" : "#94a3b8"} />
      <_Row label="최소 confidence" value={`${policy.min_confidence}/100`} mono />
      <_Row label="최소 quality_score" value={`${policy.min_quality_score}/100`} mono />
      <_Row label="설명 필수"
             value={policy.require_explanation ? "예" : "아니오"} />
      <_Row label="exit plan 필수"
             value={policy.require_exit_plan ? "예 (target+stop)" : "아니오"} />
      <_Row label="1회 주문 한도"
             value={`${(policy.max_notional_per_order || 0).toLocaleString()}원`}
             mono />
      <_Row label="일일 한도"
             value={`${policy.max_orders_per_day}건/일`}
             mono />
      <_Row label="실행 시간창 (KST)"
             value={`${String(policy.window_start_hour_kst).padStart(2, "0")}:00 — `
                    + `${String(policy.window_end_hour_kst).padStart(2, "0")}:00`}
             mono />
      <_Row label="허용 종목"
             value={
               policy.symbol_whitelist && policy.symbol_whitelist.length > 0
                 ? policy.symbol_whitelist.join(" · ")
                 : "(비어 있음 — 모든 종목 차단)"
             }
             mono
             color={
               (policy.symbol_whitelist && policy.symbol_whitelist.length > 0)
                 ? "#7dd3fc" : "#94a3b8"
             } />

      {disabled && (
        <div data-testid="ai-execution-disabled-disclaimer"
             style={{ marginTop: 8, padding: "6px 8px",
                       background: "#0c2035", border: "1px solid #94a3b855",
                       borderRadius: 4, color: "#94a3b8", fontSize: 11 }}>
          ⚠ AI 자동 실행은 기본 비활성화입니다. ENABLE_AI_EXECUTION +
          ENABLE_LIVE_TRADING + 운영자 명시 opt-in이 모두 필요합니다.
          본 화면에는 활성화 토글이 의도적으로 제공되지 않습니다.
        </div>
      )}

      {!disabled && canary && (
        <div data-testid="ai-execution-canary-disclaimer"
             style={{ marginTop: 8, padding: "6px 8px",
                       background: "#3b2a05", border: "1px solid #fbbf2466",
                       borderRadius: 4, color: "#fbbf24", fontSize: 11 }}>
          ⓘ canary 모드: AI 자동 실행 후보가 모두 통과해도 실제 주문은
          나가지 않습니다 (decision=CANARY_ONLY). 1~2주 canary 운용 후
          결과 비교 후에만 해제합니다.
        </div>
      )}
    </Card>
  );
}
