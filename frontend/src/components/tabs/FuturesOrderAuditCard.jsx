import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { backendApi } from "../../services/backend/client";

const DECISION_COLOR = {
  APPROVED: "#22c55e",
  REJECTED: "#ef4444",
};

function decisionColor(d) { return DECISION_COLOR[d] ?? "#94a3b8"; }

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(5, 19);
}

export function FuturesOrderAuditCard() {
  const [orders,  setOrders]  = useState([]);
  const [summary, setSummary] = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");
  const [forcedOnly, setForcedOnly] = useState(false);

  const load = async (forced = forcedOnly) => {
    setBusy(true); setError("");
    try {
      const [list, sum] = await Promise.all([
        backendApi.futuresOrders({ limit: 50, forced: forced ? true : null }),
        backendApi.futuresOrdersSummary(),
      ]);
      setOrders(Array.isArray(list) ? list : []);
      setSummary(sum);
    } catch (e) {
      setError("선물 주문 audit 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const [list, sum] = await Promise.all([
          backendApi.futuresOrders({ limit: 50 }),
          backendApi.futuresOrdersSummary(),
        ]);
        if (!cancelled) {
          setOrders(Array.isArray(list) ? list : []);
          setSummary(sum);
        }
      } catch (e) {
        if (!cancelled) setError("선물 주문 audit 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const toggleForced = () => {
    const next = !forcedOnly;
    setForcedOnly(next);
    load(next);
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📋 선물 주문 Audit</SectionLabel>
        <Btn onClick={() => load()} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 8 }}>
        FuturesOrderAuditLog (169) read-only — MockFuturesBroker 행만 누적.
      </div>

      {summary && (
        <div data-testid="futures-summary"
             style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 8 }}>
          {[
            ["주문",       summary.total,                    "#7dd3fc"],
            ["체결",       summary.executed_count,           "#22c55e"],
            ["강제청산",   summary.forced_liquidation_count, "#ef4444"],
          ].map(([label, v, color]) => (
            <div key={label} style={{ textAlign: "center", padding: 6, background: "#0c2035", borderRadius: 4 }}>
              <div style={{ fontSize: 9, color: "#475569" }}>{label}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color }}>{v}</div>
            </div>
          ))}
        </div>
      )}

      {summary && (
        <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 6 }}>
          누적 margin Δ:{" "}
          <span style={{
            color: summary.cumulative_margin_delta >= 0 ? "#22c55e" : "#ef4444",
            fontWeight: 700,
          }}>
            {summary.cumulative_margin_delta >= 0 ? "+" : ""}
            {summary.cumulative_margin_delta}
          </span>
        </div>
      )}

      <button
        onClick={toggleForced}
        data-testid="forced-only-toggle"
        style={{
          fontSize: 10, padding: "3px 8px", marginBottom: 8,
          background: forcedOnly ? "#1f0f10" : "#010a14",
          border: `1px solid ${forcedOnly ? "#ef4444" : "#1e3a5c"}`,
          color: forcedOnly ? "#ef4444" : "#475569",
          borderRadius: 3, cursor: "pointer",
        }}
      >
        {forcedOnly ? "✓ 강제청산만" : "강제청산만 보기"}
      </button>

      {error && (
        <div style={{ color: "#f87171", fontSize: 12, marginBottom: 6 }}>{error}</div>
      )}

      {!error && orders.length === 0 && !busy && (
        <div style={{ fontSize: 11, color: "#64748b" }}>해당 조건의 선물 주문 없음</div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {orders.map((o) => (
          <div key={o.id}
               data-testid="futures-order-row"
               style={{
                 padding: "6px 8px", background: "#0c2035",
                 borderRadius: 3, fontSize: 11, display: "grid",
                 gridTemplateColumns: "auto 1fr auto auto",
                 alignItems: "center", gap: 8,
               }}>
            <span style={{
              color: decisionColor(o.decision), fontWeight: 700, fontSize: 10,
              minWidth: 70,
            }}>
              {o.decision}
            </span>
            <span>
              <span style={{ color: o.side === "BUY" ? "#22c55e" : "#ef4444" }}>
                {o.side}
              </span>
              {" "}
              <span style={{ color: "#a78bfa" }}>{o.contract}</span>
              {" "}
              <span style={{ color: "#94a3b8" }}>
                {o.filled_quantity}/{o.quantity} ×{o.leverage.toFixed(1)}
              </span>
              {o.forced_liquidation && (
                <span style={{ color: "#ef4444", fontSize: 9, marginLeft: 6, fontWeight: 700 }}>
                  ⚠ FORCED
                </span>
              )}
            </span>
            <span style={{ color: "#94a3b8", fontSize: 10 }}>
              {o.avg_fill_price != null ? `@${o.avg_fill_price}` : "—"}
            </span>
            <span style={{ color: "#475569", fontSize: 9 }}>
              {fmtTime(o.created_at)} · #{o.id}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
