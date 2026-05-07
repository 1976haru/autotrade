import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { friendlyErrorMessage } from "../../utils/errorMessage";
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
  // 206: chip filter — narrows the chain list to a specific agent's rows
  // when set. "ALL" (null) is the default and shows the full chain.
  const [agentFilter, setAgentFilter] = useState(null);

  const refresh = async (filter = agentFilter) => {
    setBusy(true); setError("");
    try {
      const data = await backendApi.aiAgentDecisions(
        50, null, filter ? { agent_name: filter } : {},
      );
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

  const onAgentFilterChange = (next) => {
    setAgentFilter(next);
    refresh(next);
  };

  const chains = groupByChain(rows);

  const AGENT_FILTERS = [
    null, "ChiefTradingAgent", "MarketRegimeAgent", "StrategySelectionAgent",
    "StockSelectionAgent", "PositionSizingAgent", "RiskOfficerAgent",
    "EntryTimingAgent", "ExitTimingAgent", "NewsTrendAgent", "PostTradeReviewAgent",
  ];

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>🤝 Agent Council 결정</SectionLabel>
        <Btn onClick={() => refresh(agentFilter)} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 8 }}>
        ChiefTradingAgent + 9 member agents (deterministic stub, no LLM cost)
      </div>

      {/* 206: agent_name 필터 chip — null이면 전체. 가로 스크롤 가능. */}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 10 }}>
        {AGENT_FILTERS.map((a) => {
          const active = agentFilter === a;
          const label = a === null ? "전체" : a.replace("Agent", "");
          return (
            <button
              key={a ?? "ALL"}
              onClick={() => onAgentFilterChange(a)}
              data-testid={`agent-filter-${a ?? "ALL"}`}
              style={{
                fontSize: 9, padding: "2px 7px",
                background: active ? "#0c2035" : "#010a14",
                border: `1px solid ${active ? "#7dd3fc" : "#1e3a5c"}`,
                color: active ? "#7dd3fc" : "#475569",
                borderRadius: 3, cursor: "pointer",
              }}
            >
              {label}
            </button>
          );
        })}
      </div>

      {error && (
        <div style={{
          color: "var(--c-danger)", fontSize: "var(--fs-sm)", marginBottom: 8,
          padding: "8px 10px", background: "#fef2f2",
          border: "1px solid #fecaca", borderRadius: "var(--r-md)",
        }}>{friendlyErrorMessage(error) || "Agent 데이터를 불러올 수 없어요."}</div>
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
