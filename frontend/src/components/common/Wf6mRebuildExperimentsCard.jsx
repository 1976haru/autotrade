/**
 * WF-6M Rebuild Experiments Card — 재설계 실험 + 개선 verdict (read-only).
 *
 * 4상태: 데이터 없음 / 분석 중(loading) / 완료 / 실패(error).
 * 절대 invariant (테스트로 lock): 매수/매도/실전/적용/자동매매 시작 버튼 0개
 * (새로고침/복사만), input/textarea 0개, 한글 위험 경고 문구 노출.
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

export function Wf6mRebuildExperimentsCard({
  apiClient = backendApi,
  testId = "wf6m-rebuild-experiments-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.wf6mRebuildLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.wf6mRebuildLatest()) || null;
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
  const best = report?.best_config || {};
  const improved = Array.isArray(report?.most_improved_top10) ? report.most_improved_top10 : [];
  const stable = Array.isArray(report?.most_stable_top10) ? report.most_stable_top10 : [];
  const unresolved = Array.isArray(report?.unresolved_issues) ? report.unresolved_issues : [];
  const next = Array.isArray(report?.next_steps) ? report.next_steps : [];

  return (
    <div data-testid={testId}>
      <Card accentColor="#3b82f633">
        <SectionLabel>🛠️ WF-6M 재설계 실험 (Rebuild Experiments)</SectionLabel>

        <div data-testid="wf6mrb-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구/백테스트 결과이며 실전매매 권고가 아닙니다.</b> 어떤 조합도 자동 적용 /
          실전 전환되지 않으며, 수익을 보장하지 않습니다.
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="wf6mrb-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="wf6mrb-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="wf6mrb-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>
            ⏳ 재설계 실험 분석 중입니다…
          </div>
        ) : null}
        {state === "error" ? (
          <div data-testid="wf6mrb-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
            결과를 불러올 수 없습니다 (backend 연결 확인).
          </div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="wf6mrb-empty" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-3)",
            padding: "6px 10px", borderRadius: 6, background: "var(--c-bg-2)",
          }}>
            아직 재설계 실험 리포트가 없습니다. CLI <code>run_wf_6m_rebuild_experiments.py</code>
            실행 후 표시됩니다 (설치 오류 아님).
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="wf6mrb-verdict" style={{
              padding: "8px 10px", borderRadius: 6, marginBottom: 8,
              background: _V_BG[verdict] || "#f1f5f9", border: "1px solid var(--c-border)",
            }}>
              <div style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)",
                            color: _V_COLOR[verdict] || "var(--c-text)" }}>
                최종 개선 verdict: <span data-testid="wf6mrb-final-verdict">{verdict}</span>
              </div>
              <div data-testid="wf6mrb-best" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)", marginTop: 2 }}>
                best: {best._label} · 수익 {best.total_return_pct ?? "—"}% · PF {best.profit_factor ?? "—"} ·
                MDD {best.max_drawdown_pct ?? "—"}% · 거래 {best.trade_count ?? 0}
                {best.low_confidence ? " · ⚠️LOW_CONFIDENCE" : ""}
                {" · 실전 권고 아님"}
              </div>
            </div>

            <div data-testid="wf6mrb-improved" style={{ marginBottom: 6 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>가장 개선된 조합</div>
              {improved.slice(0, 5).map((e) => (
                <div key={e._label} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {e._label}: {e.total_return_pct}% · PF {e.profit_factor} · MDD {e.max_drawdown_pct}% ·
                  {" "}{e.verdict}{e.low_confidence ? " (LOW)" : ""}
                </div>
              ))}
            </div>

            <div data-testid="wf6mrb-stable" style={{ marginBottom: 6 }}>
              <div style={{ fontSize: "var(--fs-xs)", fontWeight: "var(--fw-bold)" }}>가장 안정적인 조합</div>
              {stable.length === 0 ? (
                <div style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>(return≥0·PF≥1.05 충족 조합 없음)</div>
              ) : stable.slice(0, 5).map((e) => (
                <div key={e._label} style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
                  {e._label}: MDD {e.max_drawdown_pct}% · {e.total_return_pct}% · PF {e.profit_factor}
                </div>
              ))}
            </div>

            <div data-testid="wf6mrb-unresolved" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              미해결: {unresolved.join(" · ")}
            </div>
            <div data-testid="wf6mrb-next" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)" }}>
              다음 단계: {next.join(" · ")}
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default Wf6mRebuildExperimentsCard;
