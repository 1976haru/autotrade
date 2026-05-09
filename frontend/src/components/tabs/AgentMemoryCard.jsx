import { useEffect, useMemo, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// Agent Memory card — 과거 운영 사례 / 손실 원인 / 전략 변경 이력 검색.
//
// **주문 신호 아님 · 과거 학습 기록**. 검색 결과는 *advisory*만 — UI에서 BUY/
// SELL/HOLD / 즉시 주문 / 매수 매도 버튼을 *생성하지 않는다*. 본 카드의 모든
// 액션은 검색 / 조회 / archive / 운영 메모 추가만 수행.

const _MEMORY_TYPES = [
  ["", "전체"],
  ["operator_note",     "운영 메모"],
  ["daily_report",      "일일 리포트"],
  ["risk_incident",     "리스크 사례"],
  ["strategy_research", "전략 연구"],
  ["backtest_review",   "백테스트 검토"],
  ["agent_decision",    "Agent 결정"],
  ["loss_post_mortem",  "손실 분석"],
  ["lesson_learned",    "교훈"],
];

const _SEVERITY_COLOR = {
  INFO:     "#94a3b8",
  WARN:     "#fbbf24",
  HIGH:     "#fb923c",
  CRITICAL: "#ef4444",
};


function _MemoryRow({ rec, onOpen, onArchive }) {
  const sevColor = _SEVERITY_COLOR[rec.severity] || "#94a3b8";
  return (
    <div data-testid={`agent-memory-row-${rec.id}`}
         style={{ padding: "8px 10px", marginBottom: 6,
                   background: "#0c2035", borderRadius: 4,
                   borderLeft: `3px solid ${sevColor}`,
                   fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     marginBottom: 3, alignItems: "baseline" }}>
        <span style={{ fontWeight: 700, color: "#e2e8f0" }}>{rec.title}</span>
        <span style={{ fontSize: 9, fontWeight: 700, color: sevColor }}>
          {rec.severity}
        </span>
      </div>
      <div style={{ color: "#94a3b8", fontSize: 10, marginBottom: 3 }}>
        <span style={{ fontFamily: "monospace" }}>{rec.memory_type}</span>
        {rec.strategy && <> · <span>{rec.strategy}</span></>}
        {rec.symbol && <> · <span>{rec.symbol}</span></>}
        {rec.mode && <> · <span>{rec.mode}</span></>}
      </div>
      <div style={{ color: "#cbd5e1", lineHeight: 1.5,
                     overflow: "hidden",
                     textOverflow: "ellipsis",
                     display: "-webkit-box",
                     WebkitLineClamp: 3, WebkitBoxOrient: "vertical" }}>
        {rec.summary}
      </div>
      {rec.tags && rec.tags.length > 0 && (
        <div data-testid={`agent-memory-tags-${rec.id}`}
             style={{ marginTop: 4 }}>
          {rec.tags.slice(0, 5).map((t, i) => (
            <span key={i} style={{
              display: "inline-block", margin: "1px 3px 0 0",
              fontSize: 9, padding: "1px 5px", borderRadius: 3,
              background: "#1e3a5c33", color: "#7dd3fc",
            }}>{t}</span>
          ))}
        </div>
      )}
      <div style={{ marginTop: 6, display: "flex", gap: 6,
                     justifyContent: "flex-end" }}>
        <button data-testid={`agent-memory-open-${rec.id}`}
                onClick={() => onOpen(rec)} style={{
          fontSize: 10, padding: "3px 8px", background: "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: "pointer", color: "#7dd3fc",
        }}>상세</button>
        <button data-testid={`agent-memory-archive-${rec.id}`}
                onClick={() => onArchive(rec)} style={{
          fontSize: 10, padding: "3px 8px", background: "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: "pointer", color: "#94a3b8",
        }}>{rec.archived ? "복원" : "보관"}</button>
      </div>
    </div>
  );
}


function _MemoryDetail({ rec, onClose }) {
  if (!rec) return null;
  return (
    <div data-testid="agent-memory-detail"
         style={{ marginTop: 8, padding: "10px 12px",
                   background: "#000a14", borderRadius: 4,
                   border: "1px solid #1e3a5c", fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     marginBottom: 6 }}>
        <span style={{ fontWeight: 700, color: "#e2e8f0", fontSize: 12 }}>
          {rec.title}
        </span>
        <button onClick={onClose} style={{
          fontSize: 9, padding: "1px 6px", background: "transparent",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: "pointer", color: "#94a3b8",
        }}>닫기</button>
      </div>
      <div style={{ color: "#94a3b8", fontSize: 10, marginBottom: 6 }}>
        ID #{rec.id} · {rec.memory_type} ·
        {rec.strategy && ` ${rec.strategy} ·`}
        {rec.symbol && ` ${rec.symbol} ·`}
        severity {rec.severity}
      </div>
      <div style={{ color: "#cbd5e1", whiteSpace: "pre-wrap",
                     lineHeight: 1.6, marginBottom: 6 }}>
        {rec.summary}
      </div>
      {rec.lessons && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>Lessons</div>
          <div style={{ color: "#22c55e", whiteSpace: "pre-wrap" }}>
            {rec.lessons}
          </div>
        </div>
      )}
      {rec.next_action && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ fontSize: 9, color: "#475569" }}>Next Action</div>
          <div style={{ color: "#7dd3fc", whiteSpace: "pre-wrap" }}>
            {rec.next_action}
          </div>
        </div>
      )}
    </div>
  );
}


function _AddMemoryForm({ onSaved }) {
  const [title, setTitle] = useState("");
  const [summary, setSummary] = useState("");
  const [strategy, setStrategy] = useState("");
  const [symbol, setSymbol] = useState("");
  const [tags, setTags] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (!title.trim() || !summary.trim()) {
      setError("title / summary 필수");
      return;
    }
    setSaving(true); setError("");
    try {
      const tagList = tags.split(",").map(t => t.trim()).filter(Boolean);
      const rec = await backendApi.memoryCreate({
        memory_type: "operator_note",
        source_kind: "operator",
        title, summary,
        strategy: strategy || null,
        symbol: symbol || null,
        tags: tagList,
      });
      setTitle(""); setSummary(""); setStrategy(""); setSymbol(""); setTags("");
      if (onSaved) onSaved(rec);
    } catch (e) {
      // backend가 secret을 차단하면 400 + structured detail 반환.
      const msg = e.message || "저장 실패";
      setError(msg);
    }
    setSaving(false);
  };

  return (
    <div data-testid="agent-memory-add-form"
         style={{ marginBottom: 8, padding: "8px 10px",
                   background: "#0c2035", borderRadius: 4,
                   border: "1px solid #1e3a5c33" }}>
      <div style={{ fontSize: 10, color: "#94a3b8", marginBottom: 6 }}>
        운영 메모 추가 (API key / Secret / 계좌번호 / 개인정보 입력 금지 — 저장이 차단됩니다)
      </div>
      <input
        data-testid="agent-memory-add-title"
        type="text" placeholder="제목 (필수)"
        value={title} onChange={(e) => setTitle(e.target.value)}
        style={{ width: "100%", padding: "4px 6px", fontSize: 11,
                  marginBottom: 4, background: "#000a14",
                  border: "1px solid #1e3a5c", borderRadius: 3,
                  color: "#cbd5e1" }}
      />
      <textarea
        data-testid="agent-memory-add-summary"
        placeholder="메모 본문 (필수)"
        value={summary} onChange={(e) => setSummary(e.target.value)}
        style={{ width: "100%", padding: "4px 6px", fontSize: 11,
                  marginBottom: 4, background: "#000a14",
                  border: "1px solid #1e3a5c", borderRadius: 3,
                  color: "#cbd5e1", minHeight: 60, resize: "vertical" }}
      />
      <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
        <input type="text" placeholder="strategy"
               value={strategy} onChange={(e) => setStrategy(e.target.value)}
               style={{ flex: 1, padding: "3px 6px", fontSize: 10,
                         background: "#000a14",
                         border: "1px solid #1e3a5c", borderRadius: 3,
                         color: "#cbd5e1" }} />
        <input type="text" placeholder="symbol"
               value={symbol} onChange={(e) => setSymbol(e.target.value)}
               style={{ flex: 1, padding: "3px 6px", fontSize: 10,
                         background: "#000a14",
                         border: "1px solid #1e3a5c", borderRadius: 3,
                         color: "#cbd5e1" }} />
        <input type="text" placeholder="tag1, tag2"
               value={tags} onChange={(e) => setTags(e.target.value)}
               style={{ flex: 1, padding: "3px 6px", fontSize: 10,
                         background: "#000a14",
                         border: "1px solid #1e3a5c", borderRadius: 3,
                         color: "#cbd5e1" }} />
      </div>
      {error && (
        <div data-testid="agent-memory-add-error"
             style={{ fontSize: 10, color: "#ef4444", marginBottom: 4 }}>
          {error}
        </div>
      )}
      <div style={{ textAlign: "right" }}>
        <button data-testid="agent-memory-add-submit"
                onClick={handleSave} disabled={saving}
                style={{
          fontSize: 10, padding: "3px 10px",
          background: saving ? "#1e3a5c33" : "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: saving ? "not-allowed" : "pointer",
          color: "#7dd3fc",
        }}>
          {saving ? "저장 중…" : "메모 추가"}
        </button>
      </div>
    </div>
  );
}


export function AgentMemoryCard({ compact = false }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [keyword, setKeyword] = useState("");
  const [memoryType, setMemoryType] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [selected, setSelected] = useState(null);

  const filters = useMemo(() => ({
    keyword:     keyword     || null,
    memory_type: memoryType  || null,
    strategy:    strategyFilter || null,
    symbol:      symbolFilter   || null,
    limit:       compact ? 3 : 30,
  }), [keyword, memoryType, strategyFilter, symbolFilter, compact]);

  const refresh = async (overrides) => {
    setLoading(true); setError("");
    try {
      const res = await backendApi.memorySearch({ ...filters, ...overrides });
      setItems(res?.items || []);
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
        const res = await backendApi.memorySearch(filters);
        if (!cancelled) setItems(res?.items || []);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(filters)]);

  const handleArchive = async (rec) => {
    try {
      await backendApi.memoryArchive(rec.id, !rec.archived);
      await refresh();
      if (selected && selected.id === rec.id) setSelected(null);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <Card data-testid="agent-memory-card">
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📚 Agent Memory</SectionLabel>
        <span data-testid="agent-memory-not-order-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          주문 신호 아님 · 과거 학습 기록
        </span>
      </div>

      {/* 검색 / 필터 — compact 모드에서는 검색만 */}
      <div style={{ marginBottom: 8, display: "flex", gap: 4,
                    flexWrap: "wrap" }}>
        <input
          data-testid="agent-memory-search-input"
          type="text" placeholder="검색 (제목 / 본문 / lessons)"
          value={keyword} onChange={(e) => setKeyword(e.target.value)}
          style={{ flex: 1, minWidth: 120, padding: "4px 8px", fontSize: 11,
                    background: "#0c2035",
                    border: "1px solid #1e3a5c", borderRadius: 3,
                    color: "#cbd5e1" }}
        />
        {!compact && (
          <>
            <select
              data-testid="agent-memory-type-filter"
              value={memoryType} onChange={(e) => setMemoryType(e.target.value)}
              style={{ fontSize: 10, padding: "3px 6px",
                        background: "#0c2035",
                        border: "1px solid #1e3a5c", borderRadius: 3,
                        color: "#cbd5e1" }}
            >
              {_MEMORY_TYPES.map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
            <input type="text" placeholder="strategy"
                   value={strategyFilter}
                   onChange={(e) => setStrategyFilter(e.target.value)}
                   style={{ width: 80, padding: "3px 6px", fontSize: 10,
                             background: "#0c2035",
                             border: "1px solid #1e3a5c", borderRadius: 3,
                             color: "#cbd5e1" }} />
            <input type="text" placeholder="symbol"
                   value={symbolFilter}
                   onChange={(e) => setSymbolFilter(e.target.value)}
                   style={{ width: 80, padding: "3px 6px", fontSize: 10,
                             background: "#0c2035",
                             border: "1px solid #1e3a5c", borderRadius: 3,
                             color: "#cbd5e1" }} />
          </>
        )}
      </div>

      {/* 운영 메모 추가 폼 — compact에서는 숨김 */}
      {!compact && <_AddMemoryForm onSaved={() => refresh()} />}

      {/* 안내 */}
      <div data-testid="agent-memory-notice"
           style={{ marginBottom: 6, fontSize: 9, color: "#64748b",
                     lineHeight: 1.5 }}>
        ※ 본 결과는 *주문 신호가 아닙니다*. 과거 사례 / 운영 학습 자료이며,
        BUY/SELL/HOLD 결정 / 자동 주문 / 승인 큐 등록에 사용되지 않습니다.
        직접 조치가 필요하면 운영자가 별도 PR / 결재 흐름을 따르세요.
      </div>

      {/* 결과 목록 */}
      {loading && items.length === 0 && (
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      )}
      {error && (
        <div data-testid="agent-memory-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          Agent Memory 데이터를 불러오지 못했습니다.
        </div>
      )}
      {!loading && items.length === 0 && !error && (
        <div data-testid="agent-memory-empty"
             style={{ fontSize: 11, color: "#94a3b8" }}>
          저장된 메모리가 없습니다.
        </div>
      )}
      {items.length > 0 && (
        <div data-testid="agent-memory-list">
          {items.map((rec) => (
            <_MemoryRow key={rec.id} rec={rec}
                         onOpen={setSelected}
                         onArchive={handleArchive} />
          ))}
        </div>
      )}

      {selected && (
        <_MemoryDetail rec={selected} onClose={() => setSelected(null)} />
      )}
    </Card>
  );
}
