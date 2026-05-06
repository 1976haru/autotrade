import { useState } from "react";
import { Btn, Card, Inp, SectionLabel } from "../common";
import { ChipFilterBar } from "../common/ChipFilterBar";
import { fmtKRW, pnlColor } from "../../utils/format";
import { findModeDisplay } from "../../utils/modes";
import {
  useAiAudits,
  useBacktestRuns,
  useEmergencyStopAudits,
  useOrderAudits,
} from "../../store/useAuditLogs";
import { usePersistedState } from "../../store/usePersistedState";


// 108: timeline의 주문/결재 시도 행에 운용모드를 한눈에. 092/093가 동일
// 팔레트(SIM/PAPER/SHADOW/MANUAL/AI 보조/AI 자동)를 처리 내역 chip filter와
// 24h breakdown에서 쓰는데, timeline에서도 같은 시각 단서를 제공해 운영자가
// 어떤 mode 흐름을 보고 있는지 즉시 파악. 알 수 없는 mode는 회색 + raw id로
// fallback (백엔드가 새 mode를 도입한 직후 frontend가 깨지지 않도록).
export function ModeBadge({ mode }) {
  if (!mode) return null;
  const display = findModeDisplay(mode);
  return (
    <span data-testid="mode-badge"
          data-mode={display.id}
          style={{
            color: display.color, fontSize: 9, fontWeight: 700,
            padding: "1px 6px", borderRadius: 3,
            border: `1px solid ${display.color}55`,
            background: `${display.color}15`,
          }}>
      {display.label}
    </span>
  );
}


const SUBTABS = [
  { id: "events",    label: "이벤트" },
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


export function OrderAuditRow({ r }) {
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
            color: "#7dd3fc", marginRight: 6,
            padding: "1px 5px", borderRadius: 3,
            border: "1px solid #7dd3fc55", background: "#7dd3fc15",
          }}>주문</span>
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
      <div style={{ fontSize: 10, color: "#475569", marginTop: 3,
                     display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <ModeBadge mode={r.mode} />
        <span>{new Date(r.created_at).toLocaleString("ko-KR")}</span>
        <span>·</span>
        <span>
          {r.executed
            ? `${r.broker_status} ${r.filled_quantity}@${fmtKRW(r.avg_fill_price ?? 0)}`
            : "미체결"}
        </span>
      </div>
      {r.reasons.length > 0 && (
        <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>
          {r.reasons.join(" / ")}
        </div>
      )}
    </div>
  );
}


export function EmergencyStopAuditRow({ r }) {
  const stateColor = r.enabled ? "#ef4444" : "#22c55e";
  const stateLabel = r.enabled ? "ON"      : "OFF";
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
            color: "#fbbf24", marginRight: 6,
            padding: "1px 5px", borderRadius: 3,
            border: "1px solid #fbbf2455", background: "#fbbf2415",
          }}>긴급정지</span>
          <span style={{ color: "#fbbf24", fontSize: 11, fontWeight: 700 }}>토글</span>
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: "0.04em", color: stateColor,
          padding: "1px 5px", borderRadius: 3,
          border: `1px solid ${stateColor}55`, background: `${stateColor}15`,
        }}>
          {stateLabel}
        </span>
      </div>
      <div style={{ fontSize: 10, color: "#475569", marginTop: 3 }}>
        {new Date(r.created_at).toLocaleString("ko-KR")}
        {r.decided_by ? ` · by ${r.decided_by}` : ""}
      </div>
      {r.note && (
        <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>{r.note}</div>
      )}
    </div>
  );
}


// 079/108: approvals.pending + approvals.history each carry an attempts array
// per 076. Flatten them into one event-shaped list — symbol/side/quantity
// hoisted from the parent approval so each attempt is self-describing in
// the timeline. 108: mode도 hoist해서 timeline에서 mode badge로 표시 가능.
export function flattenApprovalAttempts(pending, history) {
  const _flatten = (rows) =>
    (rows || []).flatMap((a) =>
      (a.attempts || []).map((entry) => ({
        ...entry,                  // {at, decided_by, reasons}
        approval_id: a.id,
        symbol:      a.symbol,
        side:        a.side,
        quantity:    a.quantity,
        mode:        a.mode,
      })),
    );
  return [..._flatten(pending), ..._flatten(history)];
}


export function ApprovalAttemptAuditRow({ r }) {
  const reasons = Array.isArray(r.reasons) ? r.reasons.join(" / ") : "";
  return (
    <div style={{ padding: "8px 0", borderBottom: "1px solid #05121f" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div>
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: "0.04em",
            color: "#ef4444", marginRight: 6,
            padding: "1px 5px", borderRadius: 3,
            border: "1px solid #ef444466", background: "#ef444415",
          }}>결재 시도</span>
          <span style={{ color: "#7dd3fc", fontSize: 11, fontWeight: 700 }}>{r.symbol}</span>
          <span style={{
            color: r.side === "BUY" ? "#22c55e" : "#ef4444",
            fontSize: 10, marginLeft: 8, fontWeight: 700,
          }}>{r.side}</span>
          <span style={{ color: "#94a3b8", fontSize: 11, marginLeft: 8 }}>
            {r.quantity}주
          </span>
        </div>
        <span style={{
          color: "#ef4444", fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
          padding: "1px 5px", borderRadius: 3,
          border: "1px solid #ef444455", background: "#ef444415",
        }}>
          거부됨
        </span>
      </div>
      <div style={{ fontSize: 10, color: "#475569", marginTop: 3,
                     display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <ModeBadge mode={r.mode} />
        <span>승인 #{r.approval_id}</span>
        <span>·</span>
        <span>{new Date(r.at).toLocaleString("ko-KR")}</span>
        {r.decided_by && (
          <>
            <span>·</span>
            <span>by {r.decided_by}</span>
          </>
        )}
      </div>
      {reasons && (
        <div style={{ fontSize: 9, color: "#64748b", marginTop: 2 }}>{reasons}</div>
      )}
    </div>
  );
}


// id 충돌(주문 id, 긴급정지 id, 결재 시도는 모두 별도 시퀀스)을 피하려면 React
// key로 종류를 함께 묶어야 한다. created_at(또는 at) 역순으로 병합. limit이
// 명시되면 그만큼 자르지만, 064 이후 EventTimelineView는 페이징 누적 결과를
// 통째로 넘기므로 기본은 한도 없음 (Infinity).
//
// 088: options-object 시그니처 — 050에서 시작한 (orders, stops)가 079에서
// (orders, stops, attempts, limit) 4-positional로 늘어나면서 호출부 가독성이
// 떨어졌다. 향후 새 source 종류가 추가되더라도 호출부가 깨지지 않도록 정리.
export function mergeEvents({ orders = [], stops = [], attempts = [], limit = Infinity } = {}) {
  const tagged = [
    ...orders.map((r) => ({ kind: "order", row: r, ts: new Date(r.created_at).getTime() })),
    ...stops.map((r)  => ({ kind: "stop",  row: r, ts: new Date(r.created_at).getTime() })),
    ...attempts.map((r) => ({ kind: "attempt", row: r, ts: new Date(r.at).getTime() })),
  ];
  tagged.sort((a, b) => b.ts - a.ts);
  return Number.isFinite(limit) ? tagged.slice(0, limit) : tagged;
}


const KIND_FILTERS = [
  { id: "all",     label: "전체",      color: "#7dd3fc" },
  { id: "order",   label: "주문",      color: "#7dd3fc" },
  { id: "stop",    label: "긴급정지",  color: "#fbbf24" },
  { id: "attempt", label: "결재 시도", color: "#ef4444" },
];

const KIND_FILTER_STORAGE_KEY = "autotrade.eventKindFilter";
const _VALID_KINDS = new Set(KIND_FILTERS.map((f) => f.id));
const _isValidKind = (v) => _VALID_KINDS.has(v);

// 다른 탭(Dashboard 24h drill-down 등)에서 AuditLog로 점프하기 직전에
// 미리 필터를 세팅할 때 사용. EventTimelineView가 마운트 시 localStorage에서
// 초기값을 읽으므로, 여기서 쓴 뒤 onJumpTab("audit")을 호출하면 도착 시
// 자동으로 그 필터로 렌더된다. 잘못된 kind는 무시 — 호출자 실수로 인해
// 사용자 환경설정이 망가지지 않도록 방어. usePersistedState를 우회하고
// 직접 localStorage에 쓴다 — 호출자가 React 외부 (다른 탭의 onClick) 이라서.
export function setEventKindFilter(kind) {
  if (!_VALID_KINDS.has(kind)) return;
  try {
    localStorage.setItem(KIND_FILTER_STORAGE_KEY, kind);
  } catch {
    // session-only fallback
  }
}


// Time-bucket filter — narrows the timeline by recency. Kind/symbol scope by
// what kind of event it is or which ticker; the bucket scopes by when. With
// 067 paginated history a 24h or 7d bucket is the typical "what happened
// during last night's session" cut.
const TIME_BUCKETS = [
  { id: "all", label: "전 기간", color: "#7dd3fc" },
  { id: "1h",  label: "1시간",   color: "#7dd3fc" },
  { id: "24h", label: "24시간",  color: "#7dd3fc" },
  { id: "7d",  label: "7일",     color: "#7dd3fc" },
];

const TIME_BUCKET_MS = {
  "1h":  60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  "7d":  7 * 24 * 60 * 60 * 1000,
};

const TIME_BUCKET_STORAGE_KEY = "autotrade.eventTimeBucket";
const _VALID_BUCKETS = new Set(TIME_BUCKETS.map((b) => b.id));
const _isValidBucket = (v) => _VALID_BUCKETS.has(v);


// Distinguish "really nothing" from "filters narrowed it to zero" so the
// operator who tightened symbol + time-bucket + kind doesn't think the
// audit log broke. The chips above the empty message show *which* filters
// are active — the message just signals that filters are responsible.
export function emptyEventTimelineMessage(kind, symbolFilter, timeBucket) {
  const hasFilter =
    (kind && kind !== "all")
    || (symbolFilter && symbolFilter.trim() !== "")
    || (timeBucket && timeBucket !== "all");
  return hasFilter ? "해당 조건의 이벤트 없음" : "이벤트 없음";
}


export function TimeBucketBar({ active, onChange }) {
  return (
    <ChipFilterBar items={TIME_BUCKETS} active={active}
      onChange={onChange} ariaLabel="시간 범위 필터" />
  );
}


export function KindFilterBar({ active, onChange }) {
  return (
    <ChipFilterBar items={KIND_FILTERS} active={active}
      onChange={onChange} ariaLabel="이벤트 종류 필터" />
  );
}


export function EventTimelineView({ approvals = { pending: [], history: [] } }) {
  const orders = useOrderAudits();
  const stops  = useEmergencyStopAudits();
  // Persisted filters share the 074 usePersistedState pattern; symbol stays
  // transient (each investigation session uses a different ticker).
  const [kind, setKind] = usePersistedState(KIND_FILTER_STORAGE_KEY, "all", _isValidKind);
  const [symbolFilter, setSymbolFilter] = useState("");
  const symbolNeedle = symbolFilter.trim().toLowerCase();
  const [timeBucket, setTimeBucket] = usePersistedState(
    TIME_BUCKET_STORAGE_KEY, "all", _isValidBucket,
  );
  const _bucketWindowMs = TIME_BUCKET_MS[timeBucket]; // undefined for "all"
  const _now = Date.now();
  // Orders/stops use created_at; attempts (079) use `at` — different field
  // names, same elapsed-time semantics.
  const _withinBucket = (timestamp) =>
    _bucketWindowMs === undefined
      ? true
      : _now - new Date(timestamp).getTime() < _bucketWindowMs;

  // 필터를 mergeEvents *전에* 적용 — top-50 cap 안에서 한쪽 종류가 밀려나
  // 사라지는 일을 방지한다 ("긴급정지만" 선택 시 가장 최근 50건의 stop이
  // 보장되어야지, 시간상 우연히 50개 주문 사이에 끼어든 stop만 보여서는 안 됨).
  // Symbol 필터는 symbol을 가진 행에만 의미 있음 (주문/결재 시도) — 긴급정지는
  // mode-wide 이벤트라 검색 중에도 컨텍스트로서 유지 (kind로 명시 끄기 가능).
  // 시간 bucket은 universal — 모든 종류에 적용.
  const flatAttempts = flattenApprovalAttempts(approvals.pending, approvals.history);

  const filteredOrders = (kind === "all" || kind === "order" ? orders.items : [])
    .filter((r) => !symbolNeedle || r.symbol.toLowerCase().includes(symbolNeedle))
    .filter((r) => _withinBucket(r.created_at));
  const filteredStops = (kind === "all" || kind === "stop" ? stops.items : [])
    .filter((r) => _withinBucket(r.created_at));
  const filteredAttempts = (kind === "all" || kind === "attempt" ? flatAttempts : [])
    .filter((r) => !symbolNeedle || r.symbol.toLowerCase().includes(symbolNeedle))
    .filter((r) => _withinBucket(r.at));
  // 064: 페이징 누적 결과 전부 보여줌 (기본 Infinity).
  const events = mergeEvents({
    orders: filteredOrders, stops: filteredStops, attempts: filteredAttempts,
  });

  const loading = orders.loading || stops.loading;
  // 두 소스 중 하나라도 실패하면 그 메시지를 보여줌. 둘 다 실패하면 주문 쪽
  // 메시지가 우선 — 운영자 입장에선 어느 하나가 깨졌다는 사실이 중요하지
  // 정확히 어느 쪽인지는 부차적.
  const error = orders.error || stops.error;
  const refresh = () => { orders.refresh(); stops.refresh(); };

  // "더 보기"는 현재 필터 종류에 해당하는 소스만 추가 페이지를 가져온다.
  // 전체 모드면 양쪽 모두 — 한쪽이 끝나도 다른 쪽이 더 있으면 버튼 유지.
  // attempts는 approvals prop에서 통째로 와서 페이징 없음 — has-more는 항상 false.
  const sourceHasMore = (() => {
    if (kind === "order")   return orders.hasMore;
    if (kind === "stop")    return stops.hasMore;
    if (kind === "attempt") return false;
    return orders.hasMore || stops.hasMore;
  })();
  const sourceLoadingMore = orders.loadingMore || stops.loadingMore;
  const loadMore = () => {
    if ((kind === "all" || kind === "order") && orders.hasMore) orders.loadMore();
    if ((kind === "all" || kind === "stop")  && stops.hasMore)  stops.loadMore();
  };

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>이벤트 타임라인 ({events.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      <div style={{ fontSize: 9, color: "#334155", marginBottom: 8, lineHeight: 1.5 }}>
        주문 감사 로그와 긴급정지 토글을 시간 역순으로 병합. 사고 분석 시 한 화면에서
        "어떤 주문이 있었고 그 사이 긴급정지가 어떻게 움직였는지"를 함께 본다.
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
        <KindFilterBar active={kind} onChange={setKind} />
        <div style={{ flex: 1, minWidth: 100 }}>
          <Inp
            value={symbolFilter}
            onChange={setSymbolFilter}
            placeholder="🔍 종목 (예: 005930)"
          />
        </div>
      </div>

      <div style={{ marginBottom: 8 }}>
        <TimeBucketBar active={timeBucket} onChange={setTimeBucket} />
      </div>

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : events.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>
          {emptyEventTimelineMessage(kind, symbolFilter, timeBucket)}
        </div>
      ) : (
        <>
          {events.map(({ kind: rowKind, row }) => {
            if (rowKind === "order")
              return <OrderAuditRow         key={`order-${row.id}`} r={row} />;
            if (rowKind === "stop")
              return <EmergencyStopAuditRow key={`stop-${row.id}`}  r={row} />;
            return (
              <ApprovalAttemptAuditRow
                key={`attempt-${row.approval_id}-${row.at}`}
                r={row}
              />
            );
          })}
          <div style={{ marginTop: 10, textAlign: "center" }}>
            {sourceHasMore ? (
              <Btn
                color="#334155"
                onClick={loadMore}
                disabled={sourceLoadingMore}
                small
              >
                {sourceLoadingMore ? "불러오는 중…" : "더 보기"}
              </Btn>
            ) : (
              <span style={{ fontSize: 9, color: "#1e3a5c" }}>모든 이벤트를 불러왔습니다</span>
            )}
          </div>
        </>
      )}
    </Card>
  );
}


// 089/091/094: distinguish "the AI call log is genuinely empty" from "filter
// narrowed it to zero". 089 introduced this for ticker; 091 extended to
// time bucket; 094 adds model. Mirrors 081/082 multi-axis empty-state convention.
export function aiAuditEmptyMessage(items, tickerNeedle, timeBucket, modelNeedle) {
  if (!items || items.length === 0) return "AI 호출 기록 없음";
  const hasFilter =
    (tickerNeedle && tickerNeedle.length > 0)
    || (timeBucket && timeBucket !== "all")
    || (modelNeedle && modelNeedle.length > 0);
  return hasFilter ? "해당 조건의 AI 호출 없음" : "AI 호출 기록 없음";
}


// 094: model 색상 — Anthropic 모델 family로 prefix 매칭. Opus는 보라
// (most-capable, 비싸다는 신호), Sonnet은 청록 (balanced default), Haiku는
// 노랑 (가벼운 호출). 알 수 없는 모델은 회색 — 운영자가 model 필드를 한눈에
// scan하면서 비용 클래스/응답 품질을 추정하도록.
export function modelAccent(model) {
  if (!model) return "#475569";
  const m = model.toLowerCase();
  if (m.includes("opus"))   return "#c084fc";
  if (m.includes("sonnet")) return "#67e8f9";
  if (m.includes("haiku"))  return "#fbbf24";
  return "#475569";
}


// 101: model family lookup — 단가 lookup 등 family 기반 분기에 공통으로 쓰인다.
// modelAccent 와 같은 prefix 매칭 규칙을 공유한다.
export function modelFamily(model) {
  if (!model) return null;
  const m = model.toLowerCase();
  if (m.includes("opus"))   return "opus";
  if (m.includes("sonnet")) return "sonnet";
  if (m.includes("haiku"))  return "haiku";
  return null;
}


// 101: Anthropic 단가 (USD per 1M tokens). 정확한 단가는 시점에 따라 변할 수
// 있고 enterprise/scale 가격은 다를 수 있다 — 여기 값은 2026년 5월 시점 공개
// 표준 가격을 기준으로 한 *추정값*이다. UI는 항상 "약 $X.XX"로 surfacing해
// 정확한 청구액이 아님을 시그널링한다.
export const AI_MODEL_PRICING = {
  opus:   { input: 15.0, output: 75.0 },
  sonnet: { input:  3.0, output: 15.0 },
  haiku:  { input:  0.8, output:  4.0 },
};


// 단가가 알려진 row만 합산하고, 그 외는 unknownCount로 따로 보고. UI는 합계
// 옆에 "단가 미상 K건"을 부가해 신뢰도를 명시할 수 있다.
export function estimateAiCost(items) {
  let totalUsd = 0;
  let knownCount = 0;
  let unknownCount = 0;
  for (const r of items || []) {
    const family = modelFamily(r.model);
    const price = family && AI_MODEL_PRICING[family];
    if (!price) {
      unknownCount += 1;
      continue;
    }
    const inTok  = r.input_tokens  || 0;
    const outTok = r.output_tokens || 0;
    totalUsd += (inTok / 1_000_000) * price.input
              + (outTok / 1_000_000) * price.output;
    knownCount += 1;
  }
  return { totalUsd, knownCount, unknownCount };
}


// 작은 비용은 "<$0.01"로 표시 — 0.00달러로 보이면 "공짜" 같은 인상을 줘서
// 운영자가 빈도/볼륨 신호를 놓칠 수 있다. 0인 경우는 그대로 "$0.00".
export function formatUsdCost(usd) {
  if (usd <= 0) return "$0.00";
  if (usd < 0.01) return "<$0.01";
  return "$" + usd.toFixed(2);
}


// 112: 094 model badge / 098 token sum / 101 cost 흐름의 마지막 시각화 단계.
// model family별로 호출 수 + token 분포를 한 줄 chip + mini stacked bar로 노출.
// 운영자가 "오늘 sonnet에 70% 트래픽이 몰려 있는데 haiku로 옮길 여지 있나?"
// 같은 cost 분배 의사결정을 한눈에. 가족 순서는 위험/비용 오름차순 — opus가
// 가장 비싸고 unknown이 끝.
const _AI_FAMILY_ORDER = ["opus", "sonnet", "haiku", "unknown"];

export function formatAiTokenByModel(items) {
  const byFamily = new Map();
  for (const r of items || []) {
    const f = modelFamily(r.model) || "unknown";
    const cur = byFamily.get(f) || { count: 0, inputTotal: 0, outputTotal: 0 };
    cur.count       += 1;
    cur.inputTotal  += r.input_tokens  || 0;
    cur.outputTotal += r.output_tokens || 0;
    byFamily.set(f, cur);
  }
  return _AI_FAMILY_ORDER
    .filter((f) => byFamily.has(f))
    .map((f) => ({
      family: f,
      label:  f === "unknown" ? "기타" : f,
      // modelAccent("opus") / ("sonnet") / ("haiku")가 동일 prefix 매칭으로 색을
      // 돌려준다. unknown은 명시적으로 회색.
      color:  f === "unknown" ? "#475569" : modelAccent(f),
      ...byFamily.get(f),
    }));
}


export function AiTokenByModel({ items }) {
  const cells = formatAiTokenByModel(items);
  if (cells.length === 0) return null;
  const _fmt = (n) => n.toLocaleString("ko-KR");
  return (
    <div data-testid="ai-token-by-model"
         style={{ marginBottom: 8, padding: "4px 0",
                  borderBottom: "1px dashed #0c2035" }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, fontSize: 9, marginBottom: 4 }}>
        {cells.map((c) => (
          <span key={c.family}
                data-testid={`ai-token-by-model-cell-${c.family}`}
                style={{
                  color: c.color, fontWeight: 700,
                  padding: "1px 6px", borderRadius: 3,
                  border: `1px solid ${c.color}55`, background: `${c.color}15`,
                }}>
            {c.label} {c.count}건 · in {_fmt(c.inputTotal)} · out {_fmt(c.outputTotal)}
          </span>
        ))}
      </div>
      <div style={{ display: "flex", height: 4, borderRadius: 2, overflow: "hidden",
                    background: "#020e1c" }}>
        {cells.map((c) => {
          const total = c.inputTotal + c.outputTotal;
          if (total <= 0) return null;
          return (
            <div key={c.family}
                 data-testid={`ai-token-by-model-bar-${c.family}`}
                 style={{ flex: total, background: c.color }} />
          );
        })}
      </div>
    </div>
  );
}


export function AiModelBadge({ model }) {
  if (!model) return null;
  const color = modelAccent(model);
  return (
    <span data-testid="ai-model-badge"
          style={{
            color: color, fontSize: 9, fontWeight: 700,
            padding: "1px 6px", borderRadius: 3,
            border: `1px solid ${color}55`, background: `${color}15`,
          }}>
      {model}
    </span>
  );
}


// 098: 필터 적용 후 AI 호출 비용 추정 보조. 094 model 칩으로 모델 분포는
// 보지만 token 총량은 row 단위 ("tok in/out")만 보여 scan에 시간이 걸린다.
// summarizeAiTokens는 filteredItems에 대해 N회 / in / out 합산 — 운영자가
// "오늘 sonnet 호출만 3 만 in / 1.5만 out 넘었다" 같은 비용 신호를 즉시 파악.
//
// input_tokens/output_tokens 누락 row는 0으로 취급 (백엔드가 None으로 직렬화
// 한 경우 등). NaN 누적을 방지.
export function summarizeAiTokens(items) {
  let inputTotal = 0;
  let outputTotal = 0;
  for (const r of items || []) {
    inputTotal  += r.input_tokens  || 0;
    outputTotal += r.output_tokens || 0;
  }
  return { count: (items || []).length, inputTotal, outputTotal };
}


export function AiTokenSummary({ items }) {
  if (!items || items.length === 0) return null;
  const s = summarizeAiTokens(items);
  const c = estimateAiCost(items);  // 101: family-based USD estimate
  const _fmt = (n) => n.toLocaleString("ko-KR");
  return (
    <div data-testid="ai-token-summary"
         style={{ fontSize: 10, color: "#64748b", marginBottom: 8,
                  display: "flex", gap: 8, flexWrap: "wrap",
                  padding: "4px 0", borderBottom: "1px dashed #0c2035" }}>
      <span>총 {_fmt(s.count)}회</span>
      <span>·</span>
      <span style={{ color: "#67e8f9", fontWeight: 700 }}>
        in {_fmt(s.inputTotal)}
      </span>
      <span>·</span>
      <span style={{ color: "#c084fc", fontWeight: 700 }}>
        out {_fmt(s.outputTotal)}
      </span>
      <span>·</span>
      <span data-testid="ai-cost-estimate"
            style={{ color: "#fbbf24", fontWeight: 700 }}>
        약 {formatUsdCost(c.totalUsd)}
      </span>
      {c.unknownCount > 0 && (
        <span data-testid="ai-cost-unknown"
              style={{ color: "#64748b" }}>
          (+ 미상 {_fmt(c.unknownCount)}건)
        </span>
      )}
    </div>
  );
}


// 091: time bucket persistence — same pattern as 073 (audit timeline) and
// 086 (approvals history). Reuses TIME_BUCKETS / TIME_BUCKET_MS / _isValidBucket
// from 073 (same module, same shape) but a distinct storage key so the AI
// sub-tab's selection doesn't collide with the event timeline's.
const AI_TIME_BUCKET_STORAGE_KEY = "autotrade.aiAuditTimeBucket";


export function AiAuditView() {
  const { items, loading, error, refresh } = useAiAudits();
  // 089: transient ticker filter (not persisted) — same reasoning as the
  // audit timeline symbol filter (067) and the approvals symbol filter (082):
  // each investigation focuses on a different ticker, and clearing on remount
  // matches that workflow.
  const [tickerFilter, setTickerFilter] = useState("");
  const _tickerNeedle = tickerFilter.trim().toLowerCase();
  // 094: transient model filter — substring match, mirrors 089 ticker pattern.
  // Operators investigating cost/quality (e.g. "오늘 sonnet 호출만 보고 싶다")
  // type a fragment and the list narrows. Not persisted because each session
  // tends to compare different model classes.
  const [modelFilter, setModelFilter] = useState("");
  const _modelNeedle = modelFilter.trim().toLowerCase();
  // 091: persisted time bucket — investigation sessions tend to fix a window
  // ("recent 24h") for a stretch.
  const [timeBucket, setTimeBucket] = usePersistedState(
    AI_TIME_BUCKET_STORAGE_KEY, "all", _isValidBucket,
  );
  const _bucketWindowMs = TIME_BUCKET_MS[timeBucket];
  const _now = Date.now();
  const _withinBucket = (r) =>
    _bucketWindowMs === undefined
      ? true
      : _now - new Date(r.created_at).getTime() < _bucketWindowMs;
  const filteredItems = items
    .filter((r) =>
      !_tickerNeedle || (r.ticker && r.ticker.toLowerCase().includes(_tickerNeedle)))
    .filter((r) =>
      !_modelNeedle || (r.model && r.model.toLowerCase().includes(_modelNeedle)))
    .filter(_withinBucket);

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>AI 분석 감사 로그 ({filteredItems.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
        <div style={{ flex: 1 }}>
          <Inp
            value={tickerFilter}
            onChange={setTickerFilter}
            placeholder="🔍 종목 (예: 005930)"
          />
        </div>
        <div style={{ flex: 1 }}>
          <Inp
            value={modelFilter}
            onChange={setModelFilter}
            placeholder="🔍 모델 (예: sonnet)"
          />
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <ChipFilterBar
          items={TIME_BUCKETS}
          active={timeBucket}
          onChange={setTimeBucket}
          ariaLabel="AI 호출 시간 범위 필터"
        />
      </div>

      <AiTokenSummary items={filteredItems} />
      <AiTokenByModel items={filteredItems} />

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : filteredItems.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>
          {aiAuditEmptyMessage(items, _tickerNeedle, timeBucket, _modelNeedle)}
        </div>
      ) : filteredItems.map((r) => (
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
          <div style={{ fontSize: 10, color: "#475569", marginTop: 3,
                         display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
            <span>{new Date(r.created_at).toLocaleString("ko-KR")}</span>
            <AiModelBadge model={r.model} />
          </div>
          {r.error && (
            <div style={{ fontSize: 9, color: "#f87171", marginTop: 2 }}>오류: {r.error}</div>
          )}
        </div>
      ))}
    </Card>
  );
}


// 099: BacktestRun 스키마에는 status/error 필드가 없다 — 백엔드가 실행 실패
// 시 row를 만들지 않는 단순 구조라 "성공/실패" 분류는 client에서 만들 수 없다.
// 대신 운영자에게 더 의미 있는 outcome(수익/손실/break-even) 분류 chip을 둔다.
// total_pnl 부호로 분류하므로 schema 변경 0건.
export function classifyBacktestOutcome(run) {
  if (!run || run.total_pnl === undefined || run.total_pnl === null) return "breakeven";
  if (run.total_pnl > 0) return "profit";
  if (run.total_pnl < 0) return "loss";
  return "breakeven";
}


// 104: 정렬 — 백엔드는 created_at desc 고정으로 내려주는데, 운영자는 종종
// "이번 주 가장 잘 된 sma 변형"을 위해 total_pnl desc 또는 win_rate desc로
// 보고 싶다. client-side sort로 충분 (pagination 없는 단일 페이지 50건).
export function backtestWinRate(run) {
  const w = run?.win_count || 0;
  const l = run?.loss_count || 0;
  const trades = w + l;
  return trades > 0 ? w / trades : 0;
}


export function sortBacktestRuns(items, sortKey) {
  const arr = [...(items || [])];
  if (sortKey === "pnl") {
    arr.sort((a, b) => (b.total_pnl || 0) - (a.total_pnl || 0));
  } else if (sortKey === "win_rate") {
    arr.sort((a, b) => backtestWinRate(b) - backtestWinRate(a));
  } else {
    // 'recent' (default) — created_at desc. 백엔드가 이미 그 순서지만 정렬을
    // 한 번 더 적용해 client-side에서 일관성 보장 (mock 데이터 등 비정렬 입력
    // 대비).
    arr.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  }
  return arr;
}


const BACKTEST_SORT_OPTIONS = [
  { id: "recent",   label: "최근순", color: "#7dd3fc" },
  { id: "pnl",      label: "수익순", color: "#22c55e" },
  { id: "win_rate", label: "승률순", color: "#a78bfa" },
];

export const BACKTEST_SORT_STORAGE_KEY = "autotrade.backtestSort";
const _VALID_BACKTEST_SORTS = new Set(BACKTEST_SORT_OPTIONS.map((o) => o.id));
export const isValidBacktestSort = (v) => _VALID_BACKTEST_SORTS.has(v);


export function BacktestSortBar({ active, onChange }) {
  return (
    <ChipFilterBar items={BACKTEST_SORT_OPTIONS} active={active}
      onChange={onChange} ariaLabel="백테스트 정렬" />
  );
}


const BACKTEST_OUTCOME_FILTERS = [
  { id: "all",       label: "전체",     color: "#7dd3fc" },
  { id: "profit",    label: "수익",     color: "#22c55e" },
  { id: "loss",      label: "손실",     color: "#ef4444" },
  { id: "breakeven", label: "브레이크", color: "#94a3b8" },
];

export const BACKTEST_OUTCOME_STORAGE_KEY = "autotrade.backtestOutcomeFilter";
const _VALID_BACKTEST_OUTCOMES = new Set(BACKTEST_OUTCOME_FILTERS.map((f) => f.id));
export const isValidBacktestOutcome = (v) => _VALID_BACKTEST_OUTCOMES.has(v);


export function BacktestOutcomeFilterBar({ active, onChange }) {
  return (
    <ChipFilterBar items={BACKTEST_OUTCOME_FILTERS} active={active}
      onChange={onChange} ariaLabel="백테스트 결과 필터" />
  );
}


// 090/099: distinguish "no backtest runs at all" from "filter narrowed to zero",
// matching 081/082/089 empty-state convention. 099 adds outcome axis — same
// shape as 092's modeFilter extension, so backwards-compatible if outcome arg
// is omitted.
export function backtestEmptyMessage(items, strategyNeedle, outcome) {
  if (!items || items.length === 0) return "백테스트 실행 기록 없음";
  const hasFilter =
    (strategyNeedle && strategyNeedle.length > 0)
    || (outcome && outcome !== "all");
  return hasFilter ? "해당 조건의 백테스트 없음" : "백테스트 실행 기록 없음";
}


export function BacktestRunsView() {
  const { items, loading, error, refresh } = useBacktestRuns();
  // 090: transient strategy filter (not persisted) — operators investigating
  // a specific strategy (sma_crossover, etc.) want to see only its runs.
  // Same shape as 089 ticker filter on AI sub-tab.
  const [strategyFilter, setStrategyFilter] = useState("");
  const _strategyNeedle = strategyFilter.trim().toLowerCase();
  // 099: persisted outcome filter — investigation sessions tend to lock onto
  // 손실 케이스 한 번에 검토하기 같은 흐름이 길게 이어진다. 083/086/092
  // status/time/mode와 같은 영구 저장 패턴.
  const [outcomeFilter, setOutcomeFilter] = usePersistedState(
    BACKTEST_OUTCOME_STORAGE_KEY, "all", isValidBacktestOutcome,
  );
  // 104: 정렬 — 최근/수익/승률. 영구 저장.
  const [sortKey, setSortKey] = usePersistedState(
    BACKTEST_SORT_STORAGE_KEY, "recent", isValidBacktestSort,
  );
  const filteredItems = sortBacktestRuns(
    items
      .filter((r) =>
        !_strategyNeedle || (r.strategy && r.strategy.toLowerCase().includes(_strategyNeedle)))
      .filter((r) => outcomeFilter === "all" || classifyBacktestOutcome(r) === outcomeFilter),
    sortKey,
  );

  return (
    <Card>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <SectionLabel>백테스트 실행 로그 ({filteredItems.length})</SectionLabel>
        <Btn color="#334155" onClick={refresh} disabled={loading} small>새로고침</Btn>
      </div>

      <div style={{ marginBottom: 8 }}>
        <Inp
          value={strategyFilter}
          onChange={setStrategyFilter}
          placeholder="🔍 전략 (예: sma_crossover)"
        />
      </div>
      <div style={{ marginBottom: 8 }}>
        <BacktestOutcomeFilterBar
          active={outcomeFilter}
          onChange={setOutcomeFilter}
        />
      </div>
      <div style={{ marginBottom: 8 }}>
        <BacktestSortBar
          active={sortKey}
          onChange={setSortKey}
        />
      </div>

      {error && <div style={{ color: "#f87171", fontSize: 11, marginBottom: 8 }}>{error}</div>}

      {loading ? (
        <div style={{ color: "#475569", fontSize: 11, padding: 12, textAlign: "center" }}>로딩 중…</div>
      ) : filteredItems.length === 0 ? (
        <div style={{ color: "#1e3a5c", fontSize: 12, padding: 16, textAlign: "center" }}>
          {backtestEmptyMessage(items, _strategyNeedle, outcomeFilter)}
        </div>
      ) : filteredItems.map((r) => {
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


export function AuditLog({ approvals }) {
  const [view, setView] = useState("events");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <SubTabBar active={view} onChange={setView} />
      {view === "events"    && <EventTimelineView approvals={approvals} />}
      {view === "ai"        && <AiAuditView />}
      {view === "backtests" && <BacktestRunsView />}
    </div>
  );
}
