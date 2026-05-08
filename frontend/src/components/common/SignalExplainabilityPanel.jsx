/**
 * 33: Signal Explainability Panel.
 *
 * `/api/signals/{audit_id}/explain` 응답을 PASS/WARN/FAIL/BLOCKED/INFO 그룹별
 * reason 카드로 표시한다. 운영자/감사가 "왜 이 신호가 승인/거절/대기됐는지"
 * 3초 안에 인지할 수 있도록 — JSON dump 대신 사람이 읽는 카드 리스트.
 *
 * 사용 패턴:
 *   <SignalExplainabilityPanel auditId={42} />
 *
 * 호출자가 auditId를 넘기면 컴포넌트가 fetch + 상태 관리 모두 처리. 별도
 * loading/error/empty 상태 모두 내부에서 처리. 모바일에서도 readable —
 * column flex layout으로 좁은 화면에서 카드가 자연스럽게 stack.
 *
 * 절대 원칙: 본 컴포넌트는 *주문을 만들지 않는다*. read-only audit 설명
 * 레이어 (#33 backend와 동일한 invariant).
 */

import { useEffect, useState } from "react";
import { Card } from "./index";
import { EmptyState, ErrorState, LoadingState, StatusBadge } from "./primitives";
import { backendApi } from "../../services/backend/client";


// 그룹별 표시 메타. backend의 ReasonStatus 5종(PASS/WARN/FAIL/BLOCKED/INFO)과
// 1:1 매핑 — 색은 primitives.StatusBadge의 status prop으로 위임.
const STATUS_META = {
  PASS:    { label: "통과 조건",    status: "success", icon: "✓" },
  WARN:    { label: "주의 조건",    status: "warning", icon: "!" },
  FAIL:    { label: "실패 조건",    status: "danger",  icon: "✕" },
  BLOCKED: { label: "차단 조건",    status: "danger",  icon: "⛔" },
  INFO:    { label: "참고 정보",    status: "info",    icon: "ℹ" },
};

const STATUS_ORDER = ["BLOCKED", "FAIL", "WARN", "PASS", "INFO"];


// 최종 상태 → 헤더 배지 색.
function finalStatusToBadgeStatus(finalStatus) {
  switch (finalStatus) {
    case "APPROVED": return "success";
    case "PENDING":  return "warning";
    case "WATCH":    return "warning";
    case "REJECTED": return "danger";
    case "UNKNOWN":  return "neutral";
    default:         return "neutral";
  }
}

// 운영자가 raw error를 보지 않도록 친근 문구로 바꾼다.
function friendlyErrorMessage(err) {
  if (!err) return "알 수 없는 오류가 발생했습니다.";
  const msg = String(err.message || "").toLowerCase();
  if (msg.includes("failed to fetch") || msg.includes("networkerror")) {
    return "백엔드 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.";
  }
  if (err.status === 404) {
    return "해당 신호의 판정 기록을 찾을 수 없습니다.";
  }
  if (err.status >= 500) {
    return "서버에서 판정 근거를 불러오는 중 오류가 발생했습니다.";
  }
  // 기타 — 사용자 메시지를 그대로 노출하지 않음. 일반화된 안내로 대체.
  return "판정 근거를 불러오는 중 오류가 발생했습니다.";
}


function ReasonCard({ reason }) {
  return (
    <li
      data-testid="signal-explain-reason"
      data-category={reason.category}
      data-status={reason.status}
      style={{
        padding: "8px 10px",
        borderLeft: `3px solid ${
          reason.status === "PASS"    ? "var(--c-success)" :
          reason.status === "WARN"    ? "var(--c-warning)" :
          reason.status === "FAIL"    ? "var(--c-danger)"  :
          reason.status === "BLOCKED" ? "var(--c-danger)"  :
                                          "var(--c-info)"
        }`,
        background: "var(--c-surface-2, #f8fafc)",
        borderRadius: 4,
        fontSize: "var(--fs-sm)",
        lineHeight: 1.5,
        color: "var(--c-text-1)",
        wordBreak: "break-word",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <span style={{
          fontSize: 9, letterSpacing: "0.05em", fontWeight: 700,
          color: "var(--c-text-3)",
        }}>
          {reason.category}
        </span>
        {reason.code && (
          <span style={{
            fontSize: 9, fontFamily: "monospace",
            color: "var(--c-text-3)",
          }}>
            {reason.code}
          </span>
        )}
      </div>
      <div style={{ marginTop: 2 }}>{reason.message || "(설명 없음)"}</div>
    </li>
  );
}


function ReasonGroup({ statusKey, reasons }) {
  if (!reasons || reasons.length === 0) return null;
  const meta = STATUS_META[statusKey];
  if (!meta) return null;
  return (
    <section
      data-testid={`signal-explain-group-${statusKey}`}
      style={{ display: "flex", flexDirection: "column", gap: 6 }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <StatusBadge status={meta.status} testId={`signal-explain-badge-${statusKey}`}>
          {meta.icon} {meta.label} ({reasons.length})
        </StatusBadge>
      </div>
      <ul style={{
        listStyle: "none", padding: 0, margin: 0,
        display: "flex", flexDirection: "column", gap: 4,
      }}>
        {reasons.map((r, i) => (
          <ReasonCard key={`${statusKey}-${i}`} reason={r} />
        ))}
      </ul>
    </section>
  );
}


function SignalSummary({ payload }) {
  const final = payload.final_status || "UNKNOWN";
  return (
    <div
      data-testid="signal-explain-summary"
      style={{
        display: "flex", flexDirection: "column", gap: 4,
        paddingBottom: 8, borderBottom: "1px solid var(--c-border)",
      }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        {payload.symbol && (
          <span style={{ fontSize: "var(--fs-md)", fontWeight: 700, fontFamily: "monospace" }}>
            {payload.symbol}
          </span>
        )}
        {payload.action && (
          <span style={{
            fontSize: "var(--fs-sm)", padding: "1px 6px",
            background: "var(--c-surface-2, #f1f5f9)", borderRadius: 4,
            color: "var(--c-text-2)",
          }}>
            {payload.action}
          </span>
        )}
        {payload.strategy && (
          <span style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            {payload.strategy}
          </span>
        )}
        <StatusBadge status={finalStatusToBadgeStatus(final)} testId="signal-explain-final">
          {final}
        </StatusBadge>
      </div>
      {payload.summary && (
        <div style={{ fontSize: "var(--fs-sm)", color: "var(--c-text-2)", lineHeight: 1.5 }}>
          {payload.summary}
        </div>
      )}
      {payload.audit_trace_id != null && (
        <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
          audit_id #{payload.audit_trace_id}
        </div>
      )}
    </div>
  );
}


export function SignalExplainabilityPanel({ auditId, autoLoad = true, testId = "signal-explainability-panel" }) {
  // requestKey = the auditId that the current payload/error belongs to. If it
  // doesn't match the prop auditId, we're either loading or pre-fetch — either
  // way we render the loading state. This pattern avoids the cascading-render
  // lint warning that synchronous setState inside useEffect would trigger.
  const [state, setState] = useState({ requestKey: null, error: null, payload: null });

  useEffect(() => {
    if (!autoLoad || auditId == null) return;
    let cancelled = false;
    backendApi.explainSignal(auditId)
      .then((p) => {
        if (!cancelled) setState({ requestKey: auditId, error: null, payload: p });
      })
      .catch((err) => {
        if (!cancelled) setState({ requestKey: auditId, error: err, payload: null });
      });
    return () => { cancelled = true; };
  }, [auditId, autoLoad]);

  if (auditId == null) {
    return (
      <Card>
        <EmptyState
          testId={`${testId}-no-audit`}
          icon="ℹ"
          title="판정 근거를 불러오려면 신호를 선택하세요"
          hint="감사 로그 행에서 [판정 근거 보기]를 누르면 표시됩니다."
        />
      </Card>
    );
  }

  // requestKey가 현재 prop auditId와 다르면 아직 fetch가 끝나지 않은 상태.
  // (or autoLoad=false인데 호출자가 직접 payload를 안 채운 경우 — UI는 loading.)
  const isReady = state.requestKey === auditId;

  if (!isReady) {
    return (
      <Card>
        <LoadingState testId={`${testId}-loading`} title="판정 근거 불러오는 중..." />
      </Card>
    );
  }

  if (state.error) {
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="판정 근거 조회 실패"
          hint={friendlyErrorMessage(state.error)}
        />
      </Card>
    );
  }

  const payload = state.payload;
  if (!payload) return null;

  const grouped = payload.grouped || {};
  const totalReasons = (payload.reasons || []).length;

  return (
    <Card>
      <div data-testid={testId} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <SignalSummary payload={payload} />

        {totalReasons === 0 ? (
          <EmptyState
            testId={`${testId}-empty-reasons`}
            icon="ℹ"
            title="판정 근거가 아직 없습니다"
            hint="이 신호는 설명이 기록되지 않은 채 처리됐습니다. 운영자가 사후에 사유를 보강해야 합니다."
          />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {STATUS_ORDER.map((statusKey) => (
              <ReasonGroup
                key={statusKey}
                statusKey={statusKey}
                reasons={grouped[statusKey] || []}
              />
            ))}
          </div>
        )}

        {payload.risk_notes && payload.risk_notes.length > 0 && (
          <section
            data-testid="signal-explain-risk-notes"
            style={{
              padding: 8, borderRadius: 4,
              background: "rgba(245, 158, 11, 0.08)",
              border: "1px solid rgba(245, 158, 11, 0.25)",
              display: "flex", flexDirection: "column", gap: 4,
            }}
          >
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: 700, color: "var(--c-warning)" }}>
              ⚠ 위험 요약
            </div>
            <ul style={{ listStyle: "disc", paddingLeft: 16, margin: 0 }}>
              {payload.risk_notes.map((n, i) => (
                <li key={i} style={{ fontSize: "var(--fs-sm)", lineHeight: 1.5 }}>{n}</li>
              ))}
            </ul>
          </section>
        )}

        {payload.operator_note && (
          <section
            data-testid="signal-explain-operator-note"
            style={{
              padding: 8, borderRadius: 4,
              background: "var(--c-surface-2, #f8fafc)",
              border: "1px solid var(--c-border)",
            }}
          >
            <div style={{ fontSize: "var(--fs-xs)", fontWeight: 700, color: "var(--c-text-3)" }}>
              운영자 메모
            </div>
            <div style={{ fontSize: "var(--fs-sm)", marginTop: 2 }}>
              {payload.operator_note}
            </div>
          </section>
        )}
      </div>
    </Card>
  );
}


export default SignalExplainabilityPanel;
