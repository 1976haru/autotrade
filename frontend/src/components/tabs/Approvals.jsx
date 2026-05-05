import { Btn, Card, SectionLabel } from "../common";
import { fmtKRW } from "../../utils/format";
import { useApprovals } from "../../store/useApprovals";


const STATUS_COLOR = {
  APPROVED:  "#22c55e",
  REJECTED:  "#ef4444",
  CANCELLED: "#94a3b8",
};


export function HistoryRow({ a }) {
  const color = STATUS_COLOR[a.status] || "#475569";
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "baseline", marginBottom: 4 }}>
        <div>
          <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>{a.symbol}</span>
          <span style={{ color: a.side === "BUY" ? "#22c55e" : "#ef4444",
                         fontSize: 10, marginLeft: 8, fontWeight: 700 }}>
            {a.side}
          </span>
          <span style={{ color: "#94a3b8", fontSize: 11, marginLeft: 8 }}>
            {a.quantity}주 · {a.order_type}
            {a.limit_price ? ` · ${fmtKRW(a.limit_price)}원` : ""}
          </span>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
          color, padding: "1px 6px", borderRadius: 3,
          border: `1px solid ${color}55`, background: `${color}15`,
        }}>
          {a.status}
        </span>
      </div>
      <div style={{ fontSize: 9, color: "#475569" }}>
        #{a.id} · {a.mode} ·{" "}
        {a.decided_at ? new Date(a.decided_at).toLocaleString("ko-KR") : "—"}
        {a.decided_by ? ` · by ${a.decided_by}` : ""}
        {a.note ? ` · ${a.note}` : ""}
      </div>
    </div>
  );
}


export function Approvals() {
  const { pending, history, loading, error, busy,
          approve, reject, cancel } = useApprovals();

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Card>
        <SectionLabel>승인 대기 큐</SectionLabel>

        {error && (
          <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>
        )}

        {loading ? (
          <div style={{ color: "#475569", fontSize: 12, textAlign: "center", padding: 16 }}>
            로딩 중…
          </div>
        ) : pending.length === 0 ? (
          <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
            승인 대기 중인 주문 없음
          </div>
        ) : pending.map((a) => (
          <div
            key={a.id}
            style={{ padding: "10px 0", borderBottom: "1px solid #05121f" }}
          >
            <div style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "baseline", marginBottom: 6,
            }}>
              <div>
                <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>
                  {a.symbol}
                </span>
                <span style={{
                  color: a.side === "BUY" ? "#22c55e" : "#ef4444",
                  fontSize: 10, marginLeft: 8, fontWeight: 700,
                }}>
                  {a.side}
                </span>
                <span style={{ color: "#94a3b8", fontSize: 11, marginLeft: 8 }}>
                  {a.quantity}주 · {a.order_type}
                  {a.limit_price ? ` · ${fmtKRW(a.limit_price)}원` : ""}
                </span>
              </div>
              <span style={{ color: "#475569", fontSize: 9 }}>#{a.id}</span>
            </div>

            <div style={{ fontSize: 10, color: "#475569", marginBottom: 8 }}>
              {a.mode} · {new Date(a.created_at).toLocaleString("ko-KR")}
            </div>

            <div style={{ display: "flex", gap: 6 }}>
              <Btn color="#22c55e" onClick={() => approve(a.id)} disabled={busy} small>
                ✓ 승인
              </Btn>
              <Btn color="#ef4444" onClick={() => reject(a.id)} disabled={busy} small>
                ✗ 거부
              </Btn>
              <Btn color="#94a3b8" onClick={() => cancel(a.id)} disabled={busy} small>
                ⊘ 취소
              </Btn>
            </div>
          </div>
        ))}
      </Card>

      <Card>
        <SectionLabel>처리 내역 (최근 50건)</SectionLabel>
        {history.length === 0 ? (
          <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
            결정된 항목이 없습니다
          </div>
        ) : history.map((a) => <HistoryRow key={a.id} a={a} />)}
      </Card>

      <div style={{ fontSize: 10, color: "#1e3a5c", lineHeight: 1.6, padding: "0 4px" }}>
        ⚠ 승인 시 백엔드 RiskManager 평가는 이미 끝난 상태이며, 승인 즉시 브로커 어댑터로 주문이 전송됩니다.
        제출 시점과 승인 시점 사이의 잔고·가격 변동은 직접 확인하세요.
        <br />
        거부(REJECTED)는 "이 주문은 안 된다"는 능동적 판단, 취소(CANCELLED)는 "신호가
        오래됐거나 더 이상 의미 없다"는 중립적 폐기입니다 — 처리 내역에서 구분됩니다.
      </div>
    </div>
  );
}
