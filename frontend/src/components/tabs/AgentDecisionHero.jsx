import { useEffect, useState } from "react";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionHeader,
  StatusBadge,
} from "../common/primitives";
import { backendApi } from "../../services/backend/client";

// 232 (UI-004): Agent 판단 hero card. 사용자가 Dashboard에서 3초 안에 Agent
// 가 무엇을 추천하는지 알 수 있도록 구조화.
//
// CLAUDE.md 준수: Agent 출력은 advisory — broker 주문을 만들지 않는다.
// 모든 데이터는 백엔드의 deterministic agent 라우트에서 가져온다.

const DECISION_STATUS = {
  BUY:     { status: "success", label: "BUY",     icon: "▲" },
  SELL:    { status: "danger",  label: "SELL",    icon: "▼" },
  HOLD:    { status: "neutral", label: "HOLD",    icon: "—" },
  APPROVE: { status: "success", label: "APPROVE", icon: "✓" },
  REJECT:  { status: "danger",  label: "REJECT",  icon: "✗" },
  WARN:    { status: "warning", label: "WARN",    icon: "⚠" },
  INFO:    { status: "info",    label: "INFO",    icon: "ℹ" },
};


function _decisionMeta(d) {
  return DECISION_STATUS[d] ?? { status: "neutral", label: d || "—", icon: "·" };
}

function _isDemoBuild() {
  if (typeof import.meta === "undefined") return false;
  const v = import.meta.env?.VITE_DEMO_MODE;
  return v === "true" || v === true;
}


export function AgentDecisionHero() {
  const [chains,    setChains]    = useState(null);
  const [regime,    setRegime]    = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [error,     setError]     = useState("");
  const [loading,   setLoading]   = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [decs, rg, rd] = await Promise.all([
          backendApi.aiAgentDecisions(20),
          backendApi.marketRegime({}),
          backendApi.preMarketBrief({}),
        ]);
        if (cancelled) return;
        setChains(Array.isArray(decs) ? decs : []);
        setRegime(rg);
        setReadiness(rd);
        setError("");
      } catch (e) {
        if (!cancelled) setError(e.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return (
      <div data-testid="agent-decision-hero" style={_cardStyle()}>
        <SectionHeader sub="advisory only — 주문 권한 없음">🧠 Agent 판단</SectionHeader>
        <LoadingState title="Agent 판단 조회 중" />
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="agent-decision-hero" style={_cardStyle()}>
        <SectionHeader sub="advisory only — 주문 권한 없음">🧠 Agent 판단</SectionHeader>
        <ErrorState
          title="Agent 판단 조회 실패"
          hint={
            _isDemoBuild()
              ? "GitHub Pages 데모에서는 백엔드가 없어 mock 결과만 표시됩니다. 로컬에서 backend(uvicorn) + frontend(npm run dev)를 함께 실행하세요."
              : "백엔드 연결을 확인하세요. (uvicorn app.main:app --reload)"
          }
        />
      </div>
    );
  }

  // chief 결정만 추려 표면화 — Agent Council의 종합자.
  const chief = (chains || []).find((c) => c.agent_name === "ChiefTradingAgent");

  if (!chief) {
    return (
      <div data-testid="agent-decision-hero" style={_cardStyle()}>
        <SectionHeader sub="advisory only — 주문 권한 없음">🧠 Agent 판단</SectionHeader>
        <EmptyState
          icon="🧠"
          title="최근 Agent 판단 없음"
          hint={
            _isDemoBuild()
              ? "Demo Mode에서는 mock 판단이 표시됩니다. 시작 버튼을 누르면 운영자 의도가 RUNNING으로 전환됩니다."
              : "Agent Council이 아직 결정을 산출하지 않았습니다. AI 시그널 탭에서 분석을 실행해보세요."
          }
        />
      </div>
    );
  }

  const decisionMeta = _decisionMeta(chief.decision);
  const reasons = Array.isArray(chief.reasons) ? chief.reasons.slice(0, 3) : [];
  const conf = typeof chief.confidence === "number" ? chief.confidence : null;

  return (
    <div data-testid="agent-decision-hero" style={_cardStyle()}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <SectionHeader sub="advisory only — 주문 권한 없음">🧠 Agent 판단</SectionHeader>
        <StatusBadge status={decisionMeta.status} testId="agent-hero-decision">
          {decisionMeta.icon} {decisionMeta.label}
        </StatusBadge>
      </div>

      {/* Confidence + symbol */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--s-2)",
                    alignItems: "baseline", marginTop: "var(--s-2)" }}>
        {chief.symbol && (
          <span style={{ fontSize: "var(--fs-lg)", fontWeight: "var(--fw-bold)",
                          color: "var(--c-text)", fontFamily: "monospace" }}
                data-testid="agent-hero-symbol">
            {chief.symbol}
          </span>
        )}
        {conf !== null && (
          <span style={{ fontSize: "var(--fs-md)", color: "var(--c-text-2)" }}
                data-testid="agent-hero-confidence">
            confidence <b style={{ color: "var(--c-text)" }}>{conf}</b>
          </span>
        )}
      </div>

      {/* Reasons (top 3) */}
      {reasons.length > 0 && (
        <ul data-testid="agent-hero-reasons"
            style={{
              listStyle: "none", padding: 0, margin: "var(--s-3) 0 0",
              display: "flex", flexDirection: "column", gap: "var(--s-1)",
            }}>
          {reasons.map((r, idx) => (
            <li key={idx} style={{
              fontSize: "var(--fs-sm)", color: "var(--c-text-2)",
              padding: "4px 8px", background: "var(--c-surface-2)",
              borderRadius: "var(--r-sm)", lineHeight: "var(--lh-base)",
            }}>
              {String(r)}
            </li>
          ))}
        </ul>
      )}

      {/* Regime + readiness summary */}
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr",
        gap: "var(--s-2)", marginTop: "var(--s-3)",
      }}>
        <div style={_subCardStyle()} data-testid="agent-hero-regime">
          <div style={_subLabel()}>장세</div>
          <div style={_subValue()}>{regime?.regime || "—"}</div>
          <div style={_subSub()}>
            {regime ? `${regime.trade_permission} · 리스크 ×${(regime.risk_multiplier ?? 1).toFixed(1)}` : "—"}
          </div>
        </div>
        <div style={_subCardStyle()} data-testid="agent-hero-readiness">
          <div style={_subLabel()}>준비도</div>
          <div style={_subValue()}>{readiness?.readiness_label || "—"}</div>
          <div style={_subSub()}>
            {readiness?.readiness_score != null ? `점수 ${readiness.readiness_score}` : "—"}
          </div>
        </div>
      </div>

      <div style={{
        marginTop: "var(--s-3)", fontSize: "var(--fs-xs)",
        color: "var(--c-text-3)", lineHeight: "var(--lh-base)",
      }}>
        모든 Agent 결정은 RiskManager + PermissionGate + AgentDecisionLog를
        통과합니다. AI는 broker 주문 API를 직접 호출하지 않습니다.
      </div>
    </div>
  );
}


function _cardStyle() {
  // 238 (Light-001): light surface + subtle shadow + larger radius.
  return {
    background: "var(--c-surface)",
    border: "1px solid var(--c-border)",
    borderRadius: "var(--r-xl)",
    padding: "var(--s-5)",
    display: "flex",
    flexDirection: "column",
    gap: "var(--s-2)",
    boxShadow: "var(--sh-1)",
  };
}

function _subCardStyle() {
  return {
    background: "var(--c-surface-2)",
    padding: "var(--s-3) var(--s-4)",
    borderRadius: "var(--r-md)",
    border: "1px solid var(--c-border)",
  };
}
function _subLabel() {
  return { fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
           textTransform: "uppercase", letterSpacing: "0.06em" };
}
function _subValue() {
  return { fontSize: "var(--fs-md)", fontWeight: "var(--fw-bold)",
           color: "var(--c-text)", marginTop: 2 };
}
function _subSub() {
  return { fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
           marginTop: 2 };
}
