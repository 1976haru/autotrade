/**
 * 체크리스트 #68: 통합 감사 이벤트 timeline 카드.
 *
 * /api/audit/events 응답을 read-only로 표시. 운영자가 신호 / 주문 / 승인 /
 * 거절 / AI 제안 / 리스크 차단 / 긴급정지 등을 한 timeline에서 추적.
 *
 * 절대 원칙:
 *   1. **삭제 버튼 0개** — append-only. UI는 archive만 노출.
 *   2. **수정 버튼 0개** — row 수정 경로 없음.
 *   3. archive는 *확인 모달 필수* (실수로 row가 사라지지 않게).
 *   4. broker / order / risk API 호출 0건 — 본 카드는 audit endpoint만 사용.
 *   5. Secret 노출 0건 — backend가 SecretLeakError로 INSERT 단계에서 차단.
 */

import { useCallback, useEffect, useState } from "react";
import { Btn, Card, SectionLabel } from "./index";
import { ErrorState, LoadingState } from "./primitives";
import { ChipFilterBar } from "./ChipFilterBar";
import { DecisionDialog } from "./DecisionDialog";
import { friendlyErrorMessage } from "../../utils/errorMessage";
import { backendApi } from "../../services/backend/client";


const SEVERITY_COLOR = {
  INFO:     "#7dd3fc",   // light blue
  WARN:     "#f59e0b",   // amber
  CRITICAL: "#ef4444",   // red
  SECURITY: "#a78bfa",   // purple
};

const SOURCE_COLOR = {
  AI:        "#a78bfa",
  STRATEGY:  "#67e8f9",
  MANUAL:    "#94a3b8",
  SYSTEM:    "#64748b",
  OPERATOR:  "#22c55e",
  SCHEDULER: "#f59e0b",
};


const SEVERITY_FILTERS = [
  { id: "all",      label: "전체",     color: "#7dd3fc" },
  { id: "INFO",     label: "INFO",     color: "#7dd3fc" },
  { id: "WARN",     label: "WARN",     color: "#f59e0b" },
  { id: "CRITICAL", label: "CRITICAL", color: "#ef4444" },
  { id: "SECURITY", label: "SECURITY", color: "#a78bfa" },
];

const SOURCE_FILTERS = [
  { id: "all",      label: "전체",     color: "#7dd3fc" },
  { id: "AI",       label: "AI",       color: "#a78bfa" },
  { id: "STRATEGY", label: "전략",     color: "#67e8f9" },
  { id: "OPERATOR", label: "운영자",   color: "#22c55e" },
  { id: "SYSTEM",   label: "시스템",   color: "#64748b" },
];


function _eventRowColors(ev) {
  return {
    severityColor: SEVERITY_COLOR[ev.severity] || "#64748b",
    sourceColor:   SOURCE_COLOR[ev.source] || "#64748b",
  };
}


function _EventRow({ ev, onArchive }) {
  const { severityColor, sourceColor } = _eventRowColors(ev);
  return (
    <div
      data-testid={`audit-event-row-${ev.id}`}
      data-event-type={ev.event_type}
      data-severity={ev.severity}
      data-source={ev.source}
      data-archived={ev.archived ? "true" : "false"}
      style={{
        padding: "8px 10px",
        borderLeft: `3px solid ${severityColor}`,
        background: ev.archived ? "#f1f5f9" : "#0c2035",
        borderRadius: 4,
        marginBottom: 6,
        opacity: ev.archived ? 0.7 : 1,
      }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "baseline", gap: 6, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", gap: 6, alignItems: "center",
                       flexWrap: "wrap" }}>
          <span data-testid={`audit-event-severity-${ev.id}`}
                style={{
                  fontSize: 9, fontWeight: 700,
                  padding: "1px 6px", borderRadius: 3,
                  color: severityColor,
                  background: `${severityColor}15`,
                  border: `1px solid ${severityColor}55`,
                }}>
            {ev.severity}
          </span>
          <span data-testid={`audit-event-source-${ev.id}`}
                style={{
                  fontSize: 9, fontWeight: 700,
                  padding: "1px 6px", borderRadius: 3,
                  color: sourceColor,
                  background: `${sourceColor}15`,
                  border: `1px solid ${sourceColor}55`,
                }}>
            {ev.source}
          </span>
          <span style={{
            fontSize: 10, color: "#cbd5e1", fontWeight: 700,
          }}>
            {ev.event_type}
          </span>
          {ev.symbol && (
            <span style={{ fontSize: 10, color: "#7dd3fc" }}>
              · {ev.symbol}
            </span>
          )}
          {ev.archived && (
            <span data-testid={`audit-event-archived-badge-${ev.id}`}
                  style={{
                    fontSize: 9, color: "#64748b",
                    padding: "1px 5px", borderRadius: 3,
                    border: "1px solid #cbd5e1", background: "#e2e8f0",
                  }}>
              archived
            </span>
          )}
        </div>
        <span style={{ fontSize: 9, color: "#94a3b8" }}>
          {new Date(ev.created_at).toLocaleString("ko-KR")}
        </span>
      </div>
      <div style={{ fontSize: 11, color: "#e2e8f0", marginTop: 4 }}>
        {ev.summary}
      </div>
      {ev.reason && (
        <div style={{ fontSize: 10, color: "#94a3b8", marginTop: 2 }}>
          사유: {ev.reason}
        </div>
      )}
      {ev.actor && (
        <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>
          by {ev.actor}
        </div>
      )}
      {!ev.archived && onArchive && (
        <div style={{ marginTop: 6, display: "flex",
                       justifyContent: "flex-end" }}>
          <Btn small color="#94a3b8" onClick={() => onArchive(ev)}>
            🗂 archive
          </Btn>
        </div>
      )}
    </div>
  );
}


function _ArchiveConfirmModal({ event, busy, onConfirm, onCancel,
                                 defaultDecidedBy = "" }) {
  return (
    <DecisionDialog
      title="audit_event archive 처리"
      ariaLabel="audit_event archive"
      accent="#94a3b8"
      cancelLabel="닫기"
      confirmLabel="🗂 archive"
      busy={busy}
      defaultDecidedBy={defaultDecidedBy}
      summary={
        <div
          data-testid="audit-event-archive-summary"
          style={{
            fontSize: 11, color: "#cbd5e1",
            padding: "8px 10px", marginBottom: 10,
            background: "#010a14",
            border: "1px solid #94a3b855",
            borderRadius: 4, lineHeight: 1.5,
          }}>
          <div style={{ fontWeight: 700, color: "#94a3b8", marginBottom: 4 }}>
            본 작업은 row를 *삭제하지 않습니다*. archived=True 표시만 합니다.
          </div>
          <ul style={{
            fontSize: 9, color: "#64748b", margin: "6px 0 0",
            paddingLeft: 16, lineHeight: 1.5,
          }}>
            <li>row는 영구 보존됩니다 (감사 추적 손실 없음).</li>
            <li>archived 행은 기본 목록에서 가려지며 ?include_archived=true 로 다시 볼 수 있습니다.</li>
            <li>archive는 멱등 — 같은 row에 다시 호출해도 첫 archive 정보는 덮어쓰지 않습니다.</li>
          </ul>
          {event && (
            <div style={{ marginTop: 6, fontSize: 10 }}>
              #{event.id} · <b>{event.event_type}</b> · {event.summary}
            </div>
          )}
        </div>
      }
      description="감사 추적을 위해 운영자명 / 사유를 남겨주세요."
      notePlaceholder="예: 노이즈 분리, 사고 분석 종료"
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}


export function AuditEventTimelineCard({
  testId = "audit-event-timeline-card",
  limit = 30,
  operatorName = "",
}) {
  const [events,    setEvents]    = useState(null);
  const [loading,   setLoading]   = useState(true);
  const [error,     setError]     = useState("");
  const [severityFilter, setSeverityFilter] = useState("all");
  const [sourceFilter,   setSourceFilter]   = useState("all");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState(null);
  const [archiveBusy, setArchiveBusy] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const params = {
        limit,
        includeArchived,
      };
      if (severityFilter !== "all") params.severity = severityFilter;
      if (sourceFilter   !== "all") params.source   = sourceFilter;
      const list = await backendApi.auditEventsList(params);
      setEvents(Array.isArray(list) ? list : []);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [limit, includeArchived, severityFilter, sourceFilter]);

  useEffect(() => {
    let cancelled = false;
    (async () => { if (!cancelled) await refresh(); })();
    return () => { cancelled = true; };
  }, [refresh]);

  const onArchiveConfirm = useCallback(async (decision) => {
    if (!archiveTarget) return { ok: false, message: "no target" };
    setArchiveBusy(true);
    try {
      await backendApi.auditEventArchive(archiveTarget.id, {
        archived_by: decision?.decided_by || null,
        note:        decision?.note || null,
      });
      await refresh();
      setArchiveTarget(null);
      return { ok: true };
    } catch (e) {
      return { ok: false, message: e.message || String(e) };
    } finally {
      setArchiveBusy(false);
    }
  }, [archiveTarget, refresh]);

  if (loading && events === null) {
    return (
      <Card>
        <LoadingState testId={`${testId}-loading`}
                       title="감사 이벤트 timeline 확인 중..." />
      </Card>
    );
  }

  if (error && events === null) {
    return (
      <Card>
        <ErrorState
          testId={`${testId}-error`}
          title="감사 이벤트 timeline 조회 실패"
          hint={friendlyErrorMessage(error) || "다시 시도해 주세요."}
          retryLabel="다시 시도"
          onRetry={refresh}
        />
      </Card>
    );
  }

  return (
    <Card>
      <div data-testid={testId}
           style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "baseline", gap: 6, flexWrap: "wrap",
        }}>
          <SectionLabel>📜 감사 이벤트 timeline</SectionLabel>
          <span data-testid={`${testId}-count`}
                style={{ fontSize: 10, color: "#94a3b8" }}>
            {Array.isArray(events) ? events.length : 0}건
          </span>
        </div>

        <div data-testid={`${testId}-policy-banner`}
             style={{
               fontSize: 10, color: "#0e7490",
               padding: "6px 10px",
               background: "#ecfeff",
               border: "1px solid #67e8f9",
               borderRadius: 4, lineHeight: 1.5,
             }}>
          🔒 감사 이벤트는 <b>append-only</b>입니다 — row 삭제 / 수정 UI는
          제공되지 않습니다. <b>archive</b>만 가능하며 row는 영구 보존됩니다.
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 9, color: "#64748b" }}>severity</div>
          <ChipFilterBar
            items={SEVERITY_FILTERS}
            active={severityFilter}
            onChange={setSeverityFilter}
            ariaLabel="severity 필터"
          />
          <div style={{ fontSize: 9, color: "#64748b" }}>source</div>
          <ChipFilterBar
            items={SOURCE_FILTERS}
            active={sourceFilter}
            onChange={setSourceFilter}
            ariaLabel="source 필터"
          />
          <label data-testid={`${testId}-include-archived-toggle`}
                 style={{
                   fontSize: 10, color: "#94a3b8",
                   display: "flex", alignItems: "center", gap: 6,
                   marginTop: 4,
                 }}>
            <input
              type="checkbox"
              checked={includeArchived}
              onChange={(e) => setIncludeArchived(e.target.checked)}
            />
            archived 포함
          </label>
        </div>

        {error && (
          <div data-testid={`${testId}-error-inline`}
               style={{
                 fontSize: 10, color: "#fbbf24",
                 padding: "4px 8px", background: "#7c2d1222",
                 border: "1px solid #f59e0b66", borderRadius: 3,
               }}>
            ⚠ {friendlyErrorMessage(error) || error}
          </div>
        )}

        {Array.isArray(events) && events.length === 0 ? (
          <div data-testid={`${testId}-empty`}
               style={{
                 fontSize: 11, color: "#94a3b8",
                 padding: 12, textAlign: "center",
                 background: "#0c2035",
                 border: "1px dashed #1e3a5c", borderRadius: 4,
               }}>
            표시할 감사 이벤트가 없습니다.
          </div>
        ) : (
          (events || []).map((ev) => (
            <_EventRow key={ev.id} ev={ev}
                       onArchive={(target) => setArchiveTarget(target)} />
          ))
        )}

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <Btn small color="#334155" onClick={refresh} disabled={loading}>
            ↻ 새로고침
          </Btn>
        </div>
      </div>

      {archiveTarget && (
        <_ArchiveConfirmModal
          event={archiveTarget}
          busy={archiveBusy}
          defaultDecidedBy={operatorName}
          onConfirm={onArchiveConfirm}
          onCancel={() => setArchiveTarget(null)}
        />
      )}
    </Card>
  );
}


// 테스트 / 외부 사용을 위한 export
export { _EventRow as EventRow };
export { SEVERITY_COLOR, SOURCE_COLOR };
export default AuditEventTimelineCard;
