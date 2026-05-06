import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
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

function decisionDot(decision) {
  return DECISION_COLOR[decision] ?? "#64748b";
}

function groupByChain(rows) {
  const map = new Map();
  for (const r of rows) {
    const k = r.chain_id || `_solo_${r.id}`;
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(r);
  }
  return Array.from(map.entries()).map(([chain_id, items]) => ({
    chain_id,
    items: items.slice().sort((a, b) => a.id - b.id),
  }));
}

export function AgentCouncilCard() {
  const [rows,   setRows]   = useState([]);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState("");
  const [openId, setOpenId] = useState(null);

  const refresh = async () => {
    setBusy(true); setError("");
    try {
      const data = await backendApi.aiAgentDecisions(50);
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      setError("Agent 결정 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const data = await backendApi.aiAgentDecisions(50);
        if (!cancelled) setRows(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setError("Agent 결정 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const chains = groupByChain(rows);

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🤝 Agent Council 결정</SectionLabel>
        <Btn onClick={refresh} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 10 }}>
        ChiefTradingAgent + 9 member agents (deterministic stub, no LLM cost)
      </div>

      {error && (
        <div style={{ color: "#f87171", fontSize: 12, marginBottom: 8 }}>{error}</div>
      )}

      {!error && chains.length === 0 && !busy && (
        <div style={{ fontSize: 11, color: "#64748b" }}>아직 기록된 Agent 결정이 없습니다.</div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        {chains.map(({ chain_id, items }) => {
          const chief = items.find((i) => i.agent_name === "ChiefTradingAgent") ?? items[0];
          const isOpen = openId === chain_id;
          return (
            <div key={chain_id} style={{
              padding: 8, background: "#0c2035", borderRadius: 4, fontSize: 11,
            }}>
              <div
                onClick={() => setOpenId(isOpen ? null : chain_id)}
                style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", gap: 6 }}
              >
                <div style={{ display: "flex", gap: 6, alignItems: "center", minWidth: 0 }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: 4,
                    background: decisionDot(chief.decision), display: "inline-block",
                  }} />
                  <span style={{ fontWeight: 700, color: decisionDot(chief.decision) }}>
                    {chief.decision}
                  </span>
                  <span style={{ color: "#94a3b8" }}>
                    {chief.symbol ?? "-"}
                  </span>
                  <span style={{ color: "#64748b", fontSize: 10 }}>
                    conf {chief.confidence ?? "-"}
                  </span>
                </div>
                <div style={{ color: "#475569", fontSize: 10 }}>
                  {chief.created_at ? chief.created_at.replace("T", " ").slice(5, 19) : ""} · {items.length}개 결정 {isOpen ? "▾" : "▸"}
                </div>
              </div>

              {isOpen && (
                <div style={{ marginTop: 6, paddingTop: 6, borderTop: "1px solid #1e3a5c", display: "flex", flexDirection: "column", gap: 3 }}>
                  {items.map((it) => (
                    <div key={it.id} style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
                      <span style={{ color: decisionDot(it.decision), minWidth: 60, fontWeight: 600 }}>
                        {it.decision}
                      </span>
                      <span style={{ color: "#94a3b8", minWidth: 130, flexShrink: 0 }}>
                        {it.agent_name}
                      </span>
                      <span style={{ color: "#64748b", flex: 1 }}>
                        {Array.isArray(it.reasons) && it.reasons.length > 0 ? it.reasons.join(" · ") : "—"}
                      </span>
                    </div>
                  ))}
                  <div style={{ fontSize: 9, color: "#334155", marginTop: 4 }}>
                    chain_id: {chain_id}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}
