/**
 * RankingRedesignCard — composite ranking 후보 비교 결과 (read-only 연구용).
 *
 * 표시: earliest_first / current_composite / best_candidate / OOS verdict / 추천 /
 * look-ahead 경고 / 자동 적용 여부(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 주문/실전/적용/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

export function RankingRedesignCard({
  apiClient = backendApi,
  testId = "ranking-redesign-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.rankingRedesignLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.rankingRedesignLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "notready");
    } catch {
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

  const oos = report?.oos || {};
  const ef = oos.EARLIEST_FIRST || {};
  const cc = oos.CURRENT_COMPOSITE || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#8b5cf633">
        <SectionLabel>🔬 Composite Ranking 재설계 비교 (연구용 백테스트)</SectionLabel>

        <div data-testid="rr-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ 이 결과는 <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 어떤 ranking 후보도
          런타임/전략에 <b>자동 적용되지 않습니다</b> (auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="rr-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="rr-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)",
                           cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? (
          <div data-testid="rr-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div>
        ) : null}
        {state === "error" ? (
          <div data-testid="rr-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div>
        ) : null}
        {state === "empty" ? (
          <div data-testid="rr-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div>
        ) : null}
        {state === "notready" ? (
          <div data-testid="rr-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "RANKING_REJECTED"}</b> · 아직 리포트 없음 —
            <code> run_ranking_redesign.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="rr-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict} · best: {report.best_candidate?.name}
            </div>
            <div data-testid="rr-baseline" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              <div>earliest_first (OOS): PF {ef.profit_factor ?? "—"} / MDD {ef.mdd_pct ?? "—"}% / exp {ef.expectancy ?? "—"}bps</div>
              <div>current_composite (OOS): PF {cc.profit_factor ?? "—"} / MDD {cc.mdd_pct ?? "—"}% / exp {cc.expectancy ?? "—"}bps (실패 기준)</div>
              <div><b>best {report.best_candidate?.name}: PF {report.best_candidate?.profit_factor ?? "—"} / MDD {report.best_candidate?.mdd_pct ?? "—"}%</b></div>
            </div>
            <div data-testid="rr-lookahead" style={{ fontSize: "var(--fs-xs)", color: "#a16207", marginBottom: 4 }}>
              look-ahead 제외 후보: {(report.look_ahead_candidates || []).join(", ")}
            </div>
            <div data-testid="rr-rec" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 4 }}>
              추천: {report.recommendation}
            </div>
            <div data-testid="rr-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default RankingRedesignCard;
