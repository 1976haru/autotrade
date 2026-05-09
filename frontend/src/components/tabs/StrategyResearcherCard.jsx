import { useEffect, useState } from "react";
import { Card, SectionLabel } from "../common";
import { backendApi } from "../../services/backend/client";

// 55: Strategy Researcher card — 백테스트 결과 기반 advisory.
//
// **자동 반영 안 됨 / PR 검토 필요**. BUY/SELL/HOLD 표시 X. 매수/매도 버튼 X.
// 본 카드의 어떤 버튼도 strategy 코드 / 파라미터를 자동으로 적용하지 *않는다*.
// "Backtest 다시 실행" 같은 후속 행동만 표시 가능 — 코드 수정 / 파라미터 자동
// 저장 버튼 절대 금지.

const _LEVEL_PALETTE = {
  HEALTHY:  { color: "#22c55e", label: "정상" },
  CAUTION:  { color: "#fbbf24", label: "경계" },
  WARNING:  { color: "#fb923c", label: "경고" },
  CRITICAL: { color: "#ef4444", label: "심각" },
};

const _SEVERITY_COLOR = _LEVEL_PALETTE;


function _Field({ label, value }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between",
                   padding: "5px 0", borderBottom: "1px solid #1e3a5c33",
                   fontSize: 11 }}>
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span style={{ color: "#e2e8f0", fontWeight: 700 }}>{value}</span>
    </div>
  );
}


function _FindingRow({ finding }) {
  const palette = _SEVERITY_COLOR[finding.severity] || _SEVERITY_COLOR.CAUTION;
  return (
    <div data-testid={`strategy-researcher-finding-${finding.code}`}
         style={{ padding: "6px 8px", marginBottom: 4,
                   background: "#0c2035", borderRadius: 3,
                   borderLeft: `3px solid ${palette.color}`,
                   fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     marginBottom: 2 }}>
        <span style={{ fontWeight: 700, color: "#e2e8f0",
                        fontFamily: "monospace", fontSize: 10 }}>
          {finding.code}
        </span>
        <span style={{ fontSize: 9, fontWeight: 700, color: palette.color }}>
          {finding.severity}
        </span>
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.5 }}>
        {finding.summary}
      </div>
    </div>
  );
}


function _SuggestionRow({ suggestion, idx }) {
  const palette = _SEVERITY_COLOR[suggestion.severity] || _SEVERITY_COLOR.CAUTION;
  return (
    <div data-testid={`strategy-researcher-suggestion-${idx}`}
         style={{ padding: "8px 10px", marginBottom: 6,
                   background: "#0c2035", borderRadius: 3,
                   border: `1px solid ${palette.color}55`,
                   fontSize: 11 }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     marginBottom: 4, alignItems: "baseline" }}>
        <span style={{ fontWeight: 700, color: "#e2e8f0" }}>
          {suggestion.title}
        </span>
        <span style={{ fontSize: 9, fontFamily: "monospace",
                        color: palette.color }}>
          {suggestion.category}
        </span>
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.5, marginBottom: 3 }}>
        <strong>Why:</strong> {suggestion.rationale}
      </div>
      <div style={{ color: "#94a3b8", lineHeight: 1.5, marginBottom: 3 }}>
        <strong>제안:</strong> {suggestion.proposed_change}
      </div>
      {suggestion.required_validation
        && suggestion.required_validation.length > 0 && (
        <div style={{ marginTop: 4, fontSize: 10 }}>
          <div style={{ color: "#64748b", marginBottom: 2 }}>
            Required validation (운영자가 *수동* 실행):
          </div>
          <ul style={{ margin: 0, paddingLeft: 16, color: "#7dd3fc" }}>
            {suggestion.required_validation.map((v, i) => (
              <li key={i}>{v}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}


export function StrategyResearcherCard({
  report, loading, error, onRefresh, onRerunBacktest,
}) {
  const [showMarkdown, setShowMarkdown] = useState(false);

  if (loading && !report) {
    return (
      <Card>
        <SectionLabel>📊 전략 연구</SectionLabel>
        <div style={{ fontSize: 11, color: "#475569" }}>로딩 중…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card>
        <SectionLabel>📊 전략 연구</SectionLabel>
        <div data-testid="strategy-researcher-error"
             style={{ fontSize: 11, color: "#94a3b8", lineHeight: 1.6 }}>
          전략 연구 데이터를 아직 불러오지 못했습니다.
          {onRefresh && (
            <div style={{ marginTop: 8 }}>
              <button onClick={onRefresh} style={{
                fontSize: 10, padding: "3px 8px", background: "#0c2035",
                border: "1px solid #1e3a5c", borderRadius: 3,
                cursor: "pointer", color: "#7dd3fc",
              }}>↻ 다시 시도</button>
            </div>
          )}
        </div>
      </Card>
    );
  }
  if (!report) return null;

  const palette = _LEVEL_PALETTE[report.audit_level] || _LEVEL_PALETTE.HEALTHY;
  const findings = report.findings || [];
  const suggestions = report.suggestions || [];
  const nextTests = report.required_next_tests || [];

  return (
    <Card data-testid="strategy-researcher-card"
          accentColor={`${palette.color}55`}>
      <div style={{ display: "flex", justifyContent: "space-between",
                    alignItems: "center", marginBottom: 6 }}>
        <SectionLabel>📊 전략 연구</SectionLabel>
        <span data-testid="strategy-researcher-not-auto-apply-badge" style={{
          fontSize: 9, fontWeight: 700, color: "#94a3b8",
          padding: "1px 6px", borderRadius: 3,
          border: "1px solid #94a3b855", background: "#94a3b815",
        }}>
          자동 반영 안 됨 · PR 검토 필요
        </span>
      </div>

      {/* 운영자 요약 */}
      <div style={{ marginBottom: 8, padding: "8px 10px",
                     background: "#0c2035",
                     border: `1px solid ${palette.color}33`,
                     borderRadius: 4 }}>
        {(report.summary_lines || []).map((line, i) => (
          <div key={i} data-testid={`strategy-researcher-line-${i}`}
               style={{ fontSize: 11, color: "#e2e8f0",
                         lineHeight: 1.6 }}>
            {line}
          </div>
        ))}
      </div>

      {/* 분석 대상 */}
      <_Field label="전략" value={
        <span data-testid="strategy-researcher-strategy"
              style={{ fontFamily: "monospace" }}>
          {report.strategy}
        </span>
      } />
      <_Field label="BacktestRun ID" value={`#${report.run_id}`} />
      <_Field label="감사 단계" value={
        <span data-testid="strategy-researcher-level"
              style={{ color: palette.color }}>
          {palette.label} ({report.audit_level})
        </span>
      } />
      <_Field label="감지 findings" value={`${findings.length}건`} />
      <_Field label="개선 제안" value={`${suggestions.length}건`} />

      {/* Findings */}
      {findings.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: "#475569",
                         marginBottom: 4 }}>핵심 findings</div>
          <div data-testid="strategy-researcher-findings">
            {findings.slice(0, 5).map((f, i) => (
              <_FindingRow key={`${f.code}-${i}`} finding={f} />
            ))}
          </div>
        </div>
      )}

      {/* Suggestions */}
      {suggestions.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 10, color: "#475569",
                         marginBottom: 4 }}>개선 제안 (advisory)</div>
          <div data-testid="strategy-researcher-suggestions">
            {suggestions.slice(0, 5).map((s, i) => (
              <_SuggestionRow key={i} suggestion={s} idx={i} />
            ))}
          </div>
        </div>
      )}

      {/* Required next tests */}
      {nextTests.length > 0 && (
        <div style={{ marginTop: 8, padding: "8px 10px",
                       background: "#0c2035",
                       border: "1px solid #fbbf2455",
                       borderRadius: 4, fontSize: 11,
                       color: "#fbbf24", lineHeight: 1.6 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            ☑ Required Next Tests (반드시 *수동* 실행)
          </div>
          <ul data-testid="strategy-researcher-next-tests"
              style={{ margin: 0, paddingLeft: 16, color: "#cbd5e1",
                        fontSize: 10 }}>
            {nextTests.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Markdown preview toggle */}
      <div style={{ marginTop: 8, textAlign: "right" }}>
        <button onClick={() => setShowMarkdown(v => !v)} style={{
          fontSize: 10, padding: "3px 8px", background: "#0c2035",
          border: "1px solid #1e3a5c", borderRadius: 3,
          cursor: "pointer", color: "#7dd3fc",
        }}>
          {showMarkdown ? "▲ markdown 숨기기" : "▼ markdown 미리보기"}
        </button>
      </div>
      {showMarkdown && (
        <pre data-testid="strategy-researcher-markdown"
             style={{ marginTop: 6, padding: "8px",
                       background: "#0c2035", border: "1px solid #1e3a5c",
                       borderRadius: 3, fontSize: 10,
                       color: "#cbd5e1",
                       maxHeight: 300, overflow: "auto",
                       whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
          {report.markdown_report}
        </pre>
      )}

      {/* Disclaimer */}
      <div style={{ marginTop: 8, fontSize: 9, color: "#64748b",
                     lineHeight: 1.5 }}>
        ※ 본 리포트는 *주문 신호가 아니며*, 어떤 제안도 *자동으로 코드 / 파라
        미터에 반영되지 않습니다*. 운영자 검토 → 별도 PR → 별도 백테스트 →
        walk-forward → paper / shadow → live 절차 필수.
      </div>

      {/* 후속 행동 — Backtest 다시 실행만, 자동 적용 버튼은 절대 X */}
      <div style={{ marginTop: 8, display: "flex", gap: 6,
                     justifyContent: "flex-end" }}>
        {onRerunBacktest && (
          <button data-testid="strategy-researcher-rerun-backtest"
                  onClick={onRerunBacktest} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ Backtest 다시 실행</button>
        )}
        {onRefresh && (
          <button onClick={onRefresh} style={{
            fontSize: 10, padding: "3px 8px", background: "#0c2035",
            border: "1px solid #1e3a5c", borderRadius: 3,
            cursor: "pointer", color: "#7dd3fc",
          }}>↻ 새로고침</button>
        )}
      </div>
    </Card>
  );
}


// 55: hook for /api/agents/strategy-researcher/report/{run_id}.
export function useStrategyResearcherReport(runId) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    if (!runId) return;
    setLoading(true); setError("");
    try {
      const data = await backendApi.strategyResearcherReport(runId);
      setReport(data);
    } catch (e) {
      setError(e.message);
    }
    setLoading(false);
  };

  useEffect(() => {
    if (!runId) { setReport(null); return; }
    let cancelled = false;
    (async () => {
      setLoading(true); setError("");
      try {
        const data = await backendApi.strategyResearcherReport(runId);
        if (!cancelled) setReport(data);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  return { report, loading, error, refresh };
}


// 55: hook for /api/agents/strategy-researcher/recent.
export function useStrategyResearcherRecent({ limit = 20, strategy = null } = {}) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = async () => {
    setLoading(true); setError("");
    try {
      const data = await backendApi.strategyResearcherRecent({ limit, strategy });
      setItems(data?.items || []);
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
        const data = await backendApi.strategyResearcherRecent({ limit, strategy });
        if (!cancelled) setItems(data?.items || []);
      } catch (e) {
        if (!cancelled) setError(e.message);
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [limit, strategy]);

  return { items, loading, error, refresh };
}
