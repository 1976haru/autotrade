/**
 * #PaperCandidateWire: Paper 후보 ↔ Auto Paper Loop 승인 UI.
 *
 * GET /api/auto-paper/candidates / POST .../approve-paper / .../reject /
 * GET /api/auto-paper/active-candidate 의 응답을 표시하고 운영자가 후보를
 * 승인 / 거절할 수 있는 카드.
 *
 * CLAUDE.md 절대 원칙 (테스트로 lock):
 *   1. 본 카드는 *advisory* — 승인된 후보도 자동 적용 안 됨.
 *   2. broker / route_order / OrderExecutor 호출 0건.
 *   3. "지금 매수" / "지금 매도" / "Place Order" / "실거래 시작" /
 *      "Live 활성화" / "ENABLE_LIVE_TRADING" 라벨 button 0개.
 *   4. "승인 후 Paper에서만 사용" 영구 배지.
 *   5. 후보 텍스트에 secret / API key / 계좌번호 노출 0건 — backend 가
 *      sanitize 후 응답.
 *
 * props.apiClient: backend client (테스트에서 mock 주입).
 *   - autoPaperCandidates() — GET /api/auto-paper/candidates
 *   - autoPaperApproveCandidate(id, body) — POST approve-paper
 *   - autoPaperRejectCandidate(id, body) — POST reject
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "../common";


const _STATUS_COLOR = {
  PENDING_APPROVAL: "#f59e0b",
  APPROVED:         "#22c55e",
  REJECTED:         "#94a3b8",
};


const _READINESS_LABEL = {
  NO_CANDIDATE:     "Paper 후보 없음",
  WAITING_APPROVAL: "승인 대기",
  CANDIDATE_READY:  "승인된 후보 있음",
};


/**
 * fix/ci-paper-candidate-and-lint: backend / mock 어디서 응답해도 후보 row 가
 * 정상 표시되도록 *robust* normalizer. 다음 모든 형태를 동일한 표준 모양으로
 * 정규화한다:
 *
 *   - candidates 배열 직접 반환 (`[c1, c2]`)
 *   - `{ candidates: [...] }`
 *   - `{ items: [...] }`
 *   - `{ entries: [...] }`
 *   - `{ data: { candidates: [...] } }`  (또는 data.items / data.entries)
 *   - `{ result: { candidates: [...] } }` (또는 result.items / result.entries)
 *   - 빈 배열 / null / undefined → empty state
 *
 * 표준 모양: `{ candidates: Array, readiness_state: string, total: number,
 * raw }`.
 *
 * readiness_state 가 응답에 없으면 *후보 내용으로부터 추론*:
 *   - candidates.length === 0                       → NO_CANDIDATE
 *   - 모든 후보가 APPROVED + 1개 이상                → CANDIDATE_READY
 *   - 그 외 (PENDING_APPROVAL 1개 이상)              → WAITING_APPROVAL
 */
export function normalizeCandidatesResponse(raw) {
  // null / undefined → 빈 상태.
  if (raw == null) {
    return {
      candidates: [], readiness_state: "NO_CANDIDATE", total: 0, raw,
    };
  }

  // 배열 직접 반환.
  let candidates = null;
  if (Array.isArray(raw)) {
    candidates = raw;
  } else if (typeof raw === "object") {
    // wrapper 단계별 탐색 — caller 가 어느 모양으로 보내든 대응.
    const top = raw;
    const data = top.data && typeof top.data === "object" ? top.data : null;
    const result = top.result && typeof top.result === "object" ? top.result : null;
    const sources = [top, data, result].filter(Boolean);
    for (const src of sources) {
      for (const key of ["candidates", "items", "entries"]) {
        if (Array.isArray(src[key])) {
          candidates = src[key];
          break;
        }
      }
      if (candidates) break;
    }
  }
  if (!Array.isArray(candidates)) {
    candidates = [];
  }
  // 무효 entry 제거 — candidate_id 가 없으면 row 가 만들어질 수 없으므로 skip.
  candidates = candidates.filter(
    (c) => c != null && typeof c === "object" && c.candidate_id,
  );

  // readiness_state — 응답에 있으면 그대로, 없으면 추론.
  let readiness =
    (raw && typeof raw === "object" && typeof raw.readiness_state === "string"
      ? raw.readiness_state
      : null);
  if (!readiness && raw && typeof raw === "object") {
    const wrappers = [raw.data, raw.result].filter(
      (x) => x && typeof x === "object",
    );
    for (const w of wrappers) {
      if (typeof w.readiness_state === "string") {
        readiness = w.readiness_state;
        break;
      }
    }
  }
  if (!readiness) {
    if (candidates.length === 0) {
      readiness = "NO_CANDIDATE";
    } else {
      const hasPending = candidates.some(
        (c) => c.status === "PENDING_APPROVAL",
      );
      const allApproved = candidates.length > 0 && candidates.every(
        (c) => c.status === "APPROVED",
      );
      readiness = hasPending
        ? "WAITING_APPROVAL"
        : allApproved
        ? "CANDIDATE_READY"
        : "WAITING_APPROVAL";
    }
  }

  return {
    candidates,
    readiness_state: readiness,
    total: candidates.length,
    raw,
  };
}


function _PaperOnlyBadge() {
  return (
    <span
      data-testid="candidate-approval-paper-only-badge"
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color: "#0f172a",
        background: "#bef264",
        border: "1px solid #65a30d",
        marginRight: 6,
      }}
    >
      승인 후 Paper에서만 사용
    </span>
  );
}


function _NoLiveBadge() {
  return (
    <span
      data-testid="candidate-approval-no-live-badge"
      style={{
        display: "inline-block",
        padding: "2px 6px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 500,
        color: "#64748b",
        background: "#f1f5f9",
        border: "1px solid #cbd5e1",
      }}
    >
      실거래 활성화 아님
    </span>
  );
}


function _StatusBadge({ status }) {
  const color = _STATUS_COLOR[status] || "#94a3b8";
  return (
    <span
      data-testid={`candidate-status-${status}`}
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 700,
        color: "#fff",
        background: color,
      }}
    >
      {status}
    </span>
  );
}


function _CandidateRow({ entry, onApprove, onReject, busy }) {
  const c = entry.candidate;
  const status = entry.status;
  const canApprove = status === "PENDING_APPROVAL";
  const canReject = status === "PENDING_APPROVAL";

  return (
    <div
      data-testid={`candidate-row-${entry.candidate_id}`}
      style={{
        padding: "10px 12px",
        borderBottom: "1px solid var(--c-border-faint)",
        fontSize: 12,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          flexWrap: "wrap",
          marginBottom: 4,
        }}
      >
        <_StatusBadge status={status} />
        <strong>{c.name}</strong>
        <span style={{ color: "#64748b" }}>· {c.symbol}</span>
        <span style={{ color: "#64748b", fontSize: 11 }}>
          regime={c.primary_regime}
        </span>
        <span style={{ color: "#1e3a8a", fontSize: 11 }}>
          score={Number(c.composite_score).toFixed(3)}
        </span>
      </div>
      <div
        data-testid={`candidate-tactics-${entry.candidate_id}`}
        style={{ color: "#475569", marginBottom: 2 }}
      >
        매매기법군: {(c.included_tactics || []).join(", ") || "—"}
      </div>
      <div style={{ color: "#475569", marginBottom: 2 }}>
        전략: {(c.included_strategies || []).join(", ") || "—"}
      </div>
      {(c.recommended_reasons || []).length > 0 ? (
        <ul
          data-testid={`candidate-reasons-${entry.candidate_id}`}
          style={{
            margin: "4px 0",
            paddingLeft: 18,
            color: "#475569",
            fontSize: 11,
          }}
        >
          {c.recommended_reasons.slice(0, 3).map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      ) : null}
      {(c.risk_flags || []).length > 0 ? (
        <div
          data-testid={`candidate-risk-flags-${entry.candidate_id}`}
          style={{ marginTop: 4, marginBottom: 4 }}
        >
          {c.risk_flags.map((f) => (
            <span
              key={f}
              style={{
                display: "inline-block",
                marginRight: 4,
                padding: "1px 6px",
                borderRadius: 3,
                fontSize: 10,
                color: "#92400e",
                background: "#fef3c7",
                border: "1px solid #fcd34d",
              }}
            >
              {f}
            </span>
          ))}
        </div>
      ) : null}
      {status === "APPROVED" ? (
        <div style={{ fontSize: 11, color: "#15803d", marginTop: 2 }}>
          승인자: <code>{entry.approved_by}</code> · {entry.approved_at}
        </div>
      ) : null}
      {status === "REJECTED" ? (
        <div style={{ fontSize: 11, color: "#94a3b8", marginTop: 2 }}>
          거절자: <code>{entry.rejected_by}</code> · {entry.rejected_at}
        </div>
      ) : null}
      {(canApprove || canReject) ? (
        <div style={{ marginTop: 6, display: "flex", gap: 6 }}>
          {canApprove ? (
            <button
              type="button"
              data-testid={`candidate-approve-btn-${entry.candidate_id}`}
              onClick={() => onApprove(entry)}
              disabled={busy}
              style={{
                padding: "4px 12px",
                borderRadius: 4,
                background: busy ? "#94a3b8" : "#22c55e",
                color: "#fff",
                border: "none",
                fontSize: 12,
                cursor: busy ? "not-allowed" : "pointer",
                fontWeight: 600,
              }}
            >
              Paper 승인
            </button>
          ) : null}
          {canReject ? (
            <button
              type="button"
              data-testid={`candidate-reject-btn-${entry.candidate_id}`}
              onClick={() => onReject(entry)}
              disabled={busy}
              style={{
                padding: "4px 12px",
                borderRadius: 4,
                background: busy ? "#94a3b8" : "#ef4444",
                color: "#fff",
                border: "none",
                fontSize: 12,
                cursor: busy ? "not-allowed" : "pointer",
              }}
            >
              거절
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}


export default function PaperCandidateApprovalCard({
  apiClient,
  pollIntervalMs = 10_000,
  defaultOperatorId = "operator",
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [activeId, setActiveId] = useState(null);

  const refresh = useCallback(async () => {
    if (apiClient == null) return;
    try {
      const [list, active] = await Promise.all([
        apiClient.autoPaperCandidates(),
        apiClient.autoPaperActiveCandidate(),
      ]);
      // fix/ci-paper-candidate-and-lint: 어떤 응답 모양이 와도 normalize.
      // backend / mock 응답이 array / {candidates} / {items} / {data.*} /
      // {result.*} / null 어디든 후보 row 가 정상 표시되도록 한다.
      setData(normalizeCandidatesResponse(list));
      setActiveId(active && active.has_active && active.active
        ? active.active.candidate_id
        : null);
      setError(null);
    } catch (e) {
      setError(e?.message || "load_failed");
    }
  }, [apiClient]);

  useEffect(() => {
    refresh();
    if (pollIntervalMs > 0) {
      const t = setInterval(refresh, pollIntervalMs);
      return () => clearInterval(t);
    }
    return undefined;
  }, [refresh, pollIntervalMs]);

  const onApprove = useCallback(async (entry) => {
    setBusy(true);
    setError(null);
    try {
      await apiClient.autoPaperApproveCandidate(
        entry.candidate_id, { approved_by: defaultOperatorId },
      );
      await refresh();
    } catch (e) {
      setError(e?.message || "approve_failed");
    } finally {
      setBusy(false);
    }
  }, [apiClient, defaultOperatorId, refresh]);

  const onReject = useCallback(async (entry) => {
    setBusy(true);
    setError(null);
    try {
      await apiClient.autoPaperRejectCandidate(
        entry.candidate_id, { rejected_by: defaultOperatorId },
      );
      await refresh();
    } catch (e) {
      setError(e?.message || "reject_failed");
    } finally {
      setBusy(false);
    }
  }, [apiClient, defaultOperatorId, refresh]);

  // normalizer 가 항상 candidates / readiness_state 를 보장.
  const candidates = data?.candidates || [];
  // 실제 후보가 있으면 empty state 가 동시에 떠서는 안 된다 — readiness
  // label 도 후보 유무와 일관되게 보정 (응답 라벨이 잘못 와도 UI 모순 차단).
  const rawReadiness = data?.readiness_state || "NO_CANDIDATE";
  const readiness = candidates.length === 0 && rawReadiness !== "NO_CANDIDATE"
    ? "NO_CANDIDATE"
    : (candidates.length > 0 && rawReadiness === "NO_CANDIDATE"
        ? "WAITING_APPROVAL"
        : rawReadiness);
  const isEmpty = candidates.length === 0;

  return (
    <div data-testid="paper-candidate-approval-card">
      <Card>
        <SectionLabel>Paper 후보 승인 (Operator)</SectionLabel>

        <div style={{ marginBottom: 8, display: "flex", gap: 4, flexWrap: "wrap" }}>
          <_PaperOnlyBadge />
          <_NoLiveBadge />
          <span
            data-testid="candidate-approval-readiness"
            style={{
              padding: "2px 8px",
              borderRadius: 4,
              background: readiness === "CANDIDATE_READY"
                ? "#dcfce7"
                : readiness === "WAITING_APPROVAL"
                ? "#fef3c7"
                : "#f1f5f9",
              color: "#0f172a",
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            {_READINESS_LABEL[readiness] || readiness}
          </span>
        </div>

        {error ? (
          <div
            data-testid="candidate-approval-error"
            style={{ color: "#991b1b", fontSize: 12, marginBottom: 8 }}
          >
            {error}
          </div>
        ) : null}

        {isEmpty ? (
          <div
            data-testid="candidate-approval-empty"
            style={{ color: "#64748b", fontSize: 12, padding: "8px 0" }}
          >
            현재 등록된 Paper 후보가 없습니다. 3-15 후보 선정 결과를 먼저
            로드하세요.
          </div>
        ) : null}

        {readiness === "WAITING_APPROVAL" ? (
          <div
            data-testid="candidate-approval-waiting-banner"
            style={{
              background: "#fffbeb",
              border: "1px solid #fde68a",
              borderRadius: 4,
              padding: 6,
              color: "#92400e",
              fontSize: 11,
              marginBottom: 6,
            }}
          >
            ⏳ 운영자 승인 대기 — Paper Auto Loop 는 active_candidate 가
            없으므로 BUY/SELL/EXIT 를 생성하지 않습니다.
          </div>
        ) : null}

        {readiness === "CANDIDATE_READY" && activeId ? (
          <div
            data-testid="candidate-approval-active-banner"
            style={{
              background: "#dcfce7",
              border: "1px solid #86efac",
              borderRadius: 4,
              padding: 6,
              color: "#14532d",
              fontSize: 11,
              marginBottom: 6,
            }}
          >
            ✓ active_candidate: <code>{activeId}</code>
          </div>
        ) : null}

        {candidates.length > 0 ? (
          <div data-testid="candidate-approval-list">
            {candidates.map((c) => (
              <_CandidateRow
                key={c.candidate_id}
                entry={c}
                onApprove={onApprove}
                onReject={onReject}
                busy={busy}
              />
            ))}
          </div>
        ) : null}

        <div
          data-testid="candidate-approval-footer-note"
          style={{
            marginTop: 12,
            fontSize: 11,
            color: "#64748b",
            borderTop: "1px solid var(--c-border-faint)",
            paddingTop: 8,
            lineHeight: 1.4,
          }}
        >
          본 카드는 Paper 후보의 *수동* 승인만 합니다. 승인된 후보는
          Paper Auto Loop 의 active_candidate 가 되지만 실거래는 어떤
          경로로도 진행되지 않습니다.
          is_order_signal=false / auto_apply_allowed=false /
          is_live_authorization=false.
        </div>
      </Card>
    </div>
  );
}
