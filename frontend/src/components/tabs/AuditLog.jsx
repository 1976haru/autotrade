import { useState } from "react";
import { Btn, Card, Inp, SectionLabel } from "../common";
import { fmtKRW, pnlColor } from "../../utils/format";
import {
  useAiAudits,
  useBacktestRuns,
  useEmergencyStopAudits,
  useOrderAudits,
} from "../../store/useAuditLogs";


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


// id 충돌(주문 id와 긴급정지 id는 별도 시퀀스)을 피하려면 React key로 종류를
// 함께 묶어야 한다. created_at 역순으로 병합. limit이 명시되면 그만큼 자르지만,
// 064 이후 EventTimelineView는 페이징 누적 결과를 통째로 넘기므로 기본은 한도 없음
// (Infinity). 호출자가 cap을 두고 싶을 때만 명시.
export function mergeEvents(orders, stops, limit = Infinity) {
  const tagged = [
    ...orders.map((r) => ({ kind: "order", row: r, ts: new Date(r.created_at).getTime() })),
    ...stops.map((r)  => ({ kind: "stop",  row: r, ts: new Date(r.created_at).getTime() })),
  ];
  tagged.sort((a, b) => b.ts - a.ts);
  return Number.isFinite(limit) ? tagged.slice(0, limit) : tagged;
}


const KIND_FILTERS = [
  { id: "all",   label: "전체",     color: "#7dd3fc" },
  { id: "order", label: "주문",     color: "#7dd3fc" },
  { id: "stop",  label: "긴급정지", color: "#fbbf24" },
];

const KIND_FILTER_STORAGE_KEY = "autotrade.eventKindFilter";
const _VALID_KINDS = new Set(KIND_FILTERS.map((f) => f.id));

// 사고 분석 도중 새로고침/탭 전환해도 운영자의 필터 선택이 유지되도록
// localStorage에 보관. 저장된 값이 KIND_FILTERS에 없는 종류면 (예전 빌드의
// 잔재나 외부 변조) 안전한 기본값 "all"로 폴백.
function _readKindFilter() {
  try {
    const v = localStorage.getItem(KIND_FILTER_STORAGE_KEY);
    return _VALID_KINDS.has(v) ? v : "all";
  } catch {
    return "all";
  }
}

function _writeKindFilter(value) {
  try {
    localStorage.setItem(KIND_FILTER_STORAGE_KEY, value);
  } catch {
    // 영속화 실패는 사용자 경험에 직접 영향 없음 — 이 세션 한정.
  }
}

// 다른 탭(Dashboard 24h drill-down 등)에서 AuditLog로 점프하기 직전에
// 미리 필터를 세팅할 때 사용. EventTimelineView가 마운트 시 localStorage에서
// 초기값을 읽으므로, 여기서 쓴 뒤 onJumpTab("audit")을 호출하면 도착 시
// 자동으로 그 필터로 렌더된다. 잘못된 kind는 무시 — 호출자 실수로 인해
// 사용자 환경설정이 망가지지 않도록 방어.
export function setEventKindFilter(kind) {
  if (!_VALID_KINDS.has(kind)) return;
  _writeKindFilter(kind);
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

function _readTimeBucket() {
  try {
    const v = localStorage.getItem(TIME_BUCKET_STORAGE_KEY);
    return _VALID_BUCKETS.has(v) ? v : "all";
  } catch {
    return "all";
  }
}

function _writeTimeBucket(value) {
  try {
    localStorage.setItem(TIME_BUCKET_STORAGE_KEY, value);
  } catch {
    // session-only fallback
  }
}


export function TimeBucketBar({ active, onChange }) {
  return (
    <div role="radiogroup" aria-label="시간 범위 필터"
         style={{ display: "flex", gap: 4 }}>
      {TIME_BUCKETS.map((b) => {
        const isActive = active === b.id;
        return (
          <button
            key={b.id}
            role="radio"
            aria-checked={isActive}
            onClick={() => onChange(b.id)}
            style={{
              padding: "5px 10px", borderRadius: 12, cursor: "pointer",
              fontFamily: "inherit", fontSize: 10, fontWeight: 700,
              background: isActive ? `${b.color}22` : "transparent",
              border:     `1px solid ${isActive ? b.color : "#1a3a5c"}`,
              color:      isActive ? b.color : "#475569",
            }}
          >
            {b.label}
          </button>
        );
      })}
    </div>
  );
}


export function KindFilterBar({ active, onChange }) {
  return (
    <div role="radiogroup" aria-label="이벤트 종류 필터"
         style={{ display: "flex", gap: 4 }}>
      {KIND_FILTERS.map((f) => {
        const isActive = active === f.id;
        return (
          <button
            key={f.id}
            role="radio"
            aria-checked={isActive}
            onClick={() => onChange(f.id)}
            style={{
              padding: "5px 10px", borderRadius: 12, cursor: "pointer",
              fontFamily: "inherit", fontSize: 10, fontWeight: 700,
              background: isActive ? `${f.color}22` : "transparent",
              border:     `1px solid ${isActive ? f.color : "#1a3a5c"}`,
              color:      isActive ? f.color : "#475569",
            }}
          >
            {f.label}
          </button>
        );
      })}
    </div>
  );
}


export function EventTimelineView() {
  const orders = useOrderAudits();
  const stops  = useEmergencyStopAudits();
  const [kind, _setKind] = useState(_readKindFilter);
  const setKind = (value) => { _setKind(value); _writeKindFilter(value); };
  // Symbol filter is intentionally NOT persisted — it's a transient
  // investigation tool ("show me everything that happened around 005930"),
  // unlike the kind filter which the operator picks for a session.
  const [symbolFilter, setSymbolFilter] = useState("");
  const symbolNeedle = symbolFilter.trim().toLowerCase();
  // Time-bucket filter persists like kind: operator typically wants the same
  // window across page refreshes during an investigation session.
  const [timeBucket, _setTimeBucket] = useState(_readTimeBucket);
  const setTimeBucket = (v) => { _setTimeBucket(v); _writeTimeBucket(v); };
  const _bucketWindowMs = TIME_BUCKET_MS[timeBucket]; // undefined for "all"
  const _now = Date.now();
  const _matchesBucket = (r) =>
    _bucketWindowMs === undefined
      ? true
      : _now - new Date(r.created_at).getTime() < _bucketWindowMs;

  // 필터를 mergeEvents *전에* 적용 — top-50 cap 안에서 한쪽 종류가 밀려나
  // 사라지는 일을 방지한다 ("긴급정지만" 선택 시 가장 최근 50건의 stop이
  // 보장되어야지, 시간상 우연히 50개 주문 사이에 끼어든 stop만 보여서는 안 됨).
  // Symbol 필터는 주문 행에만 적용 — 긴급정지는 mode-wide 이벤트라 종목 검색의
  // 의미상 매칭 대상이 아니지만, 검색 중에도 그 시점에 무엇이 있었는지 보여주는
  // 컨텍스트로서 유지된다 (kind 필터로 명시적으로 끌 수 있음).
  // 시간 bucket은 universal — 주문/긴급정지 모두에 적용.
  const filteredOrders = (kind === "stop" ? [] : orders.items)
    .filter((r) => !symbolNeedle || r.symbol.toLowerCase().includes(symbolNeedle))
    .filter(_matchesBucket);
  const filteredStops = (kind === "order" ? [] : stops.items).filter(_matchesBucket);
  // 064: 페이징 누적 결과 전부 보여줌 (기본 Infinity).
  const events = mergeEvents(filteredOrders, filteredStops);

  const loading = orders.loading || stops.loading;
  // 두 소스 중 하나라도 실패하면 그 메시지를 보여줌. 둘 다 실패하면 주문 쪽
  // 메시지가 우선 — 운영자 입장에선 어느 하나가 깨졌다는 사실이 중요하지
  // 정확히 어느 쪽인지는 부차적.
  const error = orders.error || stops.error;
  const refresh = () => { orders.refresh(); stops.refresh(); };

  // "더 보기"는 현재 필터 종류에 해당하는 소스만 추가 페이지를 가져온다.
  // 전체 모드면 양쪽 모두 — 한쪽이 끝나도 다른 쪽이 더 있으면 버튼 유지.
  const sourceHasMore = (() => {
    if (kind === "order") return orders.hasMore;
    if (kind === "stop")  return stops.hasMore;
    return orders.hasMore || stops.hasMore;
  })();
  const sourceLoadingMore = orders.loadingMore || stops.loadingMore;
  const loadMore = () => {
    if (kind !== "stop"  && orders.hasMore) orders.loadMore();
    if (kind !== "order" && stops.hasMore)  stops.loadMore();
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
          {kind === "all" ? "이벤트 없음" : "해당 종류의 이벤트 없음"}
        </div>
      ) : (
        <>
          {events.map(({ kind: rowKind, row }) => (
            rowKind === "order"
              ? <OrderAuditRow         key={`order-${row.id}`} r={row} />
              : <EmergencyStopAuditRow key={`stop-${row.id}`}  r={row} />
          ))}
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
  const [view, setView] = useState("events");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <SubTabBar active={view} onChange={setView} />
      {view === "events"    && <EventTimelineView />}
      {view === "ai"        && <AiAuditView />}
      {view === "backtests" && <BacktestRunsView />}
    </div>
  );
}
