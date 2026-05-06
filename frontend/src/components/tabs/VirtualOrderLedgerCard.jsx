import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { backendApi } from "../../services/backend/client";

const STATUS_ORDER = [
  "ALL", "NEW", "ACCEPTED", "PARTIALLY_FILLED",
  "FILLED", "CANCELLED", "REJECTED", "EXPIRED",
];

const STATUS_COLOR = {
  NEW:              "#7dd3fc",
  ACCEPTED:         "#a78bfa",
  PARTIALLY_FILLED: "#facc15",
  FILLED:           "#22c55e",
  CANCELLED:        "#94a3b8",
  REJECTED:         "#ef4444",
  EXPIRED:          "#64748b",
};

function statusColor(s) { return STATUS_COLOR[s] ?? "#64748b"; }

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").slice(5, 19);
}

export function VirtualOrderLedgerCard() {
  const [filter,  setFilter]  = useState("ALL");
  const [orders,  setOrders]  = useState([]);
  const [summary, setSummary] = useState(null);
  const [busy,    setBusy]    = useState(false);
  const [error,   setError]   = useState("");

  const load = async (filterStatus = filter) => {
    setBusy(true); setError("");
    try {
      const status = filterStatus === "ALL" ? null : filterStatus;
      const [list, sum] = await Promise.all([
        backendApi.virtualOrders({ limit: 50, status }),
        backendApi.virtualOrdersSummary(),
      ]);
      setOrders(Array.isArray(list) ? list : []);
      setSummary(sum);
    } catch (e) {
      setError("가상 주문 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const [list, sum] = await Promise.all([
          backendApi.virtualOrders({ limit: 50 }),
          backendApi.virtualOrdersSummary(),
        ]);
        if (!cancelled) {
          setOrders(Array.isArray(list) ? list : []);
          setSummary(sum);
        }
      } catch (e) {
        if (!cancelled) setError("가상 주문 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const onFilterClick = (status) => {
    setFilter(status);
    load(status);
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📒 가상 주문 원장</SectionLabel>
        <Btn onClick={() => load()} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 8 }}>
        VirtualOrder 라이프사이클 (148-150) — read-only.
      </div>

      {summary && (
        <div data-testid="virtual-orders-summary"
             style={{ fontSize: 10, color: "#94a3b8", marginBottom: 8 }}>
          총 {summary.total}건 · 진행 {summary.pending_count}건 · 종결 {summary.terminal_count}건
        </div>
      )}

      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 8 }}>
        {STATUS_ORDER.map((s) => {
          const count = s === "ALL"
            ? (summary?.total ?? 0)
            : (summary?.by_status?.[s] ?? 0);
          const active = filter === s;
          return (
            <button
              key={s}
              onClick={() => onFilterClick(s)}
              data-testid={`virtual-filter-${s}`}
              style={{
                fontSize: 10, padding: "3px 8px",
                background: active ? "#0c2035" : "#010a14",
                border: `1px solid ${active ? statusColor(s) : "#1e3a5c"}`,
                color: active ? statusColor(s) : "#475569",
                borderRadius: 3, cursor: "pointer",
              }}
            >
              {s}{summary != null ? ` ${count}` : ""}
            </button>
          );
        })}
      </div>

      {error && (
        <div style={{ color: "#f87171", fontSize: 12, marginBottom: 6 }}>{error}</div>
      )}

      {!error && orders.length === 0 && !busy && (
        <div style={{ fontSize: 11, color: "#64748b" }}>해당 조건의 주문 없음</div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {orders.map((o) => (
          <div key={o.id}
               data-testid="virtual-order-row"
               style={{
                 padding: "6px 8px", background: "#0c2035",
                 borderRadius: 3, fontSize: 11, display: "grid",
                 gridTemplateColumns: "auto 1fr auto auto",
                 alignItems: "center", gap: 8,
               }}>
            <span style={{
              color: statusColor(o.status), fontWeight: 700, fontSize: 10,
              minWidth: 90,
            }}>
              {o.status}
            </span>
            <span>
              <span style={{ color: o.side === "BUY" ? "#22c55e" : "#ef4444" }}>
                {o.side}
              </span>
              {" "}
              <span style={{ color: "#7dd3fc" }}>{o.symbol}</span>
              {" "}
              <span style={{ color: "#94a3b8" }}>
                {o.filled_quantity}/{o.quantity} {o.order_type}
              </span>
              {o.strategy && (
                <span style={{ color: "#67e8f9", fontSize: 9, marginLeft: 6 }}>
                  · {o.strategy}
                </span>
              )}
              {o.structured_reason && (
                <span style={{ color: "#facc15", fontSize: 9, marginLeft: 6 }}>
                  · {o.structured_reason}
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
