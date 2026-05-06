import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

const DECISION_COLOR = {
  BUY:     "#22c55e",
  SELL:    "#ef4444",
  HOLD:    "#94a3b8",
  APPROVE: "#22c55e",
  REJECT:  "#ef4444",
  WARN:    "#facc15",
  INFO:    "#7dd3fc",
};

function decisionColor(d) { return DECISION_COLOR[d] ?? "#64748b"; }

export function pickLatestChiefDecision(rows) {
  if (!Array.isArray(rows)) return null;
  for (const r of rows) {
    if (r.agent_name === "ChiefTradingAgent") return r;
  }
  return rows[0] ?? null;
}

export function formatRelativeAge(iso, now = Date.now()) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const diffSec = Math.max(0, Math.round((now - t) / 1000));
  if (diffSec < 60)        return `${diffSec}초 전`;
  if (diffSec < 3600)      return `${Math.floor(diffSec / 60)}분 전`;
  if (diffSec < 86_400)    return `${Math.floor(diffSec / 3600)}시간 전`;
  return `${Math.floor(diffSec / 86_400)}일 전`;
}

export function AgentLatestTile({ onJumpTab }) {
  const [latest, setLatest] = useState(null);
  const [error,  setError]  = useState("");
  const [busy,   setBusy]   = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const data = await backendApi.aiAgentDecisions(20);
        if (!cancelled) setLatest(pickLatestChiefDecision(data));
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  if (busy && !latest) {
    return (
      <Card>
        <SectionLabel>🤝 Agent 최근 결정</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <SectionLabel>🤝 Agent 최근 결정</SectionLabel>
        <div style={{ fontSize: 11, color: "#f87171" }}>조회 실패: {error}</div>
      </Card>
    );
  }

  if (!latest) {
    return (
      <Card>
        <SectionLabel>🤝 Agent 최근 결정</SectionLabel>
        <div style={{ fontSize: 11, color: "#64748b" }}>최근 Agent 결정 없음</div>
      </Card>
    );
  }

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <SectionLabel>🤝 Agent 최근 결정</SectionLabel>
        {onJumpTab && (
          <button
            data-testid="agent-latest-jump"
            onClick={() => onJumpTab("ai")}
            style={{
              fontSize: 10, color: "#7dd3fc", background: "none",
              border: "none", cursor: "pointer", padding: 2,
            }}
          >
            상세 →
          </button>
        )}
      </div>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "8px 10px", background: "#0c2035", borderRadius: 4,
      }}>
        <div data-testid="agent-latest-decision" style={{
          fontSize: 18, fontWeight: 700, color: decisionColor(latest.decision),
          minWidth: 60,
        }}>
          {latest.decision}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            <span style={{ color: "#7dd3fc", fontWeight: 700 }}>
              {latest.symbol ?? "-"}
            </span>
            {latest.confidence != null && (
              <span style={{ marginLeft: 8, color: "#64748b", fontSize: 10 }}>
                conf {latest.confidence}
              </span>
            )}
          </div>
          <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
            {formatRelativeAge(latest.created_at)}
            {latest.agent_name !== "ChiefTradingAgent" && (
              <> · {latest.agent_name}</>
            )}
          </div>
        </div>
      </div>
      {Array.isArray(latest.reasons) && latest.reasons.length > 0 && (
        <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 6 }}>
          {latest.reasons.slice(0, 2).join(" · ")}
        </div>
      )}
    </Card>
  );
}
