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
const DECISION_ORDER = ["BUY", "SELL", "HOLD", "APPROVE", "REJECT", "WARN", "INFO"];

function decisionColor(d) { return DECISION_COLOR[d] ?? "#64748b"; }

export function summarizeAgentRows(byAgent) {
  if (!byAgent || typeof byAgent !== "object") return [];
  return Object.entries(byAgent).map(([agent, counts]) => {
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    return { agent, counts, total };
  }).sort((a, b) => b.total - a.total);
}

const LOOKBACK_OPTIONS = [
  { id: 0,  label: "전체" },
  { id: 1,  label: "1일" },
  { id: 7,  label: "7일" },
  { id: 30, label: "30일" },
];

export function AgentDecisionSummaryCard() {
  const [data,    setData]    = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");
  // 210: lookback_days chip — 0 == all time.
  const [lookback, setLookback] = useState(0);

  const load = async (days = lookback) => {
    setBusy(true); setError("");
    try {
      const d = await backendApi.aiAgentDecisionsSummary(days);
      setData(d);
    } catch (e) {
      setError("Agent 요약 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const d = await backendApi.aiAgentDecisionsSummary(0);
        if (!cancelled) setData(d);
      } catch (e) {
        if (!cancelled) setError("Agent 요약 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const onLookbackChange = (days) => {
    setLookback(days);
    load(days);
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📊 Agent 결정 분포</SectionLabel>
        <Btn onClick={() => load(lookback)} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 8 }}>
        AgentDecisionLog 집계 — 누가 어떤 결정을 얼마나 내렸는가.
      </div>

      {/* 210: lookback chip */}
      <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
        {LOOKBACK_OPTIONS.map((opt) => {
          const active = lookback === opt.id;
          return (
            <button
              key={opt.id}
              onClick={() => onLookbackChange(opt.id)}
              data-testid={`agent-summary-lookback-${opt.id}`}
              style={{
                fontSize: 10, padding: "3px 8px",
                background: active ? "#0c2035" : "#010a14",
                border: `1px solid ${active ? "#7dd3fc" : "#1e3a5c"}`,
                color: active ? "#7dd3fc" : "#475569",
                borderRadius: 3, cursor: "pointer",
              }}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      {error && (
        <div style={{
          color: "var(--c-danger)", fontSize: "var(--fs-sm)", marginBottom: 6,
          padding: "8px 10px", background: "#fef2f2",
          border: "1px solid #fecaca", borderRadius: "var(--r-md)",
        }}>{friendlyErrorMessage(error.replace(/^Agent 요약 조회 실패: /, "")) || "Agent 요약을 불러올 수 없어요."}</div>
      )}

      {!error && data && (
        <>
          <div data-testid="agent-summary-totals"
               style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 10 }}>
            {[
              ["전체 결정", data.total_decisions, "#7dd3fc"],
              ["chain 수",  data.total_chains,    "#a78bfa"],
            ].map(([label, v, color]) => (
              <div key={label} style={{ textAlign: "center", padding: 6, background: "#0c2035", borderRadius: 4 }}>
                <div style={{ fontSize: 9, color: "#475569" }}>{label}</div>
                <div style={{ fontSize: 13, fontWeight: 700, color }}>{v}</div>
              </div>
            ))}
          </div>

          {summarizeAgentRows(data.by_agent).length === 0 ? (
            <div style={{ fontSize: 11, color: "#64748b" }}>아직 누적 결정 없음</div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {summarizeAgentRows(data.by_agent).map((row) => (
                <div key={row.agent}
                     data-testid={`agent-summary-row-${row.agent}`}
                     style={{
                       padding: "6px 8px", background: "#0c2035", borderRadius: 3,
                       fontSize: 11,
                     }}>
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                    <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{row.agent}</span>
                    <span style={{ color: "#475569", fontSize: 10 }}>{row.total}건</span>
                  </div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {DECISION_ORDER.map((d) => {
                      const c = row.counts[d];
                      if (!c) return null;
                      return (
                        <span key={d} style={{
                          fontSize: 9, padding: "1px 6px", borderRadius: 3,
                          color: decisionColor(d),
                          background: decisionColor(d) + "15",
                          border: `1px solid ${decisionColor(d)}33`,
                        }}>
                          {d} {c}
                        </span>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

          {Array.isArray(data.recent_chains) && data.recent_chains.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 4 }}>최근 chain</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                {data.recent_chains.map((c) => (
                  <div key={c.chain_id}
                       data-testid={`agent-summary-chain-${c.chain_id}`}
                       style={{ fontSize: 10, color: "#64748b", display: "flex", gap: 6 }}>
                    <span style={{ color: decisionColor(c.decision), fontWeight: 700, minWidth: 50 }}>
                      {c.decision}
                    </span>
                    <span style={{ color: "#94a3b8", minWidth: 60 }}>
                      {c.symbol ?? "—"}
                    </span>
                    <span>conf {c.confidence ?? "—"}</span>
                    <span style={{ color: "#334155", marginLeft: "auto" }}>
                      {c.chain_id?.slice(0, 8)}…
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
