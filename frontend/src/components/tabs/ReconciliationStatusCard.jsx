import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { ErrorState } from "../common/primitives";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";

// 212: Position vs broker reconciliation surface (read-only).
//
// backlog #2 — broker가 인식한 포지션과 audit log 산출 포지션 사이에
// drift가 있으면 즉시 운영자가 인지해야 한다. SIMULATION /
// PAPER 단계에서는 보통 in_sync. LIVE에서는 broker 외부 시스템(KIS)이라 drift
// 발생 가능 — 외부 수동 주문 / broker drop / 부분체결 누락 등.

const MISMATCH_KIND_LABEL = {
  quantity_mismatch: "수량 불일치",
  broker_only:       "broker만 있음",
  audit_only:        "audit만 있음",
};

const MISMATCH_KIND_COLOR = {
  quantity_mismatch: "#fbbf24",
  broker_only:       "#7dd3fc",
  audit_only:        "#a78bfa",
};

function _kindLabel(k) { return MISMATCH_KIND_LABEL[k] ?? k; }
function _kindColor(k) { return MISMATCH_KIND_COLOR[k] ?? "#64748b"; }


export function MismatchRow({ mismatch }) {
  const color = _kindColor(mismatch.kind);
  return (
    <div data-testid={`reconciliation-mismatch-${mismatch.symbol}`}
         style={{
           display: "flex", justifyContent: "space-between",
           padding: "5px 8px", background: "#0c2035",
           borderRadius: 3, fontSize: 11, gap: 8,
         }}>
      <span style={{ color: "#7dd3fc", fontFamily: "monospace" }}>
        {mismatch.symbol}
      </span>
      <span style={{ color: "#94a3b8" }}>
        broker {mismatch.broker_quantity} / audit {mismatch.audit_quantity}
      </span>
      <span style={{
        color, fontWeight: 700,
        padding: "1px 6px", borderRadius: 3,
        border: `1px solid ${color}55`, background: `${color}15`,
      }}>
        {_kindLabel(mismatch.kind)}
      </span>
    </div>
  );
}


export function ReconciliationStatusCard({ status, loading, error, onRefresh }) {
  if (loading && !status) {
    return (
      <Card>
        <SectionLabel>포지션 reconciliation</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>포지션 reconciliation</SectionLabel>
        <ErrorState
          title="reconciliation 조회 실패"
          hint={friendlyErrorMessage(error)}
          onRetry={onRefresh || undefined}
          retryLabel="↻ 다시 시도"
          testId="reconciliation-error"
        />
      </Card>
    );
  }
  if (!status) return null;

  const drift = !status.in_sync;
  const accent = drift ? "#fbbf2455" : undefined;

  return (
    <Card data-testid="reconciliation-status" accentColor={accent}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>포지션 reconciliation</SectionLabel>
        {onRefresh && (
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3, cursor: "pointer",
            color: "#7dd3fc",
          }}>↻ 새로고침</button>
        )}
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        broker가 인식한 포지션과 OrderAuditLog 산출 포지션을 비교합니다. drift가
        감지되면 broker 외부 주문 / 누락된 체결 / 시스템 동기화 문제일 수 있어
        LIVE 활성화 전 반드시 점검해야 합니다.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
                    gap: 6, marginBottom: 8 }}>
        <div data-testid="reconciliation-tile-state"
             style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>상태</div>
          <div style={{ fontSize: 13, fontWeight: 700,
                        color: drift ? "#fbbf24" : "#22c55e" }}>
            {drift ? "DRIFT" : "IN SYNC"}
          </div>
        </div>
        <div style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>broker 보유</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#7dd3fc" }}>
            {status.broker_symbol_count}
          </div>
        </div>
        <div style={{ textAlign: "center", padding: 6,
                      background: "#0c2035", borderRadius: 4 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>audit 보유</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: "#a78bfa" }}>
            {status.audit_symbol_count}
          </div>
        </div>
      </div>

      {drift ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 2 }}>
            불일치 ({(status.mismatches || []).length}건) · 일치 {status.matched_count}
          </div>
          {(status.mismatches || []).map((m) => (
            <MismatchRow key={m.symbol} mismatch={m} />
          ))}
        </div>
      ) : (
        <div style={{ fontSize: 11, color: "#22c55e" }}>
          모든 종목 일치 ({status.matched_count}건)
        </div>
      )}
    </Card>
  );
}


// 212: hook for /api/reconciliation/status. 카드 컴포넌트와 hook을 같은
// 파일에 두어 import 한 줄로 끝나도록.
export function useReconciliationStatus() {
  const [status,  setStatus]  = useState(null);
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.reconciliationStatus();
      setStatus(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.reconciliationStatus();
        if (!cancelled) setStatus(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, []);

  return { status, loading, error, refresh };
}
