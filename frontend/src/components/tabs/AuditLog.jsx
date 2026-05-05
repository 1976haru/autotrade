import { useState } from "react";
import { Btn, Card, SectionLabel } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
import {
  useAiAudits,
  useBacktestRuns,
  useOrderAudits,
} from "../../store/useAuditLogs";


const SUBTABS = [
  { id: "orders",    label: "주문" },
  { id: "ai",        label: "AI" },
  { id: "backtests", label: "백테스트" },
];


function SubTabBar({ active, onChange }) {
  return (
    <div style={{ display: "flex", gap: 4, marginBottom: 8 }}>
      {SUBTABS.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            flex: 1, padding: "8px 0", borderRadius: 4, cursor: "pointer",
            fontFamily: "inherit", fontSize: 11, fontWeight: 700,
            background: active === t.id ? "#0c2035" : "transparent",
            border:     `1px solid ${active === t.id ? "#7dd3fc" : "#1a3a5c"}`,
            color:      active === t.id ? "#7dd3fc" : "#475569",
          }}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}


function decisionColor(decision) {
  if (decision === "APPROVED")       return "#22c55e";
  if (decision === "NEEDS_APPROVAL") return "#f59e0b";
  return "#ef4444";
}


function OrderAuditView() {
  const { items, loading, error, refresh } = useOrderAudits();

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>주문 감사 로그 ({items.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : items.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>주문 기록 없음</div>
      ) : items.map((r) => (
        <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>{r.symbol}</span>
              <span style={{
                color: r.side === "BUY" ? "#22c55e" : "#ef4444",
                fontSize: 10, marginLeft: 8, fontWeight: 700,
              }}>{r.side}</span>
              <span style={{ color: "#94a3b8", fontSize: 11, marginLeft: 8 }}>
                {r.quantity}주 · {r.order_type}
              </span>
            </div>
            <span style={{ color: decisionColor(r.decision), fontSize: 10, fontWeight: 700 }}>
              {r.decision}
            </span>
          </div>
          <div style={{ fontSize: 10, color: "#475569", marginTop: 3 }}>
            {r.mode} · {new Date(r.created_at).toLocaleString("ko-KR")} ·
            {r.executed
              ? ` ${r.broker_status} ${r.filled_quantity}@${fmtKRW(r.avg_fill_price ?? 0)}`
              : " 미체결"}
          </div>
          {r.reasons.length > 0 && (
            <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>
              {r.reasons.join(" / ")}
            </div>
          )}
        </div>
      ))}
    </Card>
  );
}


function AiAuditView() {
  const { items, loading, error, refresh } = useAiAudits();

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>AI 분석 감사 로그 ({items.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : items.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>AI 호출 기록 없음</div>
      ) : items.map((r) => (
        <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <div>
              <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>{r.ticker}</span>
              {r.score && (
                <span style={{ color: "#a78bfa", fontSize: 10, marginLeft: 8, fontWeight: 700 }}>
                  total {r.score.total ?? "?"}
                </span>
              )}
            </div>
            <span style={{ color: "#475569", fontSize: 10 }}>
              tok {r.input_tokens}/{r.output_tokens}
            </span>
          </div>
          <div style={{ fontSize: 10, color: "#475569", marginTop: 3 }}>
            {new Date(r.created_at).toLocaleString("ko-KR")}
            {r.model && ` · ${r.model}`}
          </div>
          {r.error && (
            <div style={{ fontSize: 9, color: "#f87171", marginTop: 2 }}>오류: {r.error}</div>
          )}
        </div>
      ))}
    </Card>
  );
}


function BacktestRunsView() {
  const { items, loading, error, refresh } = useBacktestRuns();

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>백테스트 실행 로그 ({items.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : items.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>백테스트 실행 기록 없음</div>
      ) : items.map((r) => {
        const trades = r.win_count + r.loss_count;
        const winRate = trades > 0 ? Math.round(r.win_count / trades * 1000) / 10 : 0;
        return (
          <div key={r.id} style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <div>
                <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>
                  #{r.id} {r.strategy}
                </span>
                {r.data_symbol && (
                  <span style={{ color: "#94a3b8", fontSize: 11, marginLeft: 8 }}>
                    {r.data_symbol}
                  </span>
                )}
              </div>
              <span style={{ color: pnlColor(r.total_pnl), fontSize: 11, fontWeight: 700 }}>
                {r.total_pnl >= 0 ? "+" : ""}{fmtKRW(r.total_pnl)}
              </span>
            </div>
            <div style={{ fontSize: 10, color: "#475569", marginTop: 3 }}>
              {new Date(r.created_at).toLocaleString("ko-KR")} ·
              {` ${r.bars_processed}봉 · ${trades}거래 · 승률 ${winRate}% · MDD ${fmtKRW(r.max_drawdown)}`}
            </div>
          </div>
        );
      })}
    </Card>
  );
}


export function AuditLog() {
  const [view, setView] = useState("orders");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <SubTabBar active={view} onChange={setView} />
      {view === "orders"    && <OrderAuditView />}
      {view === "ai"        && <AiAuditView />}
      {view === "backtests" && <BacktestRunsView />}
    </div>
  );
}
