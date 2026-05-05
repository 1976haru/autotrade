import { useState } from "react";

import { Btn, Card, SectionLabel } from "../common";
import { DecisionDialog } from "../common/DecisionDialog";
import { fmtKRW } from "../../utils/format";


const STATUS_COLOR = {
  APPROVED:  "#22c55e",
  REJECTED:  "#ef4444",
  CANCELLED: "#94a3b8",
};


// 결재 액션별 라벨/색상. 모달과 dispatch 분기에서 함께 쓴다.
const ACTION_META = {
  approve: { label: "주문 승인", confirmLabel: "✓ 승인", color: "#22c55e" },
  reject:  { label: "주문 거부", confirmLabel: "✗ 거부", color: "#ef4444" },
  cancel:  { label: "주문 취소", confirmLabel: "⊘ 취소", color: "#94a3b8" },
};


// 단타 운영에서 결재 지연은 기회 손실로 직결. PENDING 행에 상대 시간 배지를
// 붙여 신호 노후화를 즉시 인지시킨다 — 10분 이상이면 stale로 간주해 강조.
// useApprovals가 5초마다 폴링하며 리렌더하므로 별도 타이머 없이 자동 갱신.
const PENDING_STALE_THRESHOLD_MS = 10 * 60 * 1000;
const _MIN  = 60_000;
const _HOUR = 60 * _MIN;
const _DAY  = 24 * _HOUR;

export function formatPendingAge(createdAtIso, now = Date.now()) {
  const elapsed = Math.max(0, now - new Date(createdAtIso).getTime());
  if (elapsed < 30_000) return "방금";
  if (elapsed < _HOUR)  return `${Math.floor(elapsed / _MIN)}분 전`;
  if (elapsed < _DAY)   return `${Math.floor(elapsed / _HOUR)}시간 전`;
  return `${Math.floor(elapsed / _DAY)}일 전`;
}

export function isPendingStale(createdAtIso, now = Date.now()) {
  return (now - new Date(createdAtIso).getTime()) >= PENDING_STALE_THRESHOLD_MS;
}

export function PendingAgeBadge({ createdAt, now }) {
  const stale = isPendingStale(createdAt, now);
  return (
    <span
      data-testid="pending-age-badge"
      data-stale={stale ? "true" : "false"}
      style={{
        fontSize:     9,
        fontWeight:   700,
        marginLeft:   6,
        padding:      "1px 5px",
        borderRadius: 3,
        color:        stale ? "#f59e0b" : "#64748b",
        border:       `1px solid ${stale ? "#f59e0b55" : "#1a3a5c"}`,
        background:   stale ? "#f59e0b15" : "transparent",
      }}
    >
      {stale ? "⚠ " : ""}{formatPendingAge(createdAt, now)}
    </span>
  );
}


// RiskManager 사유 표시 — PENDING/HistoryRow에서 공유. 운영자가 결정 전 컨텍스트
// (예: "max_order_notional 초과", "manual approval required")를 즉시 본다.
export function ReasonsLine({ reasons }) {
  if (!reasons || reasons.length === 0) return null;
  return (
    <div style={{
      fontSize: 9, color: "#94a3b8", marginTop: 4, lineHeight: 1.4,
      paddingLeft: 6, borderLeft: "2px solid #1a3a5c",
    }}>
      <span style={{ color: "#64748b", marginRight: 4 }}>사유:</span>
      {reasons.join(" / ")}
    </div>
  );
}


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
        {a.decided_at ? (
          <>
            {new Date(a.decided_at).toLocaleString("ko-KR")}{" "}
            <span style={{ color: "#64748b" }}>({formatPendingAge(a.decided_at)})</span>
          </>
        ) : "—"}
        {a.decided_by ? ` · by ${a.decided_by}` : ""}
        {a.note ? ` · ${a.note}` : ""}
      </div>
      <ReasonsLine reasons={a.reasons} />
    </div>
  );
}


function _OrderSummary({ approval }) {
  return (
    <div style={{
      fontSize: 11, color: "#94a3b8", padding: "8px 10px", marginBottom: 10,
      background: "#010a14", border: "1px solid #0c2035", borderRadius: 4,
    }}>
      <div>
        <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{approval.symbol}</span>
        <span style={{
          color: approval.side === "BUY" ? "#22c55e" : "#ef4444",
          fontSize: 10, marginLeft: 8, fontWeight: 700,
        }}>
          {approval.side}
        </span>
        <span style={{ marginLeft: 8 }}>
          {approval.quantity}주 · {approval.order_type}
          {approval.limit_price ? ` · ${fmtKRW(approval.limit_price)}원` : ""}
        </span>
      </div>
      <div style={{ fontSize: 9, color: "#475569", marginTop: 2 }}>
        #{approval.id} · {approval.mode}
      </div>
    </div>
  );
}


export function ApprovalDecisionModal({
  action, approval, busy, defaultDecidedBy = "", onConfirm, onCancel,
}) {
  const meta = ACTION_META[action];
  return (
    <DecisionDialog
      title={meta.label}
      accent={meta.color}
      cancelLabel="닫기"
      confirmLabel={meta.confirmLabel}
      summary={<_OrderSummary approval={approval} />}
      description="감사 추적을 위해 운영자명과 사유를 남겨주세요. 둘 다 선택 사항이지만, 기록된 값은 영구 저장되어 사고 분석 시 사용됩니다."
      notePlaceholder="예: 신호 노후, 잔고 부족"
      busy={busy}
      defaultDecidedBy={defaultDecidedBy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}


function _StaleApprovalList({ stale }) {
  // Operator should be able to verify what they're about to dispose. Show up
  // to 5 rows; collapse the rest into "외 N건" so the modal stays bounded.
  const visible = stale.slice(0, 5);
  const overflow = stale.length - visible.length;
  return (
    <div style={{
      fontSize: 11, color: "#94a3b8", padding: "8px 10px", marginBottom: 10,
      background: "#010a14", border: "1px solid #0c2035", borderRadius: 4,
      maxHeight: 140, overflowY: "auto",
    }}>
      {visible.map((a) => (
        <div key={a.id} style={{ display: "flex", justifyContent: "space-between",
                                   fontSize: 10, padding: "2px 0" }}>
          <span>
            <span style={{ color: "#7dd3fc", fontWeight: 700 }}>{a.symbol}</span>
            <span style={{ marginLeft: 6, color: a.side === "BUY" ? "#22c55e" : "#ef4444" }}>
              {a.side}
            </span>
            <span style={{ marginLeft: 6 }}>{a.quantity}주</span>
          </span>
          <span style={{ color: "#f59e0b" }}>{formatPendingAge(a.created_at)}</span>
        </div>
      ))}
      {overflow > 0 && (
        <div style={{ fontSize: 9, color: "#475569", marginTop: 4, textAlign: "center" }}>
          외 {overflow}건
        </div>
      )}
    </div>
  );
}


export function BulkCancelStaleModal({
  approvals: stale, busy, defaultDecidedBy = "", onConfirm, onCancel,
}) {
  return (
    <DecisionDialog
      title={`stale 일괄 취소 (${stale.length}건)`}
      ariaLabel="stale 일괄 취소"
      accent="#94a3b8"
      cancelLabel="닫기"
      confirmLabel={`⊘ ${stale.length}건 취소`}
      summary={<_StaleApprovalList stale={stale} />}
      description={'모든 행이 같은 운영자명/사유로 CANCELLED 처리됩니다. 거부(REJECTED)와 달리 취소는 "신호 노후" 같은 중립적 폐기를 의미합니다.'}
      notePlaceholder="예: stale 신호 일괄 정리"
      busy={busy}
      defaultDecidedBy={defaultDecidedBy}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}


export function Approvals({ approvals, operatorName = "" }) {
  // useApprovals는 App에서 lift되어 prop으로 전달된다 — BottomNav 배지가 같은
  // 폴링 결과를 공유하기 위해서. 테스트는 모킹 없이 prop만 직접 주입.
  const { pending, history, loading, error, busy,
          approve, reject, cancel, cancelMany } = approvals;
  // 결재 모달 대상: { action, approval } | null. 같은 모달 컴포넌트를 세 액션에서
  // 공유하고, 액션은 ACTION_META에서 분기한다.
  const [decisionTarget, setDecisionTarget] = useState(null);
  const [bulkOpen, setBulkOpen] = useState(false);

  const dispatchByAction = { approve, reject, cancel };

  // 058 isPendingStale 기준(10분+) — 062 status pin escalation과 같은 임계값.
  // 일괄 취소 버튼은 여기 매칭되는 항목이 있을 때만 노출.
  const staleApprovals = pending.filter((a) => isPendingStale(a.created_at));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between",
                       alignItems: "center", marginBottom: 8 }}>
          <SectionLabel>승인 대기 큐</SectionLabel>
          {staleApprovals.length > 0 && (
            <Btn
              color="#f59e0b"
              onClick={() => setBulkOpen(true)}
              disabled={busy}
              small
            >
              📦 stale 일괄 취소 ({staleApprovals.length})
            </Btn>
          )}
        </div>

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
              <div style={{ display: "flex", alignItems: "baseline" }}>
                <span style={{ color: "#475569", fontSize: 9 }}>#{a.id}</span>
                <PendingAgeBadge createdAt={a.created_at} />
              </div>
            </div>

            <div style={{ fontSize: 10, color: "#475569", marginBottom: 8 }}>
              {a.mode} · {new Date(a.created_at).toLocaleString("ko-KR")}
            </div>

            <ReasonsLine reasons={a.reasons} />

            <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
              <Btn color="#22c55e" onClick={() => setDecisionTarget({ action: "approve", approval: a })} disabled={busy} small>
                ✓ 승인
              </Btn>
              <Btn color="#ef4444" onClick={() => setDecisionTarget({ action: "reject",  approval: a })} disabled={busy} small>
                ✗ 거부
              </Btn>
              <Btn color="#94a3b8" onClick={() => setDecisionTarget({ action: "cancel",  approval: a })} disabled={busy} small>
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

      {decisionTarget && (
        <ApprovalDecisionModal
          action={decisionTarget.action}
          approval={decisionTarget.approval}
          busy={busy}
          defaultDecidedBy={operatorName}
          onCancel={() => setDecisionTarget(null)}
          onConfirm={async (decision) => {
            await dispatchByAction[decisionTarget.action](decisionTarget.approval.id, decision);
            setDecisionTarget(null);
          }}
        />
      )}

      {bulkOpen && (
        <BulkCancelStaleModal
          approvals={staleApprovals}
          busy={busy}
          defaultDecidedBy={operatorName}
          onCancel={() => setBulkOpen(false)}
          onConfirm={async (decision) => {
            await cancelMany(staleApprovals.map((a) => a.id), decision);
            setBulkOpen(false);
          }}
        />
      )}
    </div>
  );
}
