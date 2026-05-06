import { useEffect, useState } from "react";

import { Btn, Card, Inp, SectionLabel } from "../common";
import { ChipFilterBar } from "../common/ChipFilterBar";
import { DecisionDialog } from "../common/DecisionDialog";
import { usePersistedState } from "../../store/usePersistedState";
import {
  PENDING_STALE_THRESHOLD_MS,
  fmtKRW, formatPendingAge, isPendingStale,
} from "../../utils/format";


// 103: 키보드 nav가 hotkey를 받을지 여부. 사용자가 텍스트 입력 중이거나
// 한국어 IME 조합 중이면 a/r/c/방향키가 의도치 않게 fire되는 사고를 차단.
// 095/096이 DecisionDialog Enter에 적용한 IME 가드의 키 경로가 더 넓어진 형태.
export function shouldHandleApprovalsHotkey(event) {
  if (event.isComposing || event.keyCode === 229) return false;
  const t = event.target;
  if (!t) return true;
  const tag = t.tagName ? t.tagName.toLowerCase() : "";
  if (tag === "input" || tag === "textarea" || tag === "select") return false;
  if (t.isContentEditable) return false;
  return true;
}

// 087: formatPendingAge / isPendingStale moved to utils/format.js once
// Dashboard + App started importing them across tabs. Re-export from this
// module so 058~077 callers (and tests asserting the export shape) keep
// working without churn.
export { formatPendingAge, isPendingStale };


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


export function HistoryRow({ a, focused = false, onClick }) {
  const color = STATUS_COLOR[a.status] || "#475569";
  return (
    <div
      data-testid={`approval-history-row-${a.id}`}
      data-focused={focused ? "true" : "false"}
      onClick={onClick}
      style={{
        padding: "8px 0 8px 8px", borderBottom: "1px solid #05121f",
        borderLeft: focused ? "3px solid #7dd3fc" : "3px solid transparent",
        background: focused ? "#7dd3fc0a" : "transparent",
        cursor: onClick ? "pointer" : "default",
      }}>
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
        {a.attempts && a.attempts.length > 0 && (
          <>
            {" · "}
            <span data-testid="history-attempts-summary"
                  style={{ color: "#fbbf24" }}>
              ⚠ {a.attempts.length}회 시도
            </span>
          </>
        )}
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


// 083: status filter chips on the 처리 내역 list, mirroring 052's
// KindFilterBar pattern. Persisted across sessions because investigation
// sessions tend to focus on one outcome type ("이번 주 거부 사례 보기").
const HISTORY_STATUS_FILTERS = [
  { id: "all",       label: "전체", color: "#7dd3fc" },
  { id: "APPROVED",  label: "승인", color: "#22c55e" },
  { id: "REJECTED",  label: "거부", color: "#ef4444" },
  { id: "CANCELLED", label: "취소", color: "#94a3b8" },
];

export const HISTORY_STATUS_STORAGE_KEY = "autotrade.approvalsHistoryStatusFilter";
const _VALID_HISTORY_STATUSES = new Set(HISTORY_STATUS_FILTERS.map((f) => f.id));
export const isValidHistoryStatus = (v) => _VALID_HISTORY_STATUSES.has(v);


export function HistoryStatusFilterBar({ active, onChange }) {
  return (
    <ChipFilterBar items={HISTORY_STATUS_FILTERS} active={active}
      onChange={onChange} ariaLabel="처리 내역 상태 필터" />
  );
}


// 086: time-bucket chips on 처리 내역, mirroring 073 audit timeline. Filters
// on decided_at (when the action settled the row) — that's what operators
// reason about when they say "결정된 항목 중 최근 1시간". Persisted like 083
// status filter for the same reason: investigation sessions stick to a
// window for a stretch.
const HISTORY_TIME_BUCKETS = [
  { id: "all", label: "전 기간", color: "#7dd3fc" },
  { id: "1h",  label: "1시간",   color: "#7dd3fc" },
  { id: "24h", label: "24시간",  color: "#7dd3fc" },
  { id: "7d",  label: "7일",     color: "#7dd3fc" },
];

const HISTORY_TIME_BUCKET_MS = {
  "1h":  60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d":  7 * 24 * 60 * 60 * 1000,
};

export const HISTORY_TIME_BUCKET_STORAGE_KEY = "autotrade.approvalsHistoryTimeBucket";
const _VALID_HISTORY_BUCKETS = new Set(HISTORY_TIME_BUCKETS.map((b) => b.id));
export const isValidHistoryTimeBucket = (v) => _VALID_HISTORY_BUCKETS.has(v);


export function HistoryTimeBucketBar({ active, onChange }) {
  return (
    <ChipFilterBar items={HISTORY_TIME_BUCKETS} active={active}
      onChange={onChange} ariaLabel="처리 내역 시간 범위 필터" />
  );
}


// 106/110: 처리 내역 footer — 098/101 패턴 재사용. 평균만으로는 stale 한 건이
// 흐름을 가려 — 110이 max/min을 추가해 outlier(예: stale 채로 cancel된 1일짜리)
// 가 한눈에. 평균이 짧아도 max가 크면 적체 의심을 명시적으로 surface.
export function summarizeHistoryDecisionTime(items) {
  let count = 0;
  let sumMs = 0;
  let maxMs = 0;
  let minMs = Number.POSITIVE_INFINITY;
  for (const a of items || []) {
    if (!a || !a.created_at || !a.decided_at) continue;
    const ms = new Date(a.decided_at).getTime() - new Date(a.created_at).getTime();
    // 음수는 시계 어긋남이나 fixture 오류 — defensive하게 제외해 평균 왜곡 방지.
    if (!Number.isFinite(ms) || ms < 0) continue;
    sumMs += ms;
    if (ms > maxMs) maxMs = ms;
    if (ms < minMs) minMs = ms;
    count += 1;
  }
  return {
    count,
    avgMs: count > 0 ? Math.round(sumMs / count) : 0,
    maxMs: count > 0 ? maxMs : 0,
    minMs: count > 0 ? minMs : 0,
  };
}


// "1일 3시간" / "12분 5초" / "30초" — 한국어 단위로 가장 큰 두 단위까지만.
// 결정 시간은 보통 초~분 단위지만 stale 채로 cancel된 경우 시간/일 단위까지
// 갈 수 있다. 0 또는 음수는 "0초"로 통일.
export function formatDecisionDuration(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "0초";
  const totalSec = Math.floor(ms / 1000);
  if (totalSec < 60)         return `${totalSec}초`;
  const totalMin = Math.floor(totalSec / 60);
  if (totalMin < 60)         return `${totalMin}분 ${totalSec % 60}초`;
  const totalHr  = Math.floor(totalMin / 60);
  if (totalHr  < 24)         return `${totalHr}시간 ${totalMin % 60}분`;
  const totalDay = Math.floor(totalHr / 24);
  return `${totalDay}일 ${totalHr % 24}시간`;
}


// 111: 평균/max/min 옆에 "결정에 임계 시간(기본 10분, PENDING stale 임계와 동일)
// 이상 걸린 비율"을 추가. 110의 max는 worst-case 한 건만 보여주고 중간 영역의
// 적체 분포를 못 잡는다 — 비율은 "절반이 10분 넘었다 vs 한 건만 그랬다"를
// 즉시 구분.
export function summarizeHistoryStaleRatio(items, thresholdMs = PENDING_STALE_THRESHOLD_MS) {
  let count = 0;
  let staleCount = 0;
  for (const a of items || []) {
    if (!a || !a.created_at || !a.decided_at) continue;
    const ms = new Date(a.decided_at).getTime() - new Date(a.created_at).getTime();
    if (!Number.isFinite(ms) || ms < 0) continue;
    count += 1;
    if (ms >= thresholdMs) staleCount += 1;
  }
  return { count, staleCount, ratio: count > 0 ? staleCount / count : 0 };
}


export function HistoryStaleRatio({ items, thresholdMs = PENDING_STALE_THRESHOLD_MS }) {
  const s = summarizeHistoryStaleRatio(items, thresholdMs);
  // 적체가 없으면 아무것도 안 보여줌 — 운영자가 "건강하다"는 신호로 간주.
  if (s.staleCount === 0) return null;
  // 50%+이면 빨강(주의), 그 외 amber.
  const color = s.ratio >= 0.5 ? "#ef4444" : "#fbbf24";
  const pct = Math.round(s.ratio * 100);
  const thresholdMin = Math.round(thresholdMs / 60_000);
  return (
    <div data-testid="history-stale-ratio"
         data-ratio={pct}
         style={{ fontSize: 10, color, marginBottom: 8,
                  display: "flex", gap: 6, flexWrap: "wrap",
                  padding: "4px 0", borderBottom: "1px dashed #0c2035" }}>
      <span style={{ fontWeight: 700 }}>
        ⚠ stale ({thresholdMin}분+) {s.staleCount}/{s.count}건 ({pct}%)
      </span>
    </div>
  );
}


export function HistoryDecisionTimeSummary({ items }) {
  const s = summarizeHistoryDecisionTime(items);
  if (s.count === 0) return null;
  // 110: 평균은 강조, max/min은 보조. count=1이면 max=avg=min이라 한 값만
  // 보여 노이즈 줄임. count>=2일 때만 max/min 추가.
  const showSpread = s.count >= 2;
  return (
    <div data-testid="history-decision-time-summary"
         style={{ fontSize: 10, color: "#64748b", marginBottom: 8,
                  display: "flex", gap: 8, flexWrap: "wrap",
                  padding: "4px 0", borderBottom: "1px dashed #0c2035" }}>
      <span>처리 {s.count}건</span>
      <span>·</span>
      <span style={{ color: "#a78bfa", fontWeight: 700 }}>
        평균 결정 {formatDecisionDuration(s.avgMs)}
      </span>
      {showSpread && (
        <>
          <span>·</span>
          <span data-testid="history-decision-time-max"
                style={{ color: "#fbbf24" }}>
            최대 {formatDecisionDuration(s.maxMs)}
          </span>
          <span>·</span>
          <span data-testid="history-decision-time-min"
                style={{ color: "#475569" }}>
            최소 {formatDecisionDuration(s.minMs)}
          </span>
        </>
      )}
    </div>
  );
}


// 092: mode filter on the 처리 내역 list. RiskManager only emits NEEDS_APPROVAL
// for LIVE_MANUAL_APPROVAL + LIVE_AI_ASSIST (risk_manager.py:118), so those
// are the two real-world modes that produce queue rows. The 3rd chip lets
// operators compare manual-vs-AI approval volumes — the original motivation
// for 092. Persisted alongside 083 status / 086 time since investigation
// sessions tend to lock onto one stream ("이번 주 AI 흐름만").
// "전체"는 083 status 칩이 이미 쓰는 라벨이라 같은 카드 안에서 getByRole
// 충돌이 생긴다 — "모든 모드"로 구분.
const HISTORY_MODE_FILTERS = [
  { id: "all",                   label: "모든 모드", color: "#7dd3fc" },
  { id: "LIVE_MANUAL_APPROVAL",  label: "수동",      color: "#22c55e" },
  { id: "LIVE_AI_ASSIST",        label: "AI 보조",   color: "#a78bfa" },
];

export const HISTORY_MODE_STORAGE_KEY = "autotrade.approvalsHistoryModeFilter";
const _VALID_HISTORY_MODES = new Set(HISTORY_MODE_FILTERS.map((f) => f.id));
export const isValidHistoryMode = (v) => _VALID_HISTORY_MODES.has(v);


export function HistoryModeFilterBar({ active, onChange }) {
  return (
    <ChipFilterBar items={HISTORY_MODE_FILTERS} active={active}
      onChange={onChange} ariaLabel="처리 내역 모드 필터" />
  );
}


// 082+083+086+092: 처리 내역에서 네 축(종목/상태/시간/모드) 필터를 조합.
// 081 audit empty-state 패턴과 같은 구조 — 칩/입력이 활성 필터의 진실 소스이고
// 메시지는 단순히 "필터 때문임"만 신호한다. timeBucket/modeFilter가 undefined면
// 정적 default ("all")로 취급해 기존 호출자가 추가 인자 없이 작동.
export function historyEmptyMessage(history, symbolNeedle, statusFilter, timeBucket, modeFilter) {
  if (!history || history.length === 0) return "결정된 항목이 없습니다";
  const hasFilter =
    (symbolNeedle && symbolNeedle.length > 0)
    || (statusFilter && statusFilter !== "all")
    || (timeBucket && timeBucket !== "all")
    || (modeFilter && modeFilter !== "all");
  return hasFilter ? "해당 조건의 항목이 없습니다" : "결정된 항목이 없습니다";
}


// 076: per-row failure hint sourced from PendingApproval.attempts.
// Each entry is {at: ISO, decided_by, reasons}. Survives session/operator
// changes. The badge shows the count + the most recent timestamp + reasons,
// since accumulating "이 결재는 5번 막혔다" is itself a useful signal.
export function ApproveAttemptFailureBadge({ attempts, now }) {
  if (!attempts || attempts.length === 0) return null;
  const last = attempts[attempts.length - 1];
  const reasons = Array.isArray(last.reasons) ? last.reasons.join(" / ") : "";
  return (
    <div
      data-testid="approve-attempt-failure-badge"
      style={{
        marginTop: 6,
        padding: "4px 8px",
        background: "#7f1d1d22",
        border: "1px solid #ef444466",
        borderRadius: 4,
        fontSize: 9,
        color: "#fca5a5",
        lineHeight: 1.4,
      }}
    >
      <span style={{ fontWeight: 700, marginRight: 4 }}>
        ⚠ {attempts.length}번째 시도, {formatPendingAge(last.at, now)} 거부:
      </span>
      {reasons}
    </div>
  );
}


export function Approvals({ approvals, operatorName = "" }) {
  // useApprovals는 App에서 lift되어 prop으로 전달된다 — BottomNav 배지가 같은
  // 폴링 결과를 공유하기 위해서. 테스트는 모킹 없이 prop만 직접 주입.
  const { pending, history, loading, error, busy,
          historyHasMore, historyLoadingMore, loadMoreHistory,
          approve, reject, cancel, cancelMany } = approvals;
  // 결재 모달 대상: { action, approval } | null. 같은 모달 컴포넌트를 세 액션에서
  // 공유하고, 액션은 ACTION_META에서 분기한다.
  const [decisionTarget, setDecisionTarget] = useState(null);
  const [bulkOpen, setBulkOpen] = useState(false);
  // 082: transient symbol filter for the 처리 내역 list. Not persisted —
  // each investigation focuses on a different ticker, like the 067 audit
  // timeline filter.
  const [historySymbolFilter, setHistorySymbolFilter] = useState("");
  const _historyNeedle = historySymbolFilter.trim().toLowerCase();
  // 083: persisted status filter — investigation sessions tend to lock onto
  // one outcome type for a while ("거부 사례만"), so survives reload like
  // 054 audit kind filter.
  const [historyStatusFilter, setHistoryStatusFilter] = usePersistedState(
    HISTORY_STATUS_STORAGE_KEY, "all", isValidHistoryStatus,
  );
  // 086: persisted time-bucket — same reasoning as status. Filters on
  // decided_at (the moment the row settled) since that's how operators
  // reason about "이번 주 결정된 것".
  const [historyTimeBucket, setHistoryTimeBucket] = usePersistedState(
    HISTORY_TIME_BUCKET_STORAGE_KEY, "all", isValidHistoryTimeBucket,
  );
  const _historyBucketWindowMs = HISTORY_TIME_BUCKET_MS[historyTimeBucket];
  const _now = Date.now();
  const _withinHistoryBucket = (a) => {
    if (_historyBucketWindowMs === undefined) return true;
    if (!a.decided_at) return false;  // defensive — decided rows always have it
    return _now - new Date(a.decided_at).getTime() < _historyBucketWindowMs;
  };
  // 092: persisted mode filter — separates manual vs AI approval streams.
  const [historyModeFilter, setHistoryModeFilter] = usePersistedState(
    HISTORY_MODE_STORAGE_KEY, "all", isValidHistoryMode,
  );

  const filteredHistory = history
    .filter((a) => historyStatusFilter === "all" || a.status === historyStatusFilter)
    .filter((a) => !_historyNeedle || a.symbol.toLowerCase().includes(_historyNeedle))
    .filter(_withinHistoryBucket)
    .filter((a) => historyModeFilter === "all" || a.mode === historyModeFilter);

  const dispatchByAction = { approve, reject, cancel };

  // 058 isPendingStale 기준(10분+) — 062 status pin escalation과 같은 임계값.
  // 일괄 취소 버튼은 여기 매칭되는 항목이 있을 때만 노출.
  const staleApprovals = pending.filter((a) => isPendingStale(a.created_at));

  // 103: 키보드 nav. ↑↓로 PENDING 행을 이동, a/r/c로 현재 focus된 행에
  // approve/reject/cancel 모달을 연다. mass 결재 흐름에서 마우스 사이클을
  // 줄이는 게 목표. -1은 "선택 없음" — 페이지 마운트 직후나 모든 PENDING이
  // 해소된 직후의 자연스러운 상태.
  const [focusedIndex, setFocusedIndex] = useState(-1);
  // 107: 처리 내역에도 별도 focus index. j/k vi-style — ↑↓는 PENDING 큐 nav를
  // 위해 예약돼 있고, history는 read-only 검토 흐름이라 같은 hotkey가 아니어도
  // 충분. j/k는 한국어 자판에선 ㅓ/ㅏ에 매핑되지만 shouldHandleApprovalsHotkey가
  // input/IME에서 skip하니 안전.
  const [historyFocusedIndex, setHistoryFocusedIndex] = useState(-1);

  // pending 폴링이 행을 추가/제거하면 인덱스가 invalid 될 수 있다 — clamp.
  useEffect(() => {
    if (pending.length === 0) {
      if (focusedIndex !== -1) setFocusedIndex(-1);
      return;
    }
    if (focusedIndex >= pending.length) {
      setFocusedIndex(pending.length - 1);
    }
  }, [pending.length, focusedIndex]);

  // 107: filteredHistory가 줄어들면 historyFocusedIndex clamp.
  useEffect(() => {
    if (filteredHistory.length === 0) {
      if (historyFocusedIndex !== -1) setHistoryFocusedIndex(-1);
      return;
    }
    if (historyFocusedIndex >= filteredHistory.length) {
      setHistoryFocusedIndex(filteredHistory.length - 1);
    }
  }, [filteredHistory.length, historyFocusedIndex]);

  useEffect(() => {
    const handler = (event) => {
      // 텍스트 입력 또는 IME 조합 중이면 모든 hotkey 무시.
      if (!shouldHandleApprovalsHotkey(event)) return;
      // 모달이 열려 있으면 그 모달의 자체 listener(095 IME-aware Enter/Esc)
      // 가 처리하도록 양보한다 — a/r/c도 모달 안에선 텍스트로 들어갈 수 있다.
      if (decisionTarget || bulkOpen) return;
      // 결재 액션 진행 중에는 hotkey도 disabled — DecisionDialog의 busy
      // 가드와 같은 규칙.
      if (busy) return;

      const key = event.key.toLowerCase();
      // PENDING 큐 nav (103) — ↑↓ + a/r/c.
      if (event.key === "ArrowDown") {
        if (pending.length === 0) return;
        event.preventDefault();
        setFocusedIndex((i) => {
          if (i < 0) return 0;
          return Math.min(pending.length - 1, i + 1);
        });
      } else if (event.key === "ArrowUp") {
        if (pending.length === 0) return;
        event.preventDefault();
        setFocusedIndex((i) => Math.max(0, i - 1));
      } else if (key === "a" || key === "r" || key === "c") {
        if (pending.length === 0) return;
        // focus가 없으면 무시 — 잘못된 행에 액션 fire되는 사고를 막는다.
        if (focusedIndex < 0 || focusedIndex >= pending.length) return;
        event.preventDefault();
        const action = key === "a" ? "approve" : key === "r" ? "reject" : "cancel";
        setDecisionTarget({ action, approval: pending[focusedIndex] });
      } else if (key === "j") {
        // 107: 처리 내역 다음 행. PENDING과 별도 focus.
        if (filteredHistory.length === 0) return;
        event.preventDefault();
        setHistoryFocusedIndex((i) => {
          if (i < 0) return 0;
          return Math.min(filteredHistory.length - 1, i + 1);
        });
      } else if (key === "k") {
        // 107: 처리 내역 이전 행.
        if (filteredHistory.length === 0) return;
        event.preventDefault();
        setHistoryFocusedIndex((i) => Math.max(0, i - 1));
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [pending, focusedIndex, filteredHistory, historyFocusedIndex,
       decisionTarget, bulkOpen, busy]);

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

        {pending.length > 0 && (
          <div data-testid="approvals-keyboard-hint"
               style={{ fontSize: 9, color: "#475569", marginBottom: 6, padding: "0 2px" }}>
            ↑↓ 행 이동 · <span style={{ color: "#22c55e" }}>a</span> 승인 ·{" "}
            <span style={{ color: "#ef4444" }}>r</span> 거부 ·{" "}
            <span style={{ color: "#94a3b8" }}>c</span> 취소
          </div>
        )}

        {loading ? (
          <div style={{ color: "#475569", fontSize: 12, textAlign: "center", padding: 16 }}>
            로딩 중…
          </div>
        ) : pending.length === 0 ? (
          <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
            승인 대기 중인 주문 없음
          </div>
        ) : pending.map((a, idx) => (
          <div
            key={a.id}
            data-testid={`approval-pending-row-${a.id}`}
            data-focused={idx === focusedIndex ? "true" : "false"}
            onClick={() => setFocusedIndex(idx)}
            style={{
              padding: "10px 0 10px 8px", borderBottom: "1px solid #05121f",
              borderLeft: idx === focusedIndex
                ? "3px solid #7dd3fc"
                : "3px solid transparent",
              background: idx === focusedIndex ? "#7dd3fc0a" : "transparent",
              cursor: "pointer",
            }}
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
            <ApproveAttemptFailureBadge attempts={a.attempts} />

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
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
          <HistoryStatusFilterBar
            active={historyStatusFilter}
            onChange={setHistoryStatusFilter}
          />
          <div style={{ flex: 1, minWidth: 100 }}>
            <Inp
              value={historySymbolFilter}
              onChange={setHistorySymbolFilter}
              placeholder="🔍 종목 (예: 005930)"
            />
          </div>
        </div>
        <div style={{ marginBottom: 8 }}>
          <HistoryTimeBucketBar
            active={historyTimeBucket}
            onChange={setHistoryTimeBucket}
          />
        </div>
        <div style={{ marginBottom: 8 }}>
          <HistoryModeFilterBar
            active={historyModeFilter}
            onChange={setHistoryModeFilter}
          />
        </div>
        <HistoryDecisionTimeSummary items={filteredHistory} />
        <HistoryStaleRatio items={filteredHistory} />
        {filteredHistory.length > 0 && (
          <div data-testid="approvals-history-keyboard-hint"
               style={{ fontSize: 9, color: "#475569", marginBottom: 6, padding: "0 2px" }}>
            <span style={{ color: "#a78bfa" }}>j</span>/
            <span style={{ color: "#a78bfa" }}>k</span> 처리 내역 행 이동
          </div>
        )}
        {filteredHistory.length === 0 ? (
          <div style={{ color: "#1e3a5c", fontSize: 12, textAlign: "center", padding: 16 }}>
            {historyEmptyMessage(history, _historyNeedle, historyStatusFilter,
              historyTimeBucket, historyModeFilter)}
          </div>
        ) : filteredHistory.map((a, idx) => (
          <HistoryRow
            key={a.id}
            a={a}
            focused={idx === historyFocusedIndex}
            onClick={() => setHistoryFocusedIndex(idx)}
          />
        ))}
        <div style={{ marginTop: 8, textAlign: "center" }}>
          {historyHasMore ? (
            <Btn
              color="#334155"
              onClick={loadMoreHistory}
              disabled={historyLoadingMore}
              small
            >
              {historyLoadingMore ? "불러오는 중…" : "더 보기"}
            </Btn>
          ) : history.length > 0 ? (
            <span style={{ fontSize: 9, color: "#1e3a5c" }}>모든 내역을 불러왔습니다</span>
          ) : null}
        </div>
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
            const result = await dispatchByAction[decisionTarget.action](
              decisionTarget.approval.id, decision,
            );
            // 072: only close on success — otherwise the dialog renders the
            // failure message inline and the operator can retry without
            // re-typing decided_by/note.
            if (result?.ok !== false) setDecisionTarget(null);
            return result;
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
            const result = await cancelMany(
              staleApprovals.map((a) => a.id), decision,
            );
            if (result?.ok !== false) setBulkOpen(false);
            return result;
          }}
        />
      )}
    </div>
  );
}
