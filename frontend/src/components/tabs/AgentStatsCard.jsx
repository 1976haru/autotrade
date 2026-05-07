import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";

const HISTO_ORDER = ["0-25", "25-50", "50-75", "75-100"];

function pct(value, total) {
  if (!total) return 0;
  return Math.round((value / total) * 100);
}

function fmtPct(v) {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function Bar({ label, count, total, color }) {
  const w = pct(count, total);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
      <span style={{ fontSize: 10, color: "#475569", minWidth: 60 }}>{label}</span>
      <div style={{ flex: 1, background: "#0c2035", borderRadius: 3, height: 10, position: "relative" }}>
        <div style={{
          width: `${w}%`, background: color, height: "100%",
          borderRadius: 3, transition: "width .15s ease",
        }} />
      </div>
      <span style={{ fontSize: 10, color: "#94a3b8", minWidth: 40, textAlign: "right" }}>
        {count} ({w}%)
      </span>
    </div>
  );
}

export function AgentStatsCard() {
  const [stats,  setStats]  = useState(null);
  const [busy,   setBusy]   = useState(false);
  const [error,  setError]  = useState("");
  const [days,   setDays]   = useState(7);

  const load = async (lookback) => {
    setBusy(true); setError("");
    try {
      const data = await backendApi.aiAgentStats(lookback);
      setStats(data);
    } catch (e) {
      setError("Agent 통계 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const data = await backendApi.aiAgentStats(7);
        if (!cancelled) setStats(data);
      } catch (e) {
        if (!cancelled) setError("Agent 통계 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const histoTotal = stats
    ? HISTO_ORDER.reduce((acc, k) => acc + (stats.confidence_histogram?.[k] ?? 0), 0)
        + (stats.confidence_histogram_missing ?? 0)
    : 0;

  const rejectionTotal = stats
    ? Object.values(stats.top_rejection_reasons ?? {}).reduce((a, b) => a + b, 0)
    : 0;

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📊 AI Agent 통계</SectionLabel>
        <div style={{ display: "flex", gap: 4 }}>
          {[1, 7, 30].map((d) => (
            <Btn
              key={d}
              onClick={() => { setDays(d); load(d); }}
              disabled={busy}
              color={d === days ? "#7dd3fc" : "#1e293b"}
              small
            >
              {d}일
            </Btn>
          ))}
        </div>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 10 }}>
        requested_by_ai=True audit 기반 read-only 자가 평가 (lookback {days}일)
      </div>

      {error && (
        <div style={{
          color: "var(--c-danger)", fontSize: "var(--fs-sm)",
          padding: "8px 10px", background: "#fef2f2",
          border: "1px solid #fecaca", borderRadius: "var(--r-md)",
        }}>{friendlyErrorMessage(error) || "Agent 통계를 불러올 수 없어요."}</div>
      )}

      {!error && stats && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 10 }}>
            {[
              ["제안",   stats.total_proposals, "#7dd3fc"],
              ["승인율", fmtPct(stats.approval_rate), "#22c55e"],
              ["평균 conf", stats.avg_confidence != null ? Math.round(stats.avg_confidence) : "—", "#a78bfa"],
            ].map(([label, value, color]) => (
              <div key={label} style={{ textAlign: "center", padding: 8, background: "#0c2035", borderRadius: 4 }}>
                <div style={{ fontSize: 10, color: "#475569" }}>{label}</div>
                <div style={{ fontWeight: 700, color }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>Confidence 분포</div>
            {HISTO_ORDER.map((k) => (
              <Bar
                key={k}
                label={k}
                count={stats.confidence_histogram?.[k] ?? 0}
                total={histoTotal || 1}
                color="#a78bfa"
              />
            ))}
            {(stats.confidence_histogram_missing ?? 0) > 0 && (
              <Bar
                label="없음"
                count={stats.confidence_histogram_missing}
                total={histoTotal || 1}
                color="#475569"
              />
            )}
          </div>

          {rejectionTotal > 0 && (
            <div style={{ marginBottom: 10 }}>
              <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>거부 사유 (상위)</div>
              {Object.entries(stats.top_rejection_reasons).slice(0, 5).map(([k, v]) => (
                <Bar key={k} label={k} count={v} total={rejectionTotal} color="#ef4444" />
              ))}
            </div>
          )}

          {Array.isArray(stats.per_strategy) && stats.per_strategy.length > 0 && (
            <div>
              <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>Strategy별</div>
              <div style={{ background: "#0c2035", borderRadius: 4, padding: 6, fontSize: 10 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1.4fr 0.6fr 0.6fr 0.8fr 0.8fr", color: "#475569", marginBottom: 2 }}>
                  <span>strategy</span>
                  <span style={{ textAlign: "right" }}>제안</span>
                  <span style={{ textAlign: "right" }}>승인%</span>
                  <span style={{ textAlign: "right" }}>W/L</span>
                  <span style={{ textAlign: "right" }}>P/L</span>
                </div>
                {stats.per_strategy.slice(0, 8).map((row) => (
                  <div key={row.strategy} style={{
                    display: "grid",
                    gridTemplateColumns: "1.4fr 0.6fr 0.6fr 0.8fr 0.8fr",
                    color: "#94a3b8",
                  }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{row.strategy}</span>
                    <span style={{ textAlign: "right" }}>{row.total}</span>
                    <span style={{ textAlign: "right" }}>{fmtPct(row.approval_rate)}</span>
                    <span style={{ textAlign: "right" }}>
                      {row.wins ?? 0}/{row.losses ?? 0}
                    </span>
                    <span style={{
                      textAlign: "right",
                      color: (row.realized_pnl ?? 0) >= 0 ? "#22c55e" : "#ef4444",
                    }}>
                      {row.realized_pnl ?? 0}
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
