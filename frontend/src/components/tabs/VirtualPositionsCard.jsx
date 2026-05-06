import { useEffect, useState } from "react";
import { Card, SectionLabel, Btn } from "../common";
import { backendApi } from "../../services/backend/client";
import { fmtKRW, pnlColor } from "../../utils/format";

function fmtHold(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return "—";
  const s = Math.round(seconds);
  if (s < 60)        return `${s}초`;
  if (s < 3_600)     return `${Math.floor(s / 60)}분`;
  if (s < 86_400)    return `${Math.floor(s / 3_600)}시간`;
  return `${Math.floor(s / 86_400)}일`;
}

export function VirtualPositionsCard() {
  const [rows,  setRows]  = useState([]);
  const [busy,  setBusy]  = useState(false);
  const [error, setError] = useState("");

  const load = async () => {
    setBusy(true); setError("");
    try {
      const data = await backendApi.virtualPositions();
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      setError("가상 포지션 조회 실패: " + e.message);
    }
    setBusy(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setBusy(true); setError("");
      try {
        const data = await backendApi.virtualPositions();
        if (!cancelled) setRows(Array.isArray(data) ? data : []);
      } catch (e) {
        if (!cancelled) setError("가상 포지션 조회 실패: " + e.message);
      }
      if (!cancelled) setBusy(false);
    })();
    return () => { cancelled = true; };
  }, []);

  const totalRealized = rows.reduce((s, r) => s + (r.realized_pnl || 0), 0);
  const totalUnrealized = rows.reduce((s, r) => s + (r.unrealized_pnl || 0), 0);

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📈 가상 포지션</SectionLabel>
        <Btn onClick={load} disabled={busy} color="#7dd3fc" small>
          {busy ? "⟳" : "↻ 새로고침"}
        </Btn>
      </div>
      <div style={{ fontSize: 11, color: "#475569", marginBottom: 8 }}>
        VirtualOrder FIFO 페어매칭 (148-150) — read-only.
      </div>

      {rows.length > 0 && (
        <div data-testid="virtual-positions-totals"
             style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginBottom: 8 }}>
          {[
            ["실현 PnL",   totalRealized],
            ["미실현 PnL", totalUnrealized],
          ].map(([label, v]) => (
            <div key={label} style={{ textAlign: "center", padding: 6, background: "#0c2035", borderRadius: 4 }}>
              <div style={{ fontSize: 9, color: "#475569" }}>{label}</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: pnlColor(v) }}>
                {v >= 0 ? "+" : ""}{fmtKRW(v)}
              </div>
            </div>
          ))}
        </div>
      )}

      {error && (
        <div style={{ color: "#f87171", fontSize: 12, marginBottom: 6 }}>{error}</div>
      )}

      {!error && rows.length === 0 && !busy && (
        <div style={{ fontSize: 11, color: "#64748b" }}>오픈 포지션 없음</div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {rows.map((r, i) => (
          <div key={`${r.symbol}-${r.strategy ?? "_"}-${i}`}
               data-testid="virtual-position-row"
               style={{
                 padding: "6px 8px", background: "#0c2035",
                 borderRadius: 3, fontSize: 11, display: "grid",
                 gridTemplateColumns: "1fr auto auto auto",
                 alignItems: "center", gap: 8,
               }}>
            <span>
              <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{r.symbol}</span>
              {" "}
              <span style={{ color: "#94a3b8" }}>{r.quantity}주 @ {fmtKRW(r.avg_price)}</span>
              {r.strategy && (
                <span style={{ color: "#67e8f9", fontSize: 9, marginLeft: 6 }}>
                  · {r.strategy}
                </span>
              )}
            </span>
            <span style={{ fontSize: 10, color: pnlColor(r.unrealized_pnl) }}>
              {r.unrealized_pnl >= 0 ? "+" : ""}{fmtKRW(r.unrealized_pnl)}
              <span style={{ color: "#475569", marginLeft: 4 }}>
                ({(r.unrealized_pct * 100).toFixed(1)}%)
              </span>
            </span>
            <span style={{ fontSize: 10, color: pnlColor(r.realized_pnl) }}>
              R {r.realized_pnl >= 0 ? "+" : ""}{fmtKRW(r.realized_pnl)}
            </span>
            <span style={{ fontSize: 9, color: "#475569" }}>
              {fmtHold(r.hold_seconds)}
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}
