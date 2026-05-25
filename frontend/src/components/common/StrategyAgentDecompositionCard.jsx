/**
 * StrategyAgentDecompositionCard — WF-6M 매매기법 vs Agent 분해 (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 핵심 표시: baseline vs Agent OFF, Agent 기능별 ranking, 매매기법 only 최고 조합,
 * 최종 verdict, EXE 재빌드 권고, Paper rehearsal candidate 여부.
 * invariant(테스트 lock): 실전/주문/적용/자동매매 시작 버튼 0개(새로고침/복사만),
 * input/textarea 0개, 한글 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _V_COLOR = {
  RESEARCH_PROMISING: "#166534", PAPER_REHEARSAL_CANDIDATE: "#0369a1",
  WATCHLIST_ONLY: "#a16207", STILL_NOT_RECOMMENDED: "#b91c1c", BLOCKED: "#b91c1c",
};
const _V_BG = {
  RESEARCH_PROMISING: "#dcfce7", PAPER_REHEARSAL_CANDIDATE: "#e0f2fe",
  WATCHLIST_ONLY: "#fef9c3", STILL_NOT_RECOMMENDED: "#fee2e2", BLOCKED: "#fee2e2",
};

export function StrategyAgentDecompositionCard({
  apiClient = backendApi,
  testId = "strategy-agent-decomposition-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.strategyAgentDecompositionLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.strategyAgentDecompositionLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "empty");
    } catch (e) {
      setReport(null);
      setState("error");
    }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(JSON.stringify(report || {}, null, 2));
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* noop */ }
  }, [report]);

  const verdict = report?.final_verdict || null;
  const b = report?.baseline || {};
  const offc = report?.agent_off_comparison || {};
  const roles = Array.isArray(offc.by_role) ? offc.by_role : [];
  const strat = Array.isArray(report?.strategy_only) ? report.strategy_only : [];
  const bestStrat = strat.reduce((a, c) => (
    (c.total_return_pct ?? -1e9) > (a?.total_return_pct ?? -1e9) ? c : a), null);
  const topReturn = Array.isArray(report?.top_by_return) ? report.top_by_return : [];
  const conclusions = Array.isArray(report?.conclusions) ? report.conclusions : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#14b8a633">
        <SectionLabel>🧩 WF-6M 매매기법 vs Agent 분해 (Decomposition)</SectionLabel>

        <div data-testid="sad-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 자동 적용 / 실전 전환 /
          EXE 빌드 0건. 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="sad-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="sad-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="sad-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 매매기법/Agent 분해 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="sad-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="sad-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 분해 리포트가 없습니다. CLI <code>run_wf_6m_strategy_agent_decomposition.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="sad-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                최종 개선 verdict: <span data-testid="sad-final-verdict">{verdict}</span>
              </div>
              <div data-testid="sad-paper" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                Paper 리허설 후보: {report.paper_rehearsal_candidate ? "예(조건부)" : "아니오"}
                {" · 실전매매 권고 아님"}
              </div>
              <div data-testid="sad-exe-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 2 }}>
                EXE 재빌드 권고: <b>{report.exe_rebuild_recommendation}</b>
              </div>
            </div>

            <div data-testid="sad-baseline" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              Baseline: {b.total_return_pct ?? "—"}% · PF {b.profit_factor ?? "—"} · MDD {b.max_drawdown_pct ?? "—"}%
              {" / Agent OFF: "}<span data-testid="sad-agent-off">{offc.agent_off_return_pct ?? "—"}%</span>
            </div>

            {bestStrat ? (
              <div data-testid="sad-best-strat" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                매매기법 only 최고: {bestStrat._label} {bestStrat.total_return_pct}% (PF {bestStrat.profit_factor})
              </div>
            ) : null}

            <div data-testid="sad-roles" style={{ marginBottom: 4 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>Agent 기능별 (OFF 대비)</div>
              {roles.map((x) => (
                <div key={x.role} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {x.role}: {x.return_pct}% · MDD {x.mdd}% · vs OFF {x.vs_off_return_pp}pp
                </div>
              ))}
            </div>

            {topReturn.length > 0 ? (
              <div data-testid="sad-top" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
                최고 조합: {topReturn[0]._label} {topReturn[0].total_return_pct}% · PF {topReturn[0].profit_factor} ·
                MDD {topReturn[0].max_drawdown_pct}%{topReturn[0].low_confidence ? " (LOW_CONFIDENCE)" : ""}
              </div>
            ) : null}

            <div data-testid="sad-conclusions" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginTop: 6 }}>
              결론: {conclusions.join(" / ")}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default StrategyAgentDecompositionCard;
