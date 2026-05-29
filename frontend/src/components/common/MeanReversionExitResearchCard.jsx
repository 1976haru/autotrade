/**
 * MeanReversionExitResearchCard — 평균회귀 exit 구조 연구 (CHECKLIST-05, read-only 연구용).
 *
 * 표시: exit mismatch 근거, entry별 best exit PF/MDD, OOS, 비용 stress, 살아남은 조합,
 * verdict, research_only/auto_apply(false). 버튼은 새로고침·복사만.
 * invariant(테스트 lock): 적용/실전/주문/자동매매 버튼 0개, input 0개, 경고 문구.
 */

import { useCallback, useEffect, useState } from "react";

import { Card, SectionLabel } from "./index";
import { backendApi } from "../../services/backend/client";

const _pf = (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3));

export function MeanReversionExitResearchCard({
  apiClient = backendApi,
  testId = "mean-reversion-exit-card",
} = {}) {
  const [report, setReport] = useState(null);
  const [state, setState] = useState("loading");
  const [copied, setCopied] = useState(false);

  const refresh = useCallback(async () => {
    if (typeof apiClient.meanReversionExitLatest !== "function") { setState("empty"); return; }
    setState("loading");
    try {
      const r = (await apiClient.meanReversionExitLatest()) || null;
      setReport(r);
      setState(r && r.available !== false ? "complete" : "notready");
    } catch { setReport(null); setState("error"); }
  }, [apiClient]);

  useEffect(() => { refresh(); }, [refresh]);

  const copyReport = useCallback(() => {
    try {
      if (navigator?.clipboard?.writeText) {
        navigator.clipboard.writeText(JSON.stringify(report || {}, null, 2));
        setCopied(true); setTimeout(() => setCopied(false), 1500);
      }
    } catch { /* noop */ }
  }, [report]);

  const best = report?.best_exit_per_entry || {};

  return (
    <div data-testid={testId}>
      <Card accentColor="#14b8a633">
        <SectionLabel>🎯 평균회귀 exit 구조 연구 (CHECKLIST-05, 연구용)</SectionLabel>

        <div data-testid="mre-warning" style={{
          fontSize: "var(--fs-xs)", color: "#7f1d1d", marginBottom: 8,
          padding: "5px 9px", borderRadius: 6, background: "#fef2f2", border: "1px solid #fecaca",
        }}>
          ⚠️ <b>연구용 백테스트이며 실전매매 권고가 아닙니다.</b> 결과가 좋아도 paper rehearsal
          후보일 뿐이며, entry/exit 구조는 런타임에 <b>등록/자동 적용되지 않습니다</b>
          (research_only · auto_apply=false).
        </div>

        <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
          <button type="button" data-testid="mre-refresh" onClick={refresh}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>결과 새로고침</button>
          <button type="button" data-testid="mre-copy" onClick={copyReport}
                  style={{ fontSize: "var(--fs-xs)", padding: "3px 10px", borderRadius: 6,
                           border: "1px solid var(--c-border)", background: "var(--c-bg-2)", cursor: "pointer" }}>{copied ? "복사됨" : "리포트 복사"}</button>
        </div>

        {state === "loading" ? <div data-testid="mre-loading" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>⏳ 불러오는 중…</div> : null}
        {state === "error" ? <div data-testid="mre-error" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>결과를 불러올 수 없습니다.</div> : null}
        {state === "empty" ? <div data-testid="mre-empty" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-3)" }}>API client 미연결.</div> : null}
        {state === "notready" ? (
          <div data-testid="mre-notready" style={{
            fontSize: "var(--fs-xs)", color: "var(--c-text-2)", padding: "6px 10px",
            borderRadius: 6, background: "#fef9c3", border: "1px solid var(--c-border)",
          }}>
            verdict: <b>{report?.verdict || "BACKTEST_INFRA_INCOMPLETE"}</b> · 리포트 없음 —
            <code> run_mean_reversion_exit.py --write-latest </code> 실행 후 표시.
          </div>
        ) : null}

        {state === "complete" ? (
          <>
            <div data-testid="mre-verdict" style={{ fontSize: "var(--fs-sm)", fontWeight: "var(--fw-bold)", marginBottom: 4 }}>
              verdict: {report.verdict}
            </div>
            <div data-testid="mre-conclusion" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text-2)", marginBottom: 6 }}>
              {(report.conclusion || []).join(" / ")}
            </div>

            <div data-testid="mre-best" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 6 }}>
              <b>entry별 best exit (net PF / OOS / verdict):</b>
              {Object.entries(best).map(([e, b]) => (
                <div key={e}>{e} → {b.exit}: PF {_pf(b.net_pf)} · OOS {_pf(b.oos_pf)} · <b>{b.verdict}</b></div>
              ))}
            </div>

            <div data-testid="mre-survivors" style={{ fontSize: "var(--fs-xs)", color: "var(--c-text)", marginBottom: 4 }}>
              살아남은 조합: <b>{(report.survivors || []).length ? report.survivors.join(", ") : "없음"}</b> ·
              COST_FRAGILE: {(report.fragile || []).length ? report.fragile.join(", ") : "없음"}
            </div>
            <div data-testid="mre-auto" style={{ fontSize: "var(--fs-xs)", color: "#7f1d1d" }}>
              research_only <b>{String(report.is_research_only)}</b> · 자동 적용 <b>{String(report.auto_apply_allowed)}</b> · 런타임 반영 <b>{String(report.applied_to_runtime)}</b>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}

export default MeanReversionExitResearchCard;
